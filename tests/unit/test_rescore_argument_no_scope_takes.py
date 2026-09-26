"""Q17 for a file argument whose name is not UTF-8.

`rescore --gate` refused any such name on disk at exit 3 without asking which
scope takes it, where hook-precommit leaves the same staged file out with one
warning line. A name no scope takes is now left out with one stderr line and
the gate judges 0; a name a scope takes keeps the rename refusal. The file is
written straight to disk: a POSIX name holding byte e9, on NTFS a lone
surrogate, so the rows run on every OS.
"""
import json

import pytest

from cli_inproc_repo import KNOTTY, add_knotty, commit_all, repo, seed_artifacts, template_repo  # noqa: F401

from crapkit.cli import main

GATES = pytest.mark.parametrize("gate", [[], ["--gate"]], ids=["rescore", "rescore-gate"])


@pytest.fixture()
def measured(repo, capsys):
    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def _rescore(repo, capsys, name: str, gate: list[str]) -> tuple[int, dict, str]:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(KNOTTY, encoding="utf-8")
    code = main(["rescore", *gate, "--json", "--repo", str(repo), "--", name])
    out, err = capsys.readouterr()
    return code, json.loads(out), err


@GATES
@pytest.mark.parametrize("name", ["docs/caf\udce9.md", "tools/caf\udce9.ts", "src/caf\udce9.txt"],
                         ids=["docs-md", "outside-every-scope-path", "no-scope-language"])
def test_a_name_no_scope_takes_is_left_out_with_one_line(measured, capsys, name, gate):
    code, payload, err = _rescore(measured, capsys, name, gate)

    shown = name.replace("\udce9", "\\xe9")
    assert code == 0, err
    assert err == (f"crapkit: left out {shown}: its name is not UTF-8 and no scope takes it, "
                   "so nothing in it is scored\n")
    assert payload["functions"] == []
    if gate:
        assert (payload["gate"]["ok"], payload["gate"]["judged"]) == (True, 0)


@GATES
def test_a_name_a_scope_takes_is_still_refused_with_the_rename(measured, capsys, gate):
    code, payload, err = _rescore(measured, capsys, "src/caf\udce9.ts", gate)

    assert code == 3, err
    assert payload["error"]["unread_files"][0]["path"] == "src/caf\\xe9.ts"
    assert "rename it (git mv) to a UTF-8 name" in err


def test_the_left_out_name_does_not_hide_a_readable_one_beside_it(measured, capsys):
    """The other arguments are still rescored."""
    (measured / "docs").mkdir()
    (measured / "docs" / "caf\udce9.md").write_text("x\n", encoding="utf-8")

    assert main(["rescore", "--json", "--repo", str(measured), "--",
                 "docs/caf\udce9.md", "src/app.ts"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert {row["path"] for row in payload["functions"]} == {"src/app.ts"}
