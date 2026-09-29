"""An istanbul file entry the producer wrote sparsely: what it may leave out, and
what it may not.

The istanbul file coverage object (istanbuljs/istanbul-lib-coverage, FileCoverage)
keys its hit maps `f`, `s` and `b` by the ids of `fnMap`, `statementMap` and
`branchMap`. A hand-built or trimmed entry can drop a map, or an id from one. A
dropped count is refused and named, since reading it as 0 moved a function's
score with nothing said (CHANGELOG 0.8.1, "A coverage artifact that lacks a
count is refused"); so is a function with no `loc.end.line`. A missing name
reads as `(anonymous)`, a missing `loc.start` as the declaration, and a missing
column as the whole line (docs/lanes.md, the `fnMap` row).
"""
import json
import re

import pytest

from coverage_readers import parse_istanbul, parse_istanbul_missing
from crapkit import coverage_istanbul
from crapkit.coverage_istanbul import FnCoverage
from crapkit.errors import ToolError

ROOT = "C:/repo"


def _at(line, column=None):
    return {"line": line} if column is None else {"line": line, "column": column}


def _span(start, end):
    return {"start": _at(*start), "end": _at(*end)}


def _read(entry: dict) -> list:
    return parse_istanbul(json.dumps({f"{ROOT}/src/a.ts": entry}), repo_root=ROOT)["src/a.ts"]


def _refusal(entry: dict) -> str:
    with pytest.raises(ToolError) as refused:
        _read(entry)
    return str(refused.value)


def _counts(fn: FnCoverage) -> tuple:
    return (fn.name, fn.start, fn.end, fn.invoked, fn.branches_total, fn.branches_covered,
            fn.statements_total, fn.statements_covered)


# function late() {}          line 7, listed first
# function early() {          line 2, no name
#   if (x) {} else {}         line 3, its first arm taken
#   work();                   line 4, never reached
# }                           line 5
SPARSE = {
    "fnMap": {"0": {"name": "late", "decl": {"start": _at(7, 9)}, "loc": _span((7, 16), (7, 18))},
              "1": {"decl": {"start": _at(2, 9)}, "loc": _span((2, 16), (5, 1))}},
    "f": {"0": 3, "1": 1},
    "branchMap": {"0": {"loc": {"start": _at(3, 2)},
                        "locations": [_span((3, 9), (3, 11)), _span((3, 17), (3, 19))]}},
    "statementMap": {"0": _span((3, 2), (3, 19)), "1": _span((4, 2), (4, 9))},
    "s": {"0": 1, "1": 0},
    "b": {"0": [1, 0]},
}


def test_a_missing_name_is_anonymous_and_spans_come_back_in_line_order():
    assert [_counts(fn) for fn in _read(SPARSE)] == [
        ("(anonymous)", 2, 5, True, 2, 1, 2, 1), ("late", 7, 7, True, 0, 0, 0, 0)]


@pytest.mark.parametrize("group, key, named", [
    ("f", "1", "function '1' has no hit count in `f`"),
    ("s", "1", "statement '1' has no hit count in `s`"),
    ("b", "0", "branch '0' has no hit count in `b`")])
def test_a_dropped_count_is_refused_and_named(group, key, named):
    dropped = {**SPARSE, group: {k: v for k, v in SPARSE[group].items() if k != key}}

    assert f"src/a.ts: {named}" in _refusal(dropped)


def test_an_entry_without_hit_maps_is_refused_at_its_first_function():
    bare = {key: value for key, value in SPARSE.items() if key not in ("f", "s", "b")}

    assert "src/a.ts: function '0' has no hit count in `f`" in _refusal(bare)
    assert "(2 such in this file)" in _refusal(bare)


def test_a_branch_hit_list_s_arms_count_even_when_the_map_is_all_it_has():
    """A branch with no `locations` list cannot be checked against its hit
    counts, so every count is an arm."""
    counted = {**SPARSE, "branchMap": {"0": {"loc": {"start": _at(3, 2)}}}, "b": {"0": [2, 0, 0]}}

    assert _counts(_read(counted)[0])[4:6] == (3, 1)


def test_a_statement_that_never_ran_is_a_dead_line():
    missing = parse_istanbul_missing(json.dumps({f"{ROOT}/src/a.ts": SPARSE}), repo_root=ROOT)
    none = {**SPARSE, "s": {"0": 0, "1": 0}}
    none_ran = parse_istanbul_missing(json.dumps({f"{ROOT}/src/a.ts": none}), repo_root=ROOT)

    assert (missing, none_ran) == ({"src/a.ts": {4}}, {"src/a.ts": {3, 4}})


def test_a_function_without_a_start_location_opens_its_body_at_the_declaration():
    """`loc` with an end and no start: the body starts at `decl.start`, so a
    statement on the next line is the function's."""
    entry = {"fnMap": {"0": {"name": "g", "decl": {"start": _at(9, 3)}, "loc": {"end": _at(11, 1)}}},
             "f": {"0": 0}, "statementMap": {"0": _span((10, 2), (10, 8))}, "s": {"0": 0}}

    assert [_counts(fn) for fn in _read(entry)] == [("g", 9, 11, False, 0, 0, 1, 0)]


@pytest.mark.parametrize("fn", [{"name": "f", "decl": {"start": _at(5, 2)}},
                                {"name": "f", "decl": {"start": _at(5, 2)}, "loc": {"end": {}}}],
                         ids=["no-loc", "no-end-line"])
def test_a_function_without_an_end_line_is_refused(fn):
    """Read as the declaration line, the span shrank to one line and an invoked
    function scored as covered."""
    assert "src/a.ts: fnMap['0'] has no loc.end.line" in _refusal({"fnMap": {"0": fn},
                                                                  "f": {"0": 1}})


def test_a_body_start_without_a_column_opens_at_the_first_column():
    """_position: a start the producer gives no column opens the whole line, so a
    statement in column 0 of the body's first line is the function's."""
    entry = {"fnMap": {"0": {"name": "f", "decl": {"start": _at(1, 9)},
                             "loc": {"start": _at(2), "end": _at(4, 1)}}},
             "f": {"0": 1}, "statementMap": {"0": _span((2, 0), (2, 8))}, "s": {"0": 0}}

    assert _counts(_read(entry)[0])[6:] == (1, 0)


def test_a_counter_on_a_function_s_last_position_is_that_function_s():
    """A span holds its end: `_drop_ended` lets go of a span only after its finish."""
    entry = {"fnMap": {"0": {"name": "f", "decl": {"start": _at(1, 0)},
                             "loc": _span((1, 12), (3, 1))}},
             "f": {"0": 1}, "branchMap": {"0": {"loc": {"start": _at(3, 1)}}},
             "b": {"0": [1, 1]}}

    assert _counts(_read(entry)[0])[4:6] == (2, 2)


def test_of_two_functions_opening_on_one_line_the_later_one_is_innermost():
    """`const f = (a) => (b) => a ? b : 0` : both arrows start on line 1 and end on
    it; the ternary in the second belongs to the second."""
    entry = {"fnMap": {"0": {"name": "outer", "decl": {"start": _at(1, 10)},
                             "loc": _span((1, 17), (1, 34))},
                       "1": {"name": "inner", "decl": {"start": _at(1, 17)},
                             "loc": _span((1, 24), (1, 34))}},
             "f": {"0": 1, "1": 1}, "branchMap": {"0": {"loc": {"start": _at(1, 24)}}},
             "b": {"0": [1, 0]}}

    assert {fn.name: fn.branches_total for fn in _read(entry)} == {"outer": 0, "inner": 2}


def test_a_root_given_with_its_trailing_slash_still_rebases():
    text = json.dumps({f"{ROOT}/src/a.ts": SPARSE})

    assert sorted(parse_istanbul(text, repo_root=ROOT + "/")) == ["src/a.ts"]


@pytest.mark.parametrize("group, hits, field", [
    ("f", {"0": "3"}, "f['0']"), ("s", {"0": -1}, "s['0']"), ("b", {"0": [1, "x"]}, "b['0'][1]")])
def test_a_count_that_is_no_count_is_named_by_its_place(group, hits, field):
    with pytest.raises(ValueError, match=f"^{re.escape(field)} must be a nonnegative integer count"):
        coverage_istanbul._admit_hits({**SPARSE, group: hits})


def test_a_clean_file_s_rows_are_a_plain_list_and_a_clamped_one_counts_each_clamp():
    clean = coverage_istanbul._file_coverage(json.loads(json.dumps(SPARSE)))
    clamped = coverage_istanbul._file_coverage({**json.loads(json.dumps(SPARSE)),
                                                "b": {"0": [-1, -2, 0]}})

    assert type(clean) is list
    assert clamped.clamped == 2


def _clamped(counts: dict) -> dict:
    return {path: coverage_istanbul.ClampedBranchCounts([], count) for path, count in counts.items()}


def test_the_clamped_note_names_the_three_loudest_files(capsys):
    coverage_istanbul._note_clamped_branches(_clamped({"a.ts": 1, "d.ts": 3, "c.ts": 3, "b.ts": 2}))
    coverage_istanbul._note_clamped_branches(_clamped({"a.ts": 1, "b.ts": 1, "c.ts": 1}))

    assert capsys.readouterr().err.splitlines() == [
        "crapkit: 9 negative derived branch count(s) in 4 file(s) clamped to 0; the producer's "
        "else-path subtraction underflowed and each such branch reads as uncovered:",
        "crapkit:   c.ts: 3", "crapkit:   d.ts: 3", "crapkit:   b.ts: 2",
        "crapkit:   ... and 1 more",
        "crapkit: 3 negative derived branch count(s) in 3 file(s) clamped to 0; the producer's "
        "else-path subtraction underflowed and each such branch reads as uncovered:",
        "crapkit:   a.ts: 1", "crapkit:   b.ts: 1", "crapkit:   c.ts: 1"]


def test_spans_come_back_in_line_order_whatever_their_names_and_ends():
    entry = {"fnMap": {"0": {"name": "a", "decl": {"start": _at(9, 0)}, "loc": _span((9, 8), (9, 20))},
                       "1": {"name": "z", "decl": {"start": _at(2, 0)}, "loc": _span((2, 8), (20, 1))}},
             "f": {"0": 1, "1": 1}}

    assert [fn.name for fn in _read(entry)] == ["z", "a"]


def test_a_statement_without_a_column_starts_before_a_function_opening_later_on_its_line():
    """_position: a start with no column opens the whole line, column 0, which is
    ahead of a body that opens at column 1, so the statement is the code around it."""
    entry = {"fnMap": {"0": {"name": "f", "decl": {"start": _at(2, 1)},
                             "loc": _span((2, 1), (4, 1))}},
             "f": {"0": 1}, "statementMap": {"0": {"start": _at(2)}, "1": {"end": _at(3)}},
             "s": {"0": 0, "1": 0}}

    assert _counts(_read(entry)[0])[6:] == (0, 0)
    assert parse_istanbul_missing(json.dumps({f"{ROOT}/src/a.ts": entry}), repo_root=ROOT) == {
        "src/a.ts": {2}}


def test_a_root_whose_last_letter_is_x_still_rebases():
    text = json.dumps({"C:/rX/src/a.ts": SPARSE})

    assert sorted(parse_istanbul(text, repo_root="C:/rX")) == ["src/a.ts"]


def test_an_artifact_with_no_file_says_the_run_measured_nothing():
    with pytest.raises(coverage_istanbul.ToolError) as refused:
        parse_istanbul("{}", repo_root=ROOT)

    assert str(refused.value) == (
        "istanbul artifact is empty (zero files) - the coverage run measured nothing; "
        "rerun the lane and check that its command runs the tests")
