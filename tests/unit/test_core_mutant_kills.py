"""The core modules' edges the weekly core run left unchecked, each value worked
by hand from the rule the module's docstring states.

digest: a run is quiet only when the totals hold still and nothing moved, and a
renamed function over its ceiling moves: it is new. keys: an older reader's
nested Python name is respelled once, at its head, and a spelling that is not
that reader's comes back as it was. worklist: a claim on a file the run holds
nothing in stays open. score: a stale overlay joins a function only to a row a
lane measured or called untested, never to one no lane covered. ratchet: the
upgrade remedy names each newer tool and says `it` or `both`. verify: the
results field is sorted, compact JSON with only JSON punctuation left bare, a
junit id's `./` prefix is read off, and a read that finds no changed unread
file hands the verdict back as it was.
"""
from fractions import Fraction
import json

import pytest

from accuracy.kit import exact

from crapkit.config import Config
from crapkit.coverage_istanbul import parse_istanbul_both_file, parse_istanbul_missing_file
from crapkit.digest import build_digest
from crapkit.errors import ToolError
from crapkit.keys import respelled_nested
from crapkit.ratchet import upgrade_remedy
from crapkit.score import ScoredRow, overlay_stale_coverage
from crapkit.snapshot import InventoryRow
from crapkit.gate import Unread
from crapkit.verify import Verdict, baseline_tsv_lines, dirty_failure_ids, evaluate
from crapkit.worklist import closable_claims


def _scored(path: str, name: str, ccn: int, cov: str, flag: str = "measured",
            scope: str = "src") -> ScoredRow:
    crap = float(exact.crap(ccn, Fraction(cov)))
    return ScoredRow(scope, path, name, 1, 9, ccn, ccn, ccn, 5, 1, 1, float(Fraction(cov)), flag,
                     crap, "decompose" if ccn > 6 else "ok")


# --- digest -------------------------------------------------------------------------------------

def test_a_renamed_function_over_its_ceiling_is_news_though_the_totals_hold_still():
    """f( ) renamed h( ): one function, ccn 9 at cov 1/2 both times, so crap
    9^2 * (1/2)^3 + 9 = 19.125 each run and the totals match. h( ) is a new
    function over the ceiling of 6, so the digest speaks."""
    before = [_scored("src/a.ts", "f( )", 9, "1/2")]
    after = [_scored("src/a.ts", "h( )", 9, "1/2")]

    digest = build_digest(before, after, ceiling_of=Config(target=6).ceiling_of)

    assert (digest.quiet, digest.lines[1:]) == (False, ["new over ceiling: src/a.ts h( ) (crap 19.1)"])


# --- keys ---------------------------------------------------------------------------------------

def test_an_older_nested_name_is_respelled_at_its_head_only():
    """`a.a.b.c` is version 10's spelling of c in b in a. A default that names
    the same dotted path is text in the parameter list and stays as written."""
    assert respelled_nested("m.py", "a.a.b.c( x = a.a.b.c )") == "a.b.c( x = a.a.b.c )"


def test_a_name_whose_parent_is_no_older_spelling_comes_back_as_it_is():
    """Eight parts would be a def four deep: its parent `a.b.c.d` must itself be
    a three-deep spelling, `a.a.b.c`-shaped, and `a` is not `b`."""
    assert respelled_nested("m.py", "a.b.c.a.b.c.d.e( )") == "a.b.c.a.b.c.d.e( )"


def test_a_six_part_name_is_no_older_spelling():
    """The older spellings double: 1, 2, 4 and 8 parts. Six parts take `a.b.c`
    as a parent, three parts, which no depth spells."""
    assert respelled_nested("m.py", "a.b.a.b.c.d( )") == "a.b.a.b.c.d( )"


# --- worklist -----------------------------------------------------------------------------------

def test_an_older_claim_on_a_file_the_run_never_scored_stays_open():
    """keys.claim_in_run asks the run for every name it holds in the claim's file,
    and a file it holds nothing in answers with no names, not a failure."""
    claim = {"id": 1, "path": "gone.py", "long_name": "a.a.b.c( )", "commit": "c1",
             "created_at": "2026-01-01T00:00:00Z"}

    assert closable_claims([claim], [_scored("kept.py", "k( )", 1, "1")], target=6,
                           scope_targets={}, stale_commits=set()) == []


# --- score --------------------------------------------------------------------------------------

def test_a_stale_overlay_joins_no_row_the_baseline_had_no_lane_for():
    """The baseline scored src with no lane, so its row for f says nothing was
    measured. src has a lane now, and f scores untested at cov 0, never no-lane."""
    fresh = [InventoryRow("src", "a.ts", "f", 1, 9, 3, 3, 3, 9, 1, 1, 0, 0)]
    baseline = [_scored("a.ts", "f", 3, "0", flag="no-lane")]

    [row] = overlay_stale_coverage(fresh, baseline, lane_scopes={"src"}, target=6)

    assert (row.cov, row.flag, row.crap) == (0.0, "untested", 12.0)


# --- coverage_istanbul --------------------------------------------------------------------------

STATEMENT_REFUSALS = {  # a statementMap entry: the refusal that names its file and field
    "a start line that is a string": (
        {"start": {"line": "2", "column": 0}, "end": {"line": 2, "column": 9}},
        "`statementMap['0'].start.line` holds a string, not a line number"),
    "a start that is an array": (
        {"start": [2], "end": {"line": 2, "column": 9}},
        "`statementMap['0'].start` holds an array, not an object"),
    "a statement that is an array": ([1], "`statementMap['0']` holds an array, not an object"),
}


def _one_statement_artifact(root, statement):
    (root / "a.js").write_text("function f(){\n  return 1;\n}\n", encoding="utf-8")
    source = str(root / "a.js")
    artifact = root / "cov.json"
    artifact.write_text(json.dumps({source: {
        "path": source, "statementMap": {"0": statement}, "s": {"0": 0},
        "fnMap": {}, "f": {}, "branchMap": {}, "b": {}}}), encoding="utf-8")
    return artifact


REGENERATE = "; regenerate the artifact with the coverage tool that wrote it"


@pytest.mark.parametrize("label", STATEMENT_REFUSALS)
def test_the_dead_lines_read_refuses_a_malformed_statement_by_file_and_field(tmp_path, label):
    """istanbul writes each statementMap entry as {start: {line, column}, end: ...}
    (istanbul-lib-coverage's file coverage schema); any other shape is refused
    with the file and the field named, and what to do."""
    statement, says = STATEMENT_REFUSALS[label]
    artifact = _one_statement_artifact(tmp_path, statement)

    with pytest.raises(ToolError) as refused:
        parse_istanbul_missing_file(artifact, repo_root=str(tmp_path))

    assert str(refused.value) == f"unparseable istanbul artifact: a.js: {says}{REGENERATE}"


@pytest.mark.parametrize("label", STATEMENT_REFUSALS)
def test_the_scoring_read_refuses_a_malformed_statement_by_file_and_field(tmp_path, label):
    """The read that attributes statements to functions meets the entry first
    and names it by the same words, the artifact's own path leading."""
    statement, says = STATEMENT_REFUSALS[label]
    artifact = _one_statement_artifact(tmp_path, statement)

    with pytest.raises(ToolError) as refused:
        parse_istanbul_both_file(artifact, repo_root=str(tmp_path))

    assert str(refused.value) == f"unparseable istanbul artifact {artifact}: a.js: {says}{REGENERATE}"


def _istanbul_file(fn_map: dict, branch_map: dict, hits: dict) -> str:
    source = "C:/repo/src/app.ts"
    return json.dumps({source: {"path": source, "fnMap": fn_map, "f": dict.fromkeys(fn_map, 1),
                                "branchMap": branch_map, "b": hits}})


FUNCTION = {"name": "f", "decl": {"start": {"line": 1, "column": 9}},
            "loc": {"start": {"line": 1, "column": 13}, "end": {"line": 4, "column": 1}}}
BRANCH = {"loc": {"start": {"line": 2, "column": 2}},
          "locations": [{"start": {"line": 2, "column": 2}}, {"start": {"line": 2, "column": 9}}]}
REPORTER = "(every istanbul reporter writes one; regenerate the artifact with the runner's reporter)"


def _istanbul_refusal(text: str) -> str:
    from coverage_readers import parse_istanbul

    with pytest.raises(ToolError) as refused:
        parse_istanbul(text, repo_root="C:/repo")
    return str(refused.value).split(".json: ", 1)[1]


def test_a_branch_s_counts_that_are_no_array_are_refused_by_what_they_hold():
    text = _istanbul_file({"0": FUNCTION}, {"0": BRANCH}, {"0": "1"})

    assert _istanbul_refusal(text) == (
        "src/app.ts: `b['0']` holds a string, not an array of branch counts; "
        "regenerate the artifact with the coverage tool that wrote it")


def test_a_branch_that_is_no_object_is_refused_as_a_branch_with_no_line():
    """istanbul writes each branchMap entry as an object; a number reads no
    locations to count and no line to sit on."""
    text = _istanbul_file({"0": FUNCTION}, {"0": BRANCH, "1": 5}, {"0": [1, 0], "1": [0]})

    assert _istanbul_refusal(text) == f"src/app.ts: branchMap['1'] has no loc.start.line and no line {REPORTER}"


def test_a_function_with_no_end_line_is_refused_saying_every_reporter_writes_one():
    function = {"name": "f", "decl": {"start": {"line": 1}}, "loc": {"start": {"line": 1}}}

    assert _istanbul_refusal(_istanbul_file({"0": function}, {}, {})) == (
        f"src/app.ts: fnMap['0'] has no loc.end.line {REPORTER}")


def test_a_branch_start_with_no_column_opens_its_line_ahead_of_a_function_at_column_one():
    """A start with no column is the line's first column (_position), which
    comes before g opens at column 1, so the branch is f's, the code around g."""
    from coverage_readers import parse_istanbul

    inner = {"name": "g", "decl": {"start": {"line": 2, "column": 1}},
             "loc": {"start": {"line": 2, "column": 1}, "end": {"line": 2, "column": 30}}}
    branch = {"loc": {"start": {"line": 2}}, "locations": [{"start": {"line": 2}}, {"start": {"line": 2}}]}
    text = _istanbul_file({"0": FUNCTION, "1": inner}, {"0": branch}, {"0": [1, 0]})

    rows = parse_istanbul(text, repo_root="C:/repo")["src/app.ts"]

    assert [(row.name, row.branches_total, row.branches_covered) for row in rows] == [
        ("f", 2, 1), ("g", 0, 0)]


# --- ratchet ------------------------------------------------------------------------------------

def test_the_upgrade_remedy_names_one_newer_tool_and_says_it():
    assert upgrade_remedy(["lizard"]) == (
        "the marks come from a newer lizard than this install; "
        "upgrade it to the version that wrote them")


def test_the_upgrade_remedy_names_both_newer_tools_and_every_crapkit_pin():
    assert upgrade_remedy(["crapkit", "lizard"]) == (
        "the marks come from a newer crapkit and a newer lizard than this install; "
        "upgrade both to the version that wrote them (the CLI, the Action's `uses:` pin "
        "and the pre-commit `rev` alike)")


# --- verify -------------------------------------------------------------------------------------

def test_the_results_field_is_sorted_compact_json_with_its_punctuation_left_bare():
    """Keys sorted, no space after `,` or `:`, and only the space in the test id
    percent-encoded."""
    results = {"unit": {"tests_total": 2, "failures": ["a b.py::t"]}}

    stamp = next(baseline_tsv_lines("c1", "coverage", [], results))

    assert stamp == ('# commit=c1 run_kind=coverage '
                     'results={"unit":{"failures":["a%20b.py::t"],"tests_total":2}}\n')


def test_a_junit_id_a_runner_wrote_with_a_leading_dot_slash_names_the_git_path():
    assert dirty_failure_ids(["./web/a.test.ts::t"], {"web/a.test.ts"}) == ["./web/a.test.ts::t"]


def test_a_read_that_finds_no_changed_unread_file_hands_the_verdict_back_as_it_was():
    """The verdict already fails on an unread file; a later read that finds none
    in the change neither drops it nor passes the gate."""
    unread = (Unread("a.py", "not UTF-8", False),)
    verdict = Verdict(ok=False, gate_violations=[], ratchet_regressions=[], new_failures=[],
                      dirty_failures=[], unread_files=unread)

    assert evaluate(fresh=[], changed_ranges={"a.py": []}, ratchet=[], baseline_failures=set(), fresh_failures=set(),
                    target=6, unread={"a.py": "not UTF-8", "b.py": "not UTF-8"}) == verdict
