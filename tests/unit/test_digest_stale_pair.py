"""The digest pair is the newest two runs with IDENTICAL lane sets, which is not
always the newest run and its predecessor.

When a `--lane` subset run lands on top, the pair rule correctly steps back to
an older like-for-like pair — and then reports that older delta in this week's
voice, with nothing said about the runs it stepped over. On one large consumer that printed runs 17 -> 19 while run 22 sat in the store.

The pair rule stays. The silence goes: one stderr line naming what was compared
and what was skipped, so a reader can tell last week's news from this week's.
"""
import pytest

from crapkit.cli import main
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
"""


def scored(crap: float) -> list:
    return [ScoredRow("src", "src/m.py", "f( )", 1, 9, 7, 7, 7, 5, 1, 1,
                      0.25, "measured", crap, "add-tests")]


def store_at(root, lane_sets_and_craps) -> SnapshotStore:
    """A snapshot store with one run per (lane set, crap) pair, oldest first."""
    (root / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (root / ".crapkit").mkdir(exist_ok=True)
    store = SnapshotStore(root / ".crapkit" / "crap.sqlite")
    for i, (lanes, crap) in enumerate(lane_sets_and_craps, start=1):
        store.write_run(commit=f"c{i}", tool_versions={}, rows=scored(crap),
                        lanes={name: {} for name in lanes}, kind="coverage")
    return store


FULL = ("unit", "py")
SUBSET = ("unit",)


@pytest.fixture()
def stale_repo(tmp_path):
    """Two full runs, then a subset run on top: the pair is 1 -> 2, run 3 is not
    in it, and nothing in the old output said so."""
    store_at(tmp_path, [(FULL, 40.0), (FULL, 55.0), (SUBSET, 9.0)])
    return tmp_path


def test_digest_names_the_runs_it_compared_and_the_newer_run_it_skipped(stale_repo, capsys):
    assert main(["digest", "--repo", str(stale_repo)]) == 0

    err = capsys.readouterr().err
    assert "warning:" in err, "a digest of an older pair must not read as this week's"
    assert "1 -> 2" in err, ("the warning has to name the runs actually compared", err)
    assert "3" in err.split("skipping", 1)[-1], ("the skipped newer run is the whole point", err)


def test_the_stale_warning_leaves_the_digest_body_alone(stale_repo, capsys):
    assert main(["digest", "--repo", str(stale_repo)]) == 0

    out = capsys.readouterr().out
    assert "CRAP load 40.0 -> 55.0" in out, out
    assert "warning" not in out, "stdout is the digest body; the warning is stderr"


def test_a_pair_that_is_the_newest_two_runs_warns_about_nothing(tmp_path, capsys):
    store_at(tmp_path, [(FULL, 40.0), (FULL, 55.0)])

    assert main(["digest", "--repo", str(tmp_path)]) == 0

    captured = capsys.readouterr()
    assert captured.err == "", ("the healthy pair is the newest two runs", captured.err)
    assert "CRAP load 40.0 -> 55.0" in captured.out


def test_a_run_skipped_in_the_middle_is_named_too(tmp_path, capsys):
    """The subset run need not be newest: a full run between two subset runs is
    stepped over the same way, and the reader is owed the same line."""
    store_at(tmp_path, [(SUBSET, 40.0), (FULL, 12.0), (SUBSET, 55.0)])

    assert main(["digest", "--repo", str(tmp_path)]) == 0

    err = capsys.readouterr().err
    assert "1 -> 3" in err and "2" in err.split("skipping", 1)[-1], err


def test_skipped_runs_is_pure_and_empty_for_an_adjacent_pair():
    from crapkit.digest import latest_comparable_pair, skipped_runs

    runs = [{"id": 1, "lanes": {"unit": {}, "py": {}}},
            {"id": 2, "lanes": {"unit": {}, "py": {}}},
            {"id": 3, "lanes": {"unit": {}}}]

    assert [r["id"] for r in skipped_runs(runs, latest_comparable_pair(runs))] == [3]
    assert skipped_runs(runs[:2], latest_comparable_pair(runs[:2])) == []
