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


# --- a function whose summary lacks what the report says it measured --------------
#
# coverage.py writes every function's summary with both counts of each kind it
# measured. A summary that is gone, or a covered count with no total beside it,
# read as 0 of 0: a function that ran scored cov 0 with nothing on stderr. A
# function with no branch counts in a report that measures branches fell back
# to its statements without a word, and a report with no meta at all said its
# term was statement-based while the functions scored on branches.

import copy  # noqa: E402

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


def _no_branch_and(edit):
    """The function as coverage.py writes one with no branch, 0 of 0, then `edit`."""
    def edited(fn):
        fn["summary"].update(num_branches=0, covered_branches=0)
        edit(fn)
    return edited


REFUSED = {
    "function-summary-missing": (lambda fn: fn.pop("summary"),
                                 "summary is missing, so crapkit cannot tell how much of it ran"),
    "covered-lines-missing": (_drop("covered_lines"), "num_statements without covered_lines"),
    "covered-branches-missing": (_drop("covered_branches"),
                                 "num_branches without covered_branches"),
    "num-statements-missing": (_drop("num_statements"), "covered_lines without num_statements"),
    "summary-empty": (_drop("covered_lines", "num_statements", "num_branches", "covered_branches"),
                      "summary holds neither statement nor branch counts"),
    "statement-counts-missing-with-no-branch": (
        _no_branch_and(_drop("covered_lines", "num_statements")),
        "summary holds no statement counts and no branch, so crapkit cannot tell how much "
        "of it ran"),
    "count-not-a-count": (lambda fn: fn["summary"].update(covered_lines=-1),
                          "covered_lines must be a nonnegative integer count, got -1"),
}


@pytest.mark.parametrize("form", sorted(REFUSED))
def test_a_summary_missing_a_count_refuses_the_report_naming_the_file_and_the_fix(form):
    """The refusal names the source file as well as the function, since one
    report holds many files with a function of that name, and ends with what
    to do, as the istanbul refusals do."""
    edit, what = REFUSED[form]

    with pytest.raises(ToolError) as raised:
        parse_coveragepy(json.dumps(_edited(edit)), path_prefix="")

    message = str(raised.value)
    assert message.startswith("unparseable coverage.py report"), message
    assert f"pylib/mod.py: guarded: {what}" in message, message
    assert message.endswith("; regenerate the report with the coverage tool"), message


def test_statement_counts_alone_missing_still_score_on_branches():
    """Both statement counts gone and both branch counts kept: the branch term
    decides this function's coverage, so the score cannot move."""
    per_file = parse_coveragepy(json.dumps(_edited(_drop("covered_lines", "num_statements"))),
                                path_prefix="")

    guarded = {f.name: f for f in per_file["pylib/mod.py"]}["guarded"]
    assert guarded.coverage == 0.75


def test_a_function_with_no_branch_scores_from_its_statements():
    """The control for the refusal above: 0 of 0 branches and both statement
    counts kept, so the statements decide, 3 of 4."""
    per_file = parse_coveragepy(json.dumps(_edited(_no_branch_and(lambda fn: None))),
                                path_prefix="")

    guarded = {f.name: f for f in per_file["pylib/mod.py"]}["guarded"]
    assert (guarded.branches_total, guarded.coverage) == (0, 0.75)


def test_a_start_line_missing_takes_the_first_measured_line():
    """coverage 7.6 to 7.13.0 wrote no start_line: the span starts where the
    function's measured lines do, and the score does not move."""
    per_file = parse_coveragepy(json.dumps(_edited(lambda fn: fn.pop("start_line"))),
                                path_prefix="")

    guarded = {f.name: f for f in per_file["pylib/mod.py"]}["guarded"]
    assert (guarded.start, guarded.coverage) == (1, 0.75)


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
