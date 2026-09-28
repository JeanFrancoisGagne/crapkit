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
MARKS = "crapkit-ratchet.tsv"


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
    assert "override refused: 1 unread file (src/app.ts: " in out, out


def unread_beside_knotty(repo) -> None:
    """src/a.ts holds only an arrow no reader parses; src/app.ts gains knotty,
    a function the override could grant if nothing else stood in its way."""
    (repo / "src" / "a.ts").write_text(ARROW, encoding="utf-8", newline="\n")
    add_knotty(repo)


def test_the_env_override_refuses_before_any_side_effect_when_a_staged_file_went_unread(
        repo, capsys, monkeypatch):
    """The grant wrote and staged the marks file, raised the alert and stored a
    hook run, then the hook exited 6 over the unread file anyway: the commit was
    refused and the debt was signed. The refusal now comes first."""
    monkeypatch.setenv("CRAPKIT_OVERRIDE_REASON", "shipping it")
    unread_beside_knotty(repo)
    git(repo, "add", "src/a.ts", "src/app.ts")

    code, out, err = run(["hook-precommit"], repo, capsys)

    assert code == 6, out + err
    assert "override granted" not in out, out
    assert "override refused: 1 unread file (src/a.ts: " in out and REASON in out, out
    assert ADVICE in out, out
    assert not (repo / MARKS).exists()
    assert git(repo, "diff", "--cached", "--name-only").split() == ["src/a.ts", "src/app.ts"]
    assert not (repo / "alerts.log").exists()
    assert not (repo / ".crapkit" / "crap.sqlite").exists()


def test_verify_override_refuses_before_it_grants_when_a_changed_file_went_unread(scored, capsys):
    """`verify --override` granted the gate violation beside the unread file,
    wrote its mark and printed `1 mark granted`, then exited 6 with no reason
    given. An unread file never qualifies, so nothing is granted and the
    refusal names it."""
    unread_beside_knotty(scored)
    commit_all(scored, "an unread file beside a knotty function")

    code, out, err = run(["verify", "--reuse-artifacts", "--override", "shipping it", "--json"],
                         scored, capsys)

    payload = json.loads(out)
    assert code == 6, out + err
    assert payload["overridden"] == []
    assert [g["long_name"] for g in payload["gate_violations"]] == ["knotty ( n )"]
    assert "override refused: 1 unread file (src/a.ts: " in err and REASON in err, err
    assert ADVICE in err, err
    assert not (scored / MARKS).exists()
    assert not (scored / "alerts.log").exists()


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
    assert [(u["path"], u["dirty"]) for u in gate["unread_files"]] == [("src/app.ts", True)]
    assert REASON in gate["unread_files"][0]["reason"]


def test_rescore_gate_passes_an_unread_file_nothing_changed(scored, capsys):
    """The gate judges what a change touched. An unread file with no change
    against HEAD holds no changed function to judge."""
    refuse_app(scored, knotty=False)
    commit_all(scored, "the arrow")

    code, out, err = run(["rescore", "src/app.ts", "--gate", "--json"], scored, capsys)

    assert code == 0, out + err
    assert json.loads(out)["gate"]["unread_files"] == []


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


def test_the_pages_quote_the_override_refusal_an_unread_file_prints():
    """The CHANGELOG quotes the refusal with PATH and REASON for the file and its
    reason; the line the code builds for those two words must match it, and the
    ratchet page must name an unread file among the causes no override grants."""
    from pathlib import Path

    from crapkit.cli.verifying import _hook_override_refusal

    line = _hook_override_refusal({"PATH": "REASON"})
    quoted = line.split(";")[0]
    root = Path(__file__).resolve().parents[2]
    assert quoted == "override refused: 1 unread file (PATH: REASON) never qualifies for an override"
    assert f"`{quoted}`" in " ".join((root / "CHANGELOG.md").read_text(encoding="utf-8").split())
    ratchet = " ".join((root / "docs" / "ratchet.md").read_text(encoding="utf-8").split())
    assert "a new test failure or an unread file in the same run refuses it" in ratchet


# --- the user meets an unread file before the commit gate refuses it ----------

def test_the_coverage_line_says_the_commit_gate_refuses_an_unread_file(repo, capsys):
    """Every file on the could-not-be-tokenized list is refused the next time
    someone stages it; the run says so where it names the file."""
    refuse_app(repo, knotty=False)
    seed_artifacts(repo)

    code, _, err = run(["coverage", "--reuse-artifacts"], repo, capsys)

    assert code == 0, err
    assert ("crapkit: 1 file(s) could not be tokenized; each is scored as zero functions and "
            "stays unranked, and the commit gate refuses these files when staged:") in err, err


def doctor_warnings(repo, capsys) -> list[str]:
    code, out, err = run(["doctor", "--json"], repo, capsys)
    assert code in (0, 1), out + err
    return [w for w in json.loads(out)["warnings"] if "could not be read" in w]


def measure(repo, capsys) -> None:
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()


def test_doctor_warns_about_each_file_the_newest_run_could_not_read(scored, capsys):
    """doctor names the file, the reader's reason and what the commit gate will
    do, so the first refused commit is not where the user learns of it. A file
    that holds no function is not one of them."""
    refuse_app(scored, knotty=False)
    (scored / "web" / "consts.ts").write_text("export const n = 1;\n", encoding="utf-8")
    commit_all(scored, "the arrow and a file with no function")
    measure(scored, capsys)

    unread = doctor_warnings(scored, capsys)

    assert len(unread) == 1, unread
    assert unread[0].startswith("src/app.ts could not be read, so the commit gate refuses it "
                                "when staged: "), unread
    assert REASON in unread[0] and ADVICE in unread[0], unread


def test_doctor_drops_a_file_fixed_since_the_run(scored, capsys):
    """doctor reads the files the newest run scored no function in, and reads
    each again: one a reader parses now is no longer named."""
    refuse_app(scored, knotty=False)
    commit_all(scored, "the arrow")
    measure(scored, capsys)
    text = (scored / "src" / "app.ts").read_text(encoding="utf-8")
    (scored / "src" / "app.ts").write_text(text.replace(ARROW, "\n"), encoding="utf-8")

    assert doctor_warnings(scored, capsys) == []


def test_doctor_is_quiet_about_files_every_reader_read(scored, capsys):
    (scored / "web" / "consts.ts").write_text("export const n = 1;\n", encoding="utf-8")
    commit_all(scored, "a file with no function")
    measure(scored, capsys)

    assert doctor_warnings(scored, capsys) == []


def test_hook_precommit_help_names_the_unread_refusal_and_its_fix(capsys):
    """`crapkit hook-precommit --help` is where a user looks after the hook
    refuses a commit; the parent listing's one line is not on that screen."""
    import pytest

    from crapkit.cli import main
    from crapkit.merge import UNREAD_ADVICE

    with pytest.raises(SystemExit) as stop:
        main(["hook-precommit", "--help"])

    assert stop.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "Exit 6 on a staged function over its ceiling or a staged file no reader could " \
           "read" in text, text
    assert UNREAD_ADVICE in text, text


def test_the_onboarding_pages_say_what_to_do_about_an_unread_file():
    """The user meets the refusal before the first refused commit.
    The upgrade guide says to run coverage and fix or exclude each file, the
    help text says the hook refuses such a file, and the recover skill routes
    an UNREAD line under exit 6."""
    from pathlib import Path

    from crapkit.cli.parser import build_parser

    root = Path(__file__).resolve().parents[2]

    def page(rel: str) -> str:
        return " ".join((root / rel).read_text(encoding="utf-8").split())

    upgrading = page("docs/upgrading.md")
    assert "**Files no reader could read.**" in upgrading
    assert "run `crapkit coverage` and read the files it names" in upgrading
    assert "under `[exclude]` globs in `crapkit.toml`" in upgrading
    recover = page("plugin/skills/crapkit-recover/SKILL.md")
    assert "an `UNREAD` line names a changed file no reader could read" in recover
    helps = [a for a in build_parser()._subparsers._group_actions[0]._choices_actions
             if a.dest == "hook-precommit"]
    assert "a staged file no reader could read" in helps[0].help
    assert "`crapkit doctor` WARNs about each one the newest run could not read" in page("README.md")
