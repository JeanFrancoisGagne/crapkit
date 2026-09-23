"""A start line and an `(anonymous)#N` handle name a position in the run `brief`
reads, in `explain` too.

`brief` reads the newest trusted run. `explain` read the newest run that held
the path, a failed verify included, so after a verify that moved functions
around and failed, `explain src/a.py 1` named one function and `brief src/a.py 1`
another, off the same file and the same line.
"""
import json

import pytest

from crapkit.cli import main
from crapkit.cli.queue import _pick_function
from crapkit.errors import CrapkitError
from crapkit.score import ScoredRow
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore, default_baseline

TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
coverage_optional = true
"""


def scored(path: str, name: str, start: int) -> ScoredRow:
    return ScoredRow("src", path, name, start, start + 2, 3, 3, 3, 3, 0, 0,
                     0.0, "untested", 12.0, "add-tests", 0, 1)


def inventoried(path: str, name: str, start: int) -> InventoryRow:
    return InventoryRow("src", path, name, start, start + 2, 3, 3, 3, 3, 0, 0, 0, 1)


@pytest.fixture()
def root(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8", newline="\n")
    (repo / ".crapkit").mkdir()
    return repo


def open_store(root) -> SnapshotStore:
    return SnapshotStore(root / ".crapkit" / "crap.sqlite")


def failed_verify_moved_everything(store: SnapshotStore) -> None:
    """Run 1 is a coverage run: f opens on line 1 and g on 5, and the first
    anonymous function in src/b.py is the bare `(anonymous)`. Run 2 is a verify
    that failed after swapping every pair."""
    store.write_run(commit="a" * 40, tool_versions={}, rows=[
        scored("src/a.py", "f( )", 1), scored("src/a.py", "g( )", 5),
        scored("src/b.py", "(anonymous)", 1), scored("src/b.py", "(anonymous) ( x )", 5)])
    failed = store.write_run(commit="b" * 40, tool_versions={}, kind="verify", rows=[
        scored("src/a.py", "g( )", 1), scored("src/a.py", "f( )", 5),
        scored("src/b.py", "(anonymous) ( x )", 1), scored("src/b.py", "(anonymous)", 5)])
    store.set_verdict_ok(failed, False, findings=1)


def explained(root, capsys, path: str, name: str) -> list[str]:
    """The long names `explain PATH NAME --json` reports, in order."""
    assert main(["explain", path, name, "--json", "--repo", str(root)]) == 0
    return [f["long_name"] for f in json.loads(capsys.readouterr().out)["functions"]]


def test_a_start_line_names_the_function_brief_names_after_a_failed_verify(root, capsys):
    store = open_store(root)
    failed_verify_moved_everything(store)
    baseline = default_baseline(store)["id"]

    briefed = _pick_function("src/a.py", store.read_scored_file(baseline, "src/a.py"), "1")

    assert briefed.long_name == "f( )"
    assert explained(root, capsys, "src/a.py", "1") == ["f( )"]


def test_an_anonymous_handle_counts_the_positions_brief_counts(root, capsys):
    store = open_store(root)
    failed_verify_moved_everything(store)
    baseline = default_baseline(store)["id"]

    briefed = _pick_function("src/b.py", store.read_scored_file(baseline, "src/b.py"),
                             "(anonymous)#1")

    assert briefed.long_name == "(anonymous)"
    assert explained(root, capsys, "src/b.py", "(anonymous)#1") == ["(anonymous)"]


def test_a_name_still_reaches_every_run_that_scored_it(root, capsys):
    """Only the positional forms moved. A name matches what any run scored, so
    the failed verify still shows in the trajectory it belongs to."""
    store = open_store(root)
    failed_verify_moved_everything(store)

    assert main(["explain", "src/a.py", "g", "--json", "--repo", str(root)]) == 0
    (function,) = json.loads(capsys.readouterr().out)["functions"]

    assert [h["run_id"] for h in function["history"]] == [1, 2]


def test_a_start_line_reads_the_newest_of_several_trusted_runs(root, capsys):
    """Two coverage runs, both trusted, and the functions swapped lines between
    them. brief reads the newer one, so line 1 is g in both commands."""
    store = open_store(root)
    store.write_run(commit="a" * 40, tool_versions={},
                    rows=[scored("src/a.py", "f( )", 1), scored("src/a.py", "g( )", 5)])
    store.write_run(commit="b" * 40, tool_versions={},
                    rows=[scored("src/a.py", "g( )", 1), scored("src/a.py", "f( )", 5)])
    baseline = default_baseline(store)["id"]

    briefed = _pick_function("src/a.py", store.read_scored_file(baseline, "src/a.py"), "1")

    assert briefed.long_name == "g( )"
    assert explained(root, capsys, "src/a.py", "1") == ["g( )"]


def test_a_store_with_no_trusted_run_reads_its_newest_run_with_rows(root, capsys):
    """`crapkit inventory` alone scores no coverage, so no run is trusted and
    `brief` has nothing to read. explain still answers off what was measured
    last: the second inventory swapped the two functions, and line 5 is f."""
    store = open_store(root)
    store.write_run(commit="a" * 40, tool_versions={}, kind="inventory",
                    rows=[inventoried("src/a.py", "f( )", 1), inventoried("src/a.py", "g( )", 5)])
    store.write_run(commit="b" * 40, tool_versions={}, kind="inventory",
                    rows=[inventoried("src/a.py", "g( )", 1), inventoried("src/a.py", "f( )", 5)])

    assert explained(root, capsys, "src/a.py", "5") == ["f( )"]


def test_a_store_with_no_run_at_all_matches_nothing(root, capsys):
    open_store(root)

    assert main(["explain", "src/a.py", "1", "--repo", str(root)]) == CrapkitError.exit_code

    assert capsys.readouterr().err == (
        "crapkit: no function matching '1' in src/a.py appears in any run\n")
