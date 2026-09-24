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


# --- a region with no start_line ----------------------------------------------
#
# coverage.py writes start_line from 7.13.1; 7.6.0 to 7.13.0 write the regions
# without it. The reader took the region's first line instead, which is the
# first line of the body, and that is the line a nested function's def
# statement sits on in its encloser's region: `outer.inner` never ran, yet it
# joined `outer`'s region by exact start and scored as half covered. Regions
# below are coverage 7.12.0's report on this source, recorded, with the file's
# statement lines beside them; 7.13.2 writes start_line 1, 2, 11 and 17.

MOD_SOURCE = (
    "def outer(x):\n    def inner(y):\n        if y > 0:\n            return y + 1\n"
    "        return y - 1\n    if x:\n        return inner(x)\n    return 0\n\n\n"
    "def documented(a):\n    \"\"\"Doc.\"\"\"\n    return a\n\n\n"
    "@staticmethod\ndef deco(\n    a,\n    b,\n):\n    return a + b\n")


def _region(executed, missing, statements, covered, branches=0, covered_branches=0):
    return {"executed_lines": executed, "missing_lines": missing,
            "summary": {"num_statements": statements, "covered_lines": covered,
                        "num_branches": branches, "covered_branches": covered_branches}}


def _report_7_12(start_lines: dict) -> dict:
    regions = {"outer": _region([2, 6, 8], [7], 4, 3, 2, 1),
               "outer.inner": _region([], [3, 4, 5], 3, 0, 2, 0),
               "documented": _region([13], [], 1, 1),
               "deco": _region([], [21], 1, 0),
               "": _region([1, 11, 16, 17], [], 4, 4)}
    for name, start in start_lines.items():
        regions[name]["start_line"] = start
    return {"meta": {"branch_coverage": True, "version": "7.12.0"},
            "files": {"pkg\\mod.py": {"executed_lines": [1, 2, 6, 8, 11, 13, 16, 17],
                                      "missing_lines": [3, 4, 5, 7, 21],
                                      "functions": regions}}}


STARTS_7_13 = {"outer": 1, "outer.inner": 2, "documented": 11, "deco": 17}


@pytest.mark.parametrize("start_lines", [{}, dict.fromkeys(STARTS_7_13), STARTS_7_13],
                         ids=["absent-coverage-7.12", "null", "present-coverage-7.13"])
def test_a_region_starts_on_its_def_statement_whatever_coverage_wrote(start_lines):
    per_file = parse_coveragepy(json.dumps(_report_7_12(start_lines)), path_prefix="")

    assert {fn.name: fn.start for fn in per_file["pkg/mod.py"]} == STARTS_7_13


@pytest.mark.parametrize("start_lines", [{}, STARTS_7_13], ids=["coverage-7.12", "coverage-7.13"])
def test_a_nested_function_that_never_ran_scores_its_own_coverage(start_lines):
    from crapkit.analyze import analyze_source
    from crapkit.score import score_rows
    from crapkit.snapshot import build_inventory_rows

    per_file = parse_coveragepy(json.dumps(_report_7_12(start_lines)), path_prefix="")
    rows = build_inventory_rows({"py": analyze_source("pkg/mod.py", MOD_SOURCE)})

    scored = {row.long_name: row.cov for row in score_rows(rows, per_file, lane_scopes={"py"})}

    assert scored["outer.inner( y )"] == 0.0
    assert scored["outer( x )"] == 0.5


# --- a function region with no summary ----------------------------------------
#
# coverage.py writes a summary on every region. `.get("summary", {})` read an
# absent one as zero statements and zero branches, so the function scored as
# never run, while a null one refused the report with an AttributeError.
# Both now refuse the report with a line naming the function.

@pytest.mark.parametrize("summary", ["absent", None, [], "4 of 4"],
                         ids=["absent", "null", "a-list", "a-string"])
def test_a_region_without_a_summary_object_refuses_the_report_naming_the_function(summary):
    report = json.loads(json.dumps(REPORT))
    guarded = report["files"]["pylib\\mod.py"]["functions"]["guarded"]
    if summary == "absent":
        del guarded["summary"]
    else:
        guarded["summary"] = summary

    with pytest.raises(ToolError, match=r"coverage\.py report .*guarded: no summary object"):
        parse_coveragepy(json.dumps(report), path_prefix="")


# --- every other shape a region's fields arrive in ----------------------------
#
# Each field a reader needs is either refused out loud or read as coverage.py
# means it; a field the reader does not need changes nothing.

def _shaped(mutate) -> dict:
    fn = {"start_line": 1, "executed_lines": [2, 3], "missing_lines": [5],
          "summary": {"num_statements": 3, "covered_lines": 2,
                      "num_branches": 2, "covered_branches": 1}}
    report = {"meta": {"version": "7.13.2", "branch_coverage": True},
              "files": {"src/a.py": {"executed_lines": [1, 2, 3], "missing_lines": [5],
                                     "functions": {"f": fn, "": {
                                         "executed_lines": [1], "missing_lines": [],
                                         "summary": {"num_statements": 1, "covered_lines": 1}}}}},
              "totals": {"covered_lines": 3}}
    mutate(report, report["files"]["src/a.py"], fn)
    return report


_REFUSED = {
    "executed_lines-null": (lambda r, f, fn: fn.update(executed_lines=None), "not iterable"),
    "missing_lines-null": (lambda r, f, fn: fn.update(missing_lines=None), "not iterable"),
    "num_branches-null": (lambda r, f, fn: fn["summary"].update(num_branches=None),
                          "num_branches must be a nonnegative integer count"),
    "num_branches-a-string": (lambda r, f, fn: fn["summary"].update(num_branches="2"),
                              "num_branches must be a nonnegative integer count"),
    "function-entry-null": (lambda r, f, fn: f["functions"].update(f=None), "coverage.py report"),
    "file-entry-null": (lambda r, f, fn: r["files"].update({"src/a.py": None}),
                        "coverage.py report"),
    "files-null": (lambda r, f, fn: r.update(files=None), "'files' is not a JSON object"),
    "functions-null-in-every-file": (lambda r, f, fn: f.update(functions=None),
                                     "no function regions for any"),
}


@pytest.mark.parametrize("shape", list(_REFUSED))
def test_a_needed_field_in_the_wrong_shape_refuses_the_report(shape):
    mutate, words = _REFUSED[shape]

    with pytest.raises(ToolError, match=words):
        parse_coveragepy(json.dumps(_shaped(mutate)), path_prefix="")


def test_a_report_that_is_a_list_refuses_as_not_an_object():
    with pytest.raises(ToolError, match="not a JSON object"):
        parse_coveragepy("[]", path_prefix="")


_UNCHANGED = {
    "meta-absent": lambda r, f, fn: r.pop("meta"),
    "meta-null": lambda r, f, fn: r.update(meta=None),
    "totals-null": lambda r, f, fn: r.update(totals=None),
    "covered_lines-a-whole-float": lambda r, f, fn: fn["summary"].update(covered_lines=2.0),
}


@pytest.mark.parametrize("shape", list(_UNCHANGED))
def test_a_field_the_scores_do_not_read_changes_nothing(shape):
    """meta carries branch_coverage, so without it the reader warns that the
    report holds no branch data; its functions, spans and counts stay the same."""
    control = parse_coveragepy(json.dumps(_shaped(lambda *_: None)), path_prefix="")

    assert (parse_coveragepy(json.dumps(_shaped(_UNCHANGED[shape])), path_prefix="")
            == control)


def test_a_non_ascii_function_name_is_kept_as_written():
    def rename(r, f, fn):
        f["functions"]["café_世界"] = f["functions"].pop("f")

    (fn,) = parse_coveragepy(json.dumps(_shaped(rename)), path_prefix="")["src/a.py"]

    assert (fn.name, fn.start, fn.coverage) == ("café_世界", 1, 0.5)


def test_a_report_with_no_files_measures_no_file():
    assert parse_coveragepy(json.dumps(_shaped(lambda r, f, fn: r.update(files={}))),
                            path_prefix="") == {}
