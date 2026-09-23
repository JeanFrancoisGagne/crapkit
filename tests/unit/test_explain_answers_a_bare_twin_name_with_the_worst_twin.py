"""A bare twin name means the worst twin in `explain`, as it does in `brief`.

One file, two `__post_init__( self )`, and the worse one second: line 7 scores
30 and line 18 scores 66. `brief` answered the bare name with line 18 and its
mark of 66. `explain`, and the MCP tool get_function_history that runs it,
answered the same NAME with line 7's history and its mark of 30.
docs/ratchet.md and AGENTS.md promise the worst twin for both.
"""
import json

import pytest

from crapkit.cli import main
from crapkit.cli.queue import _pick_function
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

PATH = "src/iso.py"
POST_INIT = "__post_init__( self )"

TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
coverage_optional = true
"""

MARKS = ("path\tlong_name\tcrap\n"
         f"{PATH}\t{POST_INIT}\t30.0000\n"
         f"{PATH}\t{POST_INIT}#2\t66.0000\n")


def scored(start: int, ccn: int, crap: float) -> ScoredRow:
    return ScoredRow("src", PATH, POST_INIT, start, start + 3, ccn, ccn, ccn, 4, 1, 0,
                     0.5, "measured", crap, "decompose", 0, 1)


@pytest.fixture()
def repo(tmp_path):
    """The reporter's shape with the worse twin second in the file."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "iso.py").write_text("".join(f"# {n}\n" for n in range(1, 30)),
                                         encoding="utf-8", newline="\n")
    (root / "crapkit.toml").write_text(TOML, encoding="utf-8", newline="\n")
    (root / "crapkit-ratchet.tsv").write_text(MARKS, encoding="utf-8", newline="\n")
    (root / ".crapkit").mkdir()
    store = SnapshotStore(root / ".crapkit" / "crap.sqlite")
    run_id = store.write_run(commit="a" * 40, tool_versions={},
                             rows=[scored(7, 5, 30.0), scored(18, 8, 66.0)])
    return root, store, run_id


def explain(root, capsys, name: str) -> dict:
    """What get_function_history returns: `explain PATH NAME --json`."""
    assert main(["explain", PATH, name, "--json", "--repo", str(root)]) == 0
    (function,) = json.loads(capsys.readouterr().out)["functions"]
    return function


def test_explain_answers_a_bare_twin_name_with_the_worst_twins_history_and_mark(repo, capsys):
    root, _, _ = repo

    function = explain(root, capsys, "__post_init__")

    assert [h["crap"] for h in function["history"]] == [66.0]
    assert function["ratchet_mark"] == 66.0


def test_explain_and_brief_pick_the_same_twin_for_the_long_name_too(repo, capsys):
    root, store, run_id = repo
    picked = _pick_function(PATH, store.read_scored_file(run_id, PATH), POST_INIT)

    function = explain(root, capsys, POST_INIT)

    assert (picked.start, picked.crap) == (18, 66.0)
    assert [h["crap"] for h in function["history"]] == [picked.crap]


def test_the_twin_selector_still_reaches_the_twin_a_bare_name_does_not(repo, capsys):
    root, _, _ = repo

    function = explain(root, capsys, "__post_init__#1")

    assert [h["crap"] for h in function["history"]] == [30.0]
    assert function["ratchet_mark"] == 30.0
