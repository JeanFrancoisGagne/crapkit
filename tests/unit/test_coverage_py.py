"""coverage.py JSON parser seam: report text in, per-file function coverage out. Pure."""
import json

import pytest

from coverage_readers import parse_coveragepy
from crapkit.errors import ToolError

REPORT = {
    "meta": {"branch_coverage": True, "version": "7.13.2"},
    "files": {
        "pylib\\mod.py": {
            "functions": {
                "guarded": {
                    "start_line": 1,
                    "executed_lines": [1, 2, 3],
                    "missing_lines": [4],
                    "summary": {"covered_lines": 3, "num_statements": 4,
                                "num_branches": 4, "covered_branches": 3},
                },
                "helper.<locals>.inner": {
                    "start_line": 7,
                    "executed_lines": [],
                    "missing_lines": [7, 8],
                    "summary": {"covered_lines": 0, "num_statements": 2,
                                "num_branches": 0, "covered_branches": 0},
                },
            },
        }
    },
}


def test_branch_coverage_per_function_with_span_from_line_data():
    per_file = parse_coveragepy(json.dumps(REPORT), path_prefix="")
    fns = {f.name: f for f in per_file["pylib/mod.py"]}
    g = fns["guarded"]
    assert g.start == 1 and g.end == 4
    assert g.branches_total == 4 and g.branches_covered == 3
    assert g.coverage == 0.75


def test_zero_branch_function_falls_back_to_execution():
    per_file = parse_coveragepy(json.dumps(REPORT), path_prefix="")
    fns = {f.name: f for f in per_file["pylib/mod.py"]}
    inner = fns["helper.<locals>.inner"]
    assert inner.invoked is False
    assert inner.coverage == 0.0


def test_path_prefix_rebases_to_repo_relative():
    per_file = parse_coveragepy(json.dumps(REPORT), path_prefix="scripts")
    assert list(per_file) == ["scripts/pylib/mod.py"]


# --- a report that carries less than crapkit asked for --------------------
#
# `pytest --cov --cov-report=json` without --cov-branch is the default shape of
# an existing CI artifact, which is exactly what --reuse-artifacts is for, and
# it used to fail the whole lane. So did one file that lost its "functions" key,
# which threw away every other file in the same report.

NO_BRANCH = {
    "meta": {"branch_coverage": False, "version": "7.13.2"},
    "files": {"pylib/mod.py": {"functions": {
        "guarded": {"start_line": 1, "executed_lines": [1, 2, 3], "missing_lines": [4],
                    "summary": {"covered_lines": 3, "num_statements": 4}},
    }}},
}


def test_a_report_without_branch_data_scores_from_its_statements(capsys):
    """The model already falls back to statements for every branchless function,
    so the refusal blocked arithmetic crapkit performs on every normal run."""
    per_file = parse_coveragepy(json.dumps(NO_BRANCH), path_prefix="")

    (guarded,) = per_file["pylib/mod.py"]
    assert guarded.coverage == 0.75, "3 of 4 statements"
    assert "statement" in capsys.readouterr().err, "and the downgrade is said out loud"


def test_the_statement_downgrade_names_the_lane_that_read_the_report(capsys):
    parse_coveragepy(json.dumps(NO_BRANCH), path_prefix="", label="lane 'py'")

    assert "lane 'py'" in capsys.readouterr().err


def test_a_report_with_neither_branch_nor_statement_data_is_still_refused():
    """The one case the refusal was really written for: nothing to divide by, so
    every function would score as fully covered."""
    hollow = {"meta": {"branch_coverage": False},
              "files": {"a.py": {"functions": {"f": {
                  "start_line": 1, "executed_lines": [1], "missing_lines": [],
                  "summary": {"covered_lines": 0, "num_statements": 0}}}}}}

    with pytest.raises(ToolError, match="branch"):
        parse_coveragepy(json.dumps(hollow), path_prefix="")


def test_one_file_without_function_regions_does_not_throw_the_report_away(capsys):
    """coverage.py writes "functions" once per code-region kind the file's
    reporter declares, so a file measured by a plugin reporter that declares
    none loses the key while every .py file in the same report keeps it."""
    mixed = {"meta": {"branch_coverage": True},
             "files": {"pylib/mod.py": REPORT["files"]["pylib\\mod.py"],
                       "tpl/page.html": {"summary": {"num_statements": 3}}}}

    per_file = parse_coveragepy(json.dumps(mixed), path_prefix="")

    assert sorted(per_file) == ["pylib/mod.py"], "the file with no regions joins nothing"
    assert len(per_file["pylib/mod.py"]) == 2, "and the file that was fine is scored"
    assert "tpl/page.html" in capsys.readouterr().err, "the skipped file is named"


def test_report_without_function_regions_anywhere_is_rejected():
    """No file in the report carries regions, which is the "coverage is too old"
    case the message is written for."""
    old = {"meta": {"branch_coverage": True}, "files": {"a.py": {"summary": {}}}}
    with pytest.raises(ToolError, match="function regions"):
        parse_coveragepy(json.dumps(old), path_prefix="")


def test_an_old_report_without_cov_branch_names_the_coverage_version():
    """`pytest --cov --cov-report=json` on a coverage below 7.6 is one shape,
    not two: no regions anywhere AND no branch data. The branch verdict got
    there first and sent the reader off to add --cov-branch, which changes
    nothing, before they could learn the version is the cause."""
    old = {"meta": {"branch_coverage": False},
           "files": {"a.py": {"summary": {}}, "b.py": {"summary": {}}}}

    with pytest.raises(ToolError, match="function regions"):
        parse_coveragepy(json.dumps(old), path_prefix="")


def test_malformed_report_is_loud():
    with pytest.raises(ToolError, match="coverage.py"):
        parse_coveragepy("not json", path_prefix="")


def test_qualname_collapse_fails_conservative_never_confident():
    # coverage.py collapses conditionally-defined same-name functions into one
    # region; the executed twin's data lands in the "" bucket, which we skip.
    # The pinned direction: the mismatched span joins nothing, scores untested
    # (CRAP overstated), never inherits the wrong twin's coverage.
    collapsed = {
        "meta": {"branch_coverage": True},
        "files": {"m.py": {"functions": {
            "": {"start_line": 1, "executed_lines": [9, 10, 11], "missing_lines": [],
                  "summary": {"covered_lines": 3, "num_statements": 3, "num_branches": 2, "covered_branches": 2}},
            "make": {"start_line": 6, "executed_lines": [], "missing_lines": [6, 7],
                      "summary": {"covered_lines": 0, "num_statements": 2, "num_branches": 0, "covered_branches": 0}},
        }}},
    }
    import json as _json

    from crapkit.score import score_rows
    from crapkit.snapshot import InventoryRow
    per_file = parse_coveragepy(_json.dumps(collapsed), path_prefix="")
    assert [f.name for f in per_file["m.py"]] == ["make"], "module bucket is skipped"
    executed_twin = InventoryRow("py", "m.py", "make( x )", 9, 11, 3, 3, 3, 3, 1, 1)
    (scored,) = score_rows([executed_twin], per_file, lane_scopes={"py"})
    assert scored.cov == 0.0 and scored.flag == "untested"


def _region(executed, missing, branches=(0, 0)):
    lines = len(executed) + len(missing)
    return {"executed_lines": executed, "missing_lines": missing,
            "summary": {"covered_lines": len(executed), "num_statements": lines,
                        "num_branches": branches[0], "covered_branches": branches[1]}}


# coverage.py 7.10.6 over (no start_line: a region starts where its lines do):
# 62 def outer(flag):           72 def wrap(items):            85 def one_line(value): return value + 1
# 63     def inner(value):      73     total = len(items)
# 64         if value: ...      74     def each(item):
# 67     if flag: ...           75         return item + total
#                               76     return [each(i) for i in items]
NO_START_LINE = {"meta": {"branch_coverage": True, "version": "7.10.6"}, "files": {"m.py": {
    "functions": {
        "": _region([1, 62, 72, 85], []),
        "outer": _region([63, 67, 69], [68], branches=(2, 1)),
        "outer.inner": _region([], [64, 65, 66], branches=(2, 0)),
        "wrap": _region([73, 74, 76], []),
        "wrap.each": _region([], [75]),
        "one_line": _region([85], [], branches=(2, 2)),
    }}}}


def test_a_region_with_no_start_line_starts_on_its_def_statement():
    """Before 7.13.1 a region names no start_line and its lines begin at the
    body. The def statement is the statement just ahead of the body, held by
    the code around the function; a one-line def holds its own."""
    per_file = parse_coveragepy(json.dumps(NO_START_LINE), path_prefix="")

    assert {fn.name: (fn.start, fn.end) for fn in per_file["m.py"]} == {
        "outer": (62, 69), "outer.inner": (63, 66), "wrap": (72, 76), "wrap.each": (74, 75),
        "one_line": (85, 85)}


def test_a_nested_def_joins_its_own_region_in_a_report_with_no_start_line():
    """outer's body opens with inner's def, so a region starting at its first
    body line put outer on inner's start line, and the join handed inner
    outer's 1 of 2 branches while no test called inner."""
    from crapkit.score import score_rows
    from crapkit.snapshot import InventoryRow

    rows = [InventoryRow("s", "m.py", name, start, end, 2, 2, 2, 3, 1, 1)
            for name, start, end in (("outer( flag )", 62, 69), ("outer.inner( value )", 63, 66),
                                     ("wrap( items )", 72, 76), ("wrap.each( item )", 74, 75))]
    per_file = parse_coveragepy(json.dumps(NO_START_LINE), path_prefix="")

    scored = score_rows(rows, per_file, lane_scopes={"s"})

    assert [(row.long_name, row.cov) for row in scored] == [
        ("outer( flag )", 0.5), ("outer.inner( value )", 0.0),
        ("wrap( items )", 1.0), ("wrap.each( item )", 0.0)]


def _excluded_region(executed, excluded, start_line=None):
    region = {"executed_lines": executed, "missing_lines": [], "excluded_lines": excluded,
              "summary": {"covered_lines": 0, "num_statements": 0, "num_branches": 0,
                          "covered_branches": 0, "excluded_lines": len(excluded)}}
    return {**region, "start_line": start_line} if start_line else region


# 92 def excluded(value):  # pragma: no cover      97 def stub():
# 93     if value and value > 0:                   98     ...
# 94         return 1                              99 def partly(value):
# 95     return 0                                 100     if value is None:
#                                                 101         raise NotImplementedError
#                                                 102     return value
def _pragma_report(version, executed, start_lines):
    excluded, stub = start_lines
    return {"meta": {"branch_coverage": True, "version": version}, "files": {"m.py": {
        "functions": {
            "": _region([1, 92, 97, 99], []),
            "excluded": _excluded_region(executed, [93, 94, 95], excluded),
            "stub": _excluded_region([], [98], stub),
            "partly": {**_region([100, 102], []), "excluded_lines": [101]},
        }}}}


def test_a_region_whose_every_statement_is_excluded_is_marked_excluded():
    """coverage.py keeps the region of a `# pragma: no cover` def, and of a
    stub whose body is `...`, with no statements and its lines excluded.
    7.10.6 also lists the excluded lines a call ran as executed. A region
    that keeps any statement is measured as before."""
    for report in (_pragma_report("7.16.1", [], (92, 97)),
                   _pragma_report("7.10.6", [93, 94], (None, None))):
        fns = parse_coveragepy(json.dumps(report), path_prefix="")["m.py"]

        assert [(fn.name, fn.start, fn.end, fn.excluded) for fn in fns] == [
            ("excluded", 92, 95, True), ("stub", 97, 98, True), ("partly", 99, 102, False)]
