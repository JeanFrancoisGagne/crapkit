"""coverage.py JSON parser seam: report text in, per-file function coverage out. Pure."""
import copy
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


@pytest.mark.parametrize("regionless", [{"summary": {"num_statements": 3}},
                                        {"summary": {"num_statements": 3}, "functions": None}],
                         ids=["functions-absent", "functions-null"])
def test_one_file_without_function_regions_does_not_throw_the_report_away(capsys, regionless):
    """coverage.py writes "functions" once per code-region kind the file's
    reporter declares, so a file measured by a plugin reporter that declares
    none loses the key while every .py file in the same report keeps it. A
    null "functions" reads the same way."""
    mixed = {"meta": {"branch_coverage": True},
             "files": {"pylib/mod.py": REPORT["files"]["pylib\\mod.py"],
                       "tpl/page.html": regionless}}

    per_file = parse_coveragepy(json.dumps(mixed), path_prefix="")

    assert sorted(per_file) == ["pylib/mod.py"], "the file with no regions joins nothing"
    assert len(per_file["pylib/mod.py"]) == 2, "and the file that was fine is scored"
    assert "tpl/page.html" in capsys.readouterr().err, "the skipped file is named"


def test_report_without_function_regions_anywhere_is_rejected():
    """No file in the report carries regions, which is the "coverage is too old"
    case the message is written for."""
    old = {"meta": {"branch_coverage": True}, "files": {"a.py": {"summary": {}}}}
    with pytest.raises(ToolError, match="function regions .* needs coverage>=7.13.1"):
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
# without it, and no line inside a region is its def line. The body starts
# below the def, and a nested function's def statement sits in its encloser's
# region: read from the body, `outer.inner` never ran, yet it joined `outer`'s
# region by exact start and scored as half covered. A def line worked out from
# the statements around the region is a guess the report never made, so a
# region without start_line refuses the report, naming the file, the first such
# function and the coverage to install. Regions below are coverage 7.12.0's
# report on this source, recorded; 7.13.2 writes start_line 1, 2, 11 and 17 on
# the functions and 1 on the "" module region.

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
WRITTEN_7_13 = {**STARTS_7_13, "": 1}
NO_START = ("pkg/mod.py: outer: no start_line; coverage.py writes it on every function from "
            "7.13.1, so install coverage>=7.13.1 and rerun the lane")


def _read_7_12(start_lines: dict, then):
    """`then` of the functions the reader read, or the reason it refused the
    report, which follows the artifact's name."""
    try:
        per_file = parse_coveragepy(json.dumps(_report_7_12(start_lines)), path_prefix="")
    except ToolError as exc:
        return str(exc).split(".json: ", 1)[1]
    return then(per_file)


def _starts(per_file: dict) -> dict:
    return {fn.name: fn.start for fn in per_file["pkg/mod.py"]}


def _nested_cov(per_file: dict) -> dict:
    from crapkit.analyze import analyze_source
    from crapkit.score import score_rows
    from crapkit.snapshot import build_inventory_rows

    rows = build_inventory_rows({"py": analyze_source("pkg/mod.py", MOD_SOURCE)})
    scored = {row.long_name: row.cov for row in score_rows(rows, per_file, lane_scopes={"py"})}
    return {name: scored[name] for name in ("outer( x )", "outer.inner( y )")}


@pytest.mark.parametrize("start_lines, read",
                         [({}, NO_START), (dict.fromkeys(STARTS_7_13), NO_START),
                          (WRITTEN_7_13, STARTS_7_13)],
                         ids=["absent-coverage-7.12", "null", "present-coverage-7.13"])
def test_a_region_starts_on_its_def_statement_whatever_coverage_wrote(start_lines, read):
    assert _read_7_12(start_lines, _starts) == read


@pytest.mark.parametrize("start_lines, read",
                         [({}, NO_START),
                          (WRITTEN_7_13, {"outer( x )": 0.5, "outer.inner( y )": 0.0})],
                         ids=["coverage-7.12", "coverage-7.13"])
def test_a_nested_function_that_never_ran_scores_its_own_coverage(start_lines, read):
    assert _read_7_12(start_lines, _nested_cov) == read


def test_the_refusal_names_the_first_function_without_a_start_line():
    """outer carries its start_line, so outer.inner, nested in it, is the one named."""
    written = {name: WRITTEN_7_13[name] for name in ("outer", "documented", "deco", "")}

    assert _read_7_12(written, _starts) == NO_START.replace("outer:", "outer.inner:")


@pytest.mark.parametrize("start", ["1", 1.0, True, 0, -1, [1]],
                         ids=["a-string", "a-float", "true", "zero", "negative", "a-list"])
def test_a_start_line_that_is_not_a_line_number_refuses_the_report(start):
    read = _read_7_12({**WRITTEN_7_13, "outer": start}, _starts)

    assert read == f"pkg/mod.py: outer: start_line must be a line number, got {start!r}"


def test_the_extras_install_the_coverage_the_reader_names():
    """`pip install "crapkit[py]"` and the dev install must land a coverage
    whose report this reader takes, or the lane fails on a fresh setup."""
    import tomllib
    from pathlib import Path

    from crapkit.coverage_py import COVERAGE_FLOOR

    root = Path(__file__).resolve().parents[2]
    extras = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"]["optional-dependencies"]

    assert {name: [d for d in extras[name] if d.startswith("coverage")]
            for name in ("py", "dev")} == {"py": [COVERAGE_FLOOR], "dev": [COVERAGE_FLOOR]}


def test_the_lanes_page_quotes_the_start_line_refusal():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    page = (root / "docs" / "lanes.md").read_text(encoding="utf-8")

    assert f"/repo/.crapkit/cov/py.json: {NO_START}" in page


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


# --- a function whose summary lacks what the report says it measured --------------
#
# coverage.py writes every function's summary with both counts of each kind it
# measured. A summary that is gone, or a covered count with no total beside it,
# read as 0 of 0: a function that ran scored cov 0 with nothing on stderr. A
# function with no branch counts in a report that measures branches fell back
# to its statements without a word, and a report with no meta at all said its
# term was statement-based while the functions scored on branches.

GUARDED = ("pylib\\mod.py", "guarded")


def _edited(edit) -> dict:
    report = copy.deepcopy(REPORT)
    edit(report["files"][GUARDED[0]]["functions"][GUARDED[1]])
    return report


def _drop(*keys):
    def edit(fn):
        for key in keys:
            fn["summary"].pop(key)
    return edit


REFUSED = {
    "function-summary-missing": (lambda fn: fn.pop("summary"),
                                 "guarded: no summary object, so crapkit cannot tell how much "
                                 "of it ran"),
    "covered-lines-missing": (_drop("covered_lines"), "guarded: num_statements without covered_lines"),
    "covered-branches-missing": (_drop("covered_branches"),
                                 "guarded: num_branches without covered_branches"),
    "num-statements-missing": (_drop("num_statements"), "guarded: covered_lines without num_statements"),
    "summary-empty": (_drop("covered_lines", "num_statements", "num_branches", "covered_branches"),
                      "guarded: summary holds neither statement nor branch counts"),
}


@pytest.mark.parametrize("form", sorted(REFUSED))
def test_a_summary_missing_a_count_refuses_the_report_and_names_the_function(form):
    edit, named = REFUSED[form]

    with pytest.raises(ToolError) as raised:
        parse_coveragepy(json.dumps(_edited(edit)), path_prefix="")

    assert named in str(raised.value), raised.value
    assert str(raised.value).startswith("unparseable coverage.py report"), raised.value


def test_statement_counts_alone_missing_still_score_on_branches():
    """Both statement counts gone and both branch counts kept: the branch term
    decides this function's coverage, so the score cannot move."""
    per_file = parse_coveragepy(json.dumps(_edited(_drop("covered_lines", "num_statements"))),
                                path_prefix="")

    guarded = {f.name: f for f in per_file["pylib/mod.py"]}["guarded"]
    assert guarded.coverage == 0.75


def test_a_start_line_missing_refuses_the_report_naming_the_file_as_the_lane_keys_it():
    """The file is named by the key crapkit joins on: the lane's path_prefix
    glued on and the report's backslashes turned forward."""
    with pytest.raises(ToolError) as raised:
        parse_coveragepy(json.dumps(_edited(lambda fn: fn.pop("start_line"))),
                         path_prefix="backend")

    assert str(raised.value).endswith(
        ": backend/pylib/mod.py: guarded: no start_line; coverage.py writes it on every "
        "function from 7.13.1, so install coverage>=7.13.1 and rerun the lane"), raised.value


def test_one_function_with_no_branch_counts_in_a_branch_report_refuses_the_report():
    """coverage.py writes 0 of 0 for a function with no branch. Read from its
    statements instead, this one's coverage moved from 0.75 to 1.0 unsaid."""
    with pytest.raises(ToolError) as raised:
        parse_coveragepy(json.dumps(_edited(_drop("num_branches", "covered_branches"))),
                         path_prefix="")

    assert ("coverage.py report measures branches, but 1 function(s) carry no branch counts "
            "(pylib/mod.py: guarded), so crapkit cannot tell how many of their branches ran; "
            "regenerate the report with the coverage tool") == str(raised.value)


def test_a_report_without_meta_reads_its_branch_counts_as_branch_data(capsys):
    report = copy.deepcopy(REPORT)
    del report["meta"]

    per_file = parse_coveragepy(json.dumps(report), path_prefix="")

    guarded = {f.name: f for f in per_file["pylib/mod.py"]}["guarded"]
    assert guarded.coverage == 0.75
    assert "statement-based" not in capsys.readouterr().err, "the functions carry branch counts"


def test_a_report_without_meta_or_branch_counts_still_says_it_is_statement_based(capsys):
    report = copy.deepcopy(NO_BRANCH)
    del report["meta"]

    parse_coveragepy(json.dumps(report), path_prefix="")

    assert "carries no branch data" in capsys.readouterr().err


def test_the_lanes_page_quotes_the_branchless_function_refusal():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    report = {"meta": {"branch_coverage": True}, "files": {"api/views.py": {"functions": {
        "render": {"start_line": 1, "executed_lines": [1], "missing_lines": [],
                   "summary": {"covered_lines": 1, "num_statements": 1}}}}}}

    with pytest.raises(ToolError) as raised:
        parse_coveragepy(json.dumps(report), path_prefix="")

    quoted = f"crapkit: lane 'py' FAILED: {raised.value}"
    assert quoted in (root / "docs" / "lanes.md").read_text(encoding="utf-8").splitlines()
