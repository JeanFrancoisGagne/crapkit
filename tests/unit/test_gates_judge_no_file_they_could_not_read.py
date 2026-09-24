"""A changed file no reader could read fails every gate that was asked to judge it.

Since 0.7.2 a file lizard or one of crapkit's readers refuses is scored as zero
functions, so one refusal no longer ends a whole run. Every gate then read those
zero records as nothing over the ceiling: a ccn-8 function in the same file as one
TypeScript arrow the reader refuses (`convert<string, number>(x)` in an
expression-arrow body) passed the commit hook, `rescore --gate`, the MCP tool
`check_gate` (which runs `rescore --gate --json`) and verify, and the only sign was
a stderr note. A gate cannot pass what it never read: each one now exits 6 and
names the file and the reader's reason.

Every test here drives `main`, the entry point `python -m crapkit` uses, over a
real git repo.
"""
from __future__ import annotations

import json

from cli_inproc_repo import (KNOTTY, add_knotty, commit_all, git, repo,  # noqa: F401
                             seed_artifacts, template_repo)

import pytest

from crapkit.cli import main

ARROW = "\nexport const pick = (x: number) => convert<string, number>(x);\n"
REASON = "expression-arrow body has '<' before a comma"
ADVICE = "change what the reason names so a reader can parse the file"


def run(argv: list[str], repo, capsys) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture()
def scored(repo, capsys):
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def refuse_app(repo, *, knotty: bool = True) -> None:
    """src/app.ts gains knotty (ccn 8, ceiling 6) and an arrow no reader parses."""
    if knotty:
        add_knotty(repo)
    with open(repo / "src" / "app.ts", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(ARROW)


def test_the_commit_hook_refuses_a_staged_file_it_could_not_read(repo, capsys):
    refuse_app(repo)
    git(repo, "add", "src/app.ts")

    code, out, err = run(["hook-precommit"], repo, capsys)

    assert code == 6, out + err
    assert "crapkit gate: 1 staged file(s) could not be read, so no function in them was judged:" \
        in out, out
    assert "  UNREAD  src/app.ts: " in out and REASON in out, out
    assert ADVICE in out, out


def test_the_commit_hook_refuses_an_unread_file_even_with_nothing_over_the_ceiling(repo, capsys):
    refuse_app(repo, knotty=False)
    git(repo, "add", "src/app.ts")

    code, out, _ = run(["hook-precommit"], repo, capsys)

    assert code == 6, out
    assert "UNREAD  src/app.ts" in out, out


def test_the_env_override_grants_no_file_it_could_not_read(repo, capsys, monkeypatch):
    """The override records debt per function, and an unread file has none."""
    monkeypatch.setenv("CRAPKIT_OVERRIDE_REASON", "shipping it")
    refuse_app(repo, knotty=False)
    git(repo, "add", "src/app.ts")

    code, out, _ = run(["hook-precommit"], repo, capsys)

    assert code == 6, out
    assert "UNREAD  src/app.ts" in out, out


@pytest.mark.parametrize("where", ["tracked", "untracked"])
def test_rescore_gate_refuses_a_changed_file_it_could_not_read(scored, capsys, where):
    path = "src/app.ts" if where == "tracked" else "src/a.ts"
    if where == "tracked":
        refuse_app(scored)
    else:
        (scored / path).write_text(KNOTTY + ARROW, encoding="utf-8", newline="\n")

    code, out, err = run(["rescore", path, "--gate"], scored, capsys)

    assert code == 6, out + err
    assert "crapkit gate: 1 changed file(s) could not be read, so no function in them was judged:" \
        in err, err
    assert f"  UNREAD  {path}: " in err and REASON in err, err
    assert "0 over ceiling" not in out, out


def test_check_gate_reports_the_unread_file_and_not_ok(scored, capsys):
    """The MCP tool runs `rescore PATH --gate --json` and an agent reads gate.ok."""
    refuse_app(scored)

    code, out, _ = run(["rescore", "src/app.ts", "--gate", "--json"], scored, capsys)

    gate = json.loads(out)["gate"]
    assert code == 6
    assert gate["ok"] is False
    assert [u["path"] for u in gate["unread"]] == ["src/app.ts"]
    assert REASON in gate["unread"][0]["reason"]


def test_rescore_gate_passes_an_unread_file_nothing_changed(scored, capsys):
    """The gate judges what a change touched. An unread file with no change
    against HEAD holds no changed function to judge."""
    refuse_app(scored, knotty=False)
    commit_all(scored, "the arrow")

    code, out, err = run(["rescore", "src/app.ts", "--gate", "--json"], scored, capsys)

    assert code == 0, out + err
    assert json.loads(out)["gate"]["unread"] == []


def test_verify_fails_a_changed_file_it_could_not_read(scored, capsys):
    refuse_app(scored)
    commit_all(scored, "knotty and the arrow")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], scored, capsys)

    payload = json.loads(out)
    assert code == 6, out + err
    assert payload["ok"] is False
    assert [(u["path"], u["dirty"]) for u in payload["unread_files"]] == [("src/app.ts", False)]
    assert REASON in payload["unread_files"][0]["reason"]


def test_verify_prints_the_unread_file_as_a_finding(scored, capsys):
    refuse_app(scored)

    code, out, _ = run(["verify", "--reuse-artifacts"], scored, capsys)

    assert code == 6
    assert "  UNREAD  src/app.ts: " in out and REASON in out and "[dirty]" in out, out
    assert "findings: 0 committed / 1 dirty" in out, out


def test_verify_passes_an_unread_file_outside_the_change(scored, capsys):
    refuse_app(scored, knotty=False)
    commit_all(scored, "the arrow")
    assert main(["coverage", "--reuse-artifacts", "--repo", str(scored)]) == 0
    capsys.readouterr()
    (scored / "web" / "ui.ts").write_text("export const n = 1;\n", encoding="utf-8")
    commit_all(scored, "an unrelated change")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], scored, capsys)

    assert code == 0, out + err
    assert json.loads(out)["unread_files"] == []
