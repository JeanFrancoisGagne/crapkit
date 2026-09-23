"""Added-lines diff coverage: changed lines with no execution are where the
next bug ships. Line-level truth comes from the same artifacts the lanes wrote."""
import json

from coverage_readers import parse_istanbul_missing
from coverage_readers import parse_coveragepy_missing
from crapkit.verify import diff_uncovered


def test_istanbul_missing_lines_are_the_dead_statements():
    artifact = {
        "C:/r/src/a.ts": {
            "statementMap": {
                "0": {"start": {"line": 2}, "end": {"line": 2}},
                "1": {"start": {"line": 5}, "end": {"line": 5}},
                "2": {"start": {"line": 9}, "end": {"line": 9}},
            },
            "s": {"0": 3, "1": 0, "2": 0},
            "fnMap": {}, "f": {}, "branchMap": {}, "b": {},
        }
    }
    assert parse_istanbul_missing(json.dumps(artifact), repo_root="C:/r") == {"src/a.ts": {5, 9}}


def test_coveragepy_missing_lines_come_from_the_file_level():
    report = {
        "meta": {"branch_coverage": True},
        "files": {"pylib/mod.py": {"missing_lines": [4, 7], "functions": {}}},
    }
    assert parse_coveragepy_missing(json.dumps(report), path_prefix="") == {"pylib/mod.py": {4, 7}}


def test_diff_uncovered_is_the_intersection_of_changed_and_dead():
    changed = {"src/a.ts": [(1, 6)], "src/b.ts": [(10, 12)]}
    missing = {"src/a.ts": {5, 9}, "src/c.ts": {1}}
    assert diff_uncovered(changed, missing) == [("src/a.ts", 5)]


def test_diff_uncovered_is_empty_when_nothing_overlaps():
    assert diff_uncovered({"src/a.ts": [(1, 3)]}, {"src/a.ts": {9}}) == []


def test_diff_uncovered_hunk_bounds_are_inclusive_at_both_ends():
    changed = {"src/a.ts": [(4, 8)]}
    missing = {"src/a.ts": {3, 4, 6, 8, 9}}
    assert diff_uncovered(changed, missing) == [("src/a.ts", 4), ("src/a.ts", 6), ("src/a.ts", 8)]


def test_diff_uncovered_reports_each_hunk_in_range_order_lines_ascending():
    changed = {"src/b.ts": [(20, 22), (1, 3)], "src/a.ts": [(1, 9)]}
    missing = {"src/a.ts": {9, 1}, "src/b.ts": {21, 2}}
    assert diff_uncovered(changed, missing) == [
        ("src/a.ts", 1), ("src/a.ts", 9), ("src/b.ts", 21), ("src/b.ts", 2)
    ], "paths sort, hunks keep the diff's own order, lines ascend inside a hunk"


def _row(path: str, start: int, end: int, flag: str):
    from crapkit.score import ScoredRow

    return ScoredRow("src", path, f"f{start}( )", start, end, 1, 1, 1, end - start + 1,
                     0, 0, 0.0, flag, 1.0, "ok")


def test_a_changed_file_no_artifact_mentions_counts_its_untested_functions_lines():
    """Nothing imported the file, so coverage recorded nothing for it and none of
    its functions ran. Leaving it out passed a new module under a ceiling of 0."""
    changed = {"src/new.py": [(1, 9)]}
    rows = [_row("src/new.py", 2, 4, "untested"), _row("src/new.py", 7, 8, "untested")]
    assert diff_uncovered(changed, {}, rows) == [
        ("src/new.py", 2), ("src/new.py", 3), ("src/new.py", 4),
        ("src/new.py", 7), ("src/new.py", 8)]


def test_a_file_an_artifact_mentions_keeps_the_artifacts_own_dead_lines():
    """A measured file's rows can floor to untested (a def on its own line) while
    the artifact saw every line run; the artifact is the line-level truth there."""
    changed = {"src/a.py": [(1, 9)]}
    rows = [_row("src/a.py", 2, 4, "untested")]
    assert diff_uncovered(changed, {"src/a.py": set()}, rows) == []
    assert diff_uncovered(changed, {"src/a.py": {3}}, rows) == [("src/a.py", 3)]


def test_rows_no_lane_measures_add_no_lines():
    changed = {"src/a.py": [(1, 9)]}
    rows = [_row("src/a.py", 2, 4, "no-lane"), _row("src/a.py", 5, 6, "cc-only")]
    assert diff_uncovered(changed, {}, rows) == []
