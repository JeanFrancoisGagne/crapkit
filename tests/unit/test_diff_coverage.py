"""Added-lines diff coverage: changed lines with no execution are where the
next bug ships. Line-level truth comes from the same artifacts the lanes wrote."""
import json

from crapkit.coverage_istanbul import parse_istanbul_missing
from crapkit.coverage_py import parse_coveragepy_missing
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
