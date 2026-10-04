"""The coverage evidence join: line and branch evidence attributed to inventory spans.

A coverage format with no function records (lcov DA and BRDA, Go coverprofile,
Cobertura and JaCoCo lines) hands score one file's hit lines, missed lines and
(line, total, covered) branch triples. score.join_evidence gives each line and
branch to the innermost inventory span holding it and builds one FnCoverage per
span that owns evidence; score_rows selects among those records by the rule it
already applies to producer records.

Four checks stand outside the join's own output:

1. a join table, every expected record worked by hand;
2. a differential against istanbul's own attribution
   (coverage_istanbul._file_coverage) over the unit fixtures and every recorded
   istanbul artifact, converted to line evidence: owners part only where a
   table lists, and every listed row sits on a line where two spans meet;
3. a brute-force oracle that finds each line's innermost span by linear scan;
4. the memory a 31,459-file tree of evidence holds, measured and printed.
"""
from array import array
from collections import Counter
import copy
import json
from pathlib import Path
import random
import tracemalloc

import pytest

from crapkit import coverage_istanbul
from crapkit.score import (FileEvidence, FnCoverage, SharedSpanFold, join_evidence,
                           score_rows, scored_tsv_lines)
from crapkit.snapshot import InventoryRow
import test_coverage_istanbul as istanbul_fixtures

PATH = "src/a.ts"
LANE = {"src"}


def _row(start, end, name=None, path=PATH):
    return InventoryRow("src", path, name or f"fn{start}_{end}( )", start, end,
                        3, 3, 3, end - start + 1, 0, 0)


def _ev(hit=(), missed=(), branches=()):
    return FileEvidence(array("I", sorted(hit)), array("I", sorted(missed)), tuple(branches))


def _reason(fn: FnCoverage) -> str:
    """The coverage reason score reads from the record's counts: branch, else
    statement, else invoked-or-not."""
    if fn.branches_total:
        return "branch"
    return "statement" if fn.statements_total else "invoked"


# --- check 1: the hand-worked join table -------------------------------------

OUTER, INNER = _row(1, 20, "outer( )"), _row(5, 8, "inner( )")
SHARED_A, SHARED_B = _row(1, 10, "first( )"), _row(1, 10, "second( )")

JOIN_TABLE = {
    # outer 1-20 holds inner 5-8; the branch on line 6 is inner's alone, and
    # outer keeps its own lines 2 (hit) and 12 (missed).
    "branch-in-nested-function": (
        [OUTER, INNER], _ev(hit=[2, 6], missed=[12], branches=[(6, 2, 1)]),
        [FnCoverage("outer( )", 1, 20, True, 0, 0, 2, 1),
         FnCoverage("inner( )", 5, 8, True, 2, 1, 1, 1)]),
    "hit-and-missed-lines-read-statement": (
        [_row(1, 10)], _ev(hit=[2, 3], missed=[4]),
        [FnCoverage("fn1_10( )", 1, 10, True, 0, 0, 3, 2)]),
    "branch-triples-read-branch": (
        [_row(1, 10)], _ev(hit=[2], missed=[3], branches=[(2, 4, 1), (3, 2, 0)]),
        [FnCoverage("fn1_10( )", 1, 10, True, 6, 1, 2, 1)]),
    "all-missed-is-not-invoked": (
        [_row(1, 10)], _ev(missed=[2, 3]),
        [FnCoverage("fn1_10( )", 1, 10, False, 0, 0, 2, 0)]),
    "span-with-no-evidence-gets-no-record": (
        [_row(1, 5), _row(10, 15)], _ev(hit=[2]),
        [FnCoverage("fn1_5( )", 1, 5, True, 0, 0, 1, 1)]),
    # inner 5-8 sits inside outer and owns no line: outer keeps 2 and 12.
    "nested-span-with-no-evidence-gets-no-record": (
        [OUTER, INNER], _ev(hit=[2], missed=[12]),
        [FnCoverage("outer( )", 1, 20, True, 0, 0, 2, 1)]),
    # `return values.map((value) => {` on 46: every line of withCallback 45-52
    # with evidence sits in the callback 46-51, so withCallback owns none.
    "encloser-with-no-evidence-gets-no-record": (
        [_row(45, 52, "withCallback( )"), _row(46, 51, "arrow( )")],
        _ev(hit=[46], missed=[47, 48, 50]),
        [FnCoverage("arrow( )", 46, 51, True, 0, 0, 4, 1)]),
    # A span nested on its encloser's start line takes line 2; the encloser
    # 1-20 starts where that record does and still owns nothing.
    "encloser-on-the-same-start-gets-no-record": (
        [_row(1, 20, "outer( )"), _row(1, 5, "head( )")], _ev(hit=[2]),
        [FnCoverage("head( )", 1, 5, True, 0, 0, 1, 1)]),
    "line-outside-every-span-is-dropped": (
        [_row(1, 5)], _ev(hit=[3, 50], missed=[7], branches=[(60, 2, 2)]),
        [FnCoverage("fn1_5( )", 1, 5, True, 0, 0, 1, 1)]),
    "only-lines-outside-make-nothing": (
        [_row(1, 5)], _ev(hit=[50], branches=[(60, 2, 2)]), []),
    "shared-span-gets-one-record": (
        [SHARED_A, SHARED_B], _ev(hit=[2], missed=[3]),
        [FnCoverage("first( )", 1, 10, True, 0, 0, 2, 1)]),
    "empty-evidence-makes-nothing": (
        [_row(1, 10)], _ev(), []),
    "start-and-end-lines-belong-to-the-span": (
        [_row(4, 9)], _ev(hit=[4], missed=[9], branches=[(3, 2, 2), (10, 2, 2)]),
        [FnCoverage("fn4_9( )", 4, 9, True, 0, 0, 2, 1)]),
    "nested-function-on-its-enclosers-start-line": (
        [_row(1, 9, "outer( )"), _row(1, 1, "lambda( )")], _ev(hit=[1, 2]),
        [FnCoverage("lambda( )", 1, 1, True, 0, 0, 1, 1),
         FnCoverage("outer( )", 1, 9, True, 0, 0, 1, 1)]),
}


@pytest.mark.parametrize("case", JOIN_TABLE)
def test_the_join_builds_the_hand_worked_records(case):
    rows, evidence, expected = JOIN_TABLE[case]
    assert join_evidence(rows, evidence) == expected


@pytest.mark.parametrize("case", JOIN_TABLE)
def test_a_joined_record_never_reads_invoked_only(case):
    rows, evidence, _ = JOIN_TABLE[case]
    assert {_reason(fn) for fn in join_evidence(rows, evidence)} <= {"branch", "statement"}


def test_the_join_reads_the_reason_arch_17_gives():
    _, statement_ev, _ = JOIN_TABLE["hit-and-missed-lines-read-statement"]
    _, branch_ev, _ = JOIN_TABLE["branch-triples-read-branch"]
    (statement,) = join_evidence([_row(1, 10)], statement_ev)
    (branch,) = join_evidence([_row(1, 10)], branch_ev)
    assert (_reason(statement), statement.coverage) == ("statement", 2 / 3)
    assert (_reason(branch), branch.coverage) == ("branch", 1 / 6)


NO_EVIDENCE_SCORES = {
    "span-with-no-evidence-gets-no-record": [
        ("fn1_5( )", 1.0, "measured"), ("fn10_15( )", 0.0, "untested")],
    "nested-span-with-no-evidence-gets-no-record": [
        ("outer( )", 0.5, "measured"), ("inner( )", 0.0, "untested")],
    "encloser-with-no-evidence-gets-no-record": [
        ("withCallback( )", 0.0, "untested"), ("arrow( )", 0.25, "measured")],
    "encloser-on-the-same-start-gets-no-record": [
        ("outer( )", 0.0, "untested"), ("head( )", 1.0, "measured")],
}


@pytest.mark.parametrize("case", NO_EVIDENCE_SCORES)
def test_a_span_with_no_evidence_scores_untested(case):
    """Never the record of a span it holds or sits in: a joined record speaks
    for its own span only."""
    rows, evidence, _ = JOIN_TABLE[case]
    scored = score_rows(rows, {}, lane_scopes=LANE, evidence_by_path={PATH: [evidence]})
    assert [(s.long_name, s.cov, s.flag) for s in scored] == NO_EVIDENCE_SCORES[case]


def test_a_producer_record_still_joins_by_overlap_beside_joined_records():
    """A producer record keeps the overlap fallback; only joined records are
    held to their own span."""
    rows = [_row(1, 20, "outer( )"), _row(30, 40, "late( )")]
    producer = {PATH: [FnCoverage("late", 31, 40, True, 4, 3)]}
    scored = score_rows(rows, producer, lane_scopes=LANE,
                        evidence_by_path={PATH: [_ev(hit=[2])]})
    assert [(s.long_name, s.cov, s.flag) for s in scored] == [
        ("outer( )", 1.0, "measured"), ("late( )", 0.75, "measured")]


def test_a_shared_span_takes_the_existing_floor():
    rows, evidence, _ = JOIN_TABLE["shared-span-gets-one-record"]
    fold = SharedSpanFold()
    scored = score_rows(rows, {}, lane_scopes=LANE, shared_spans=fold,
                        evidence_by_path={PATH: [evidence]})
    assert [(s.long_name, s.cov, s.flag, s.remedy) for s in scored] == [
        ("first( )", 0.0, "untested", "split-lines"), ("second( )", 0.0, "untested", "split-lines")]
    assert fold.sites == [[SHARED_A, SHARED_B]]


def test_a_nested_function_scores_its_own_evidence():
    rows, evidence, _ = JOIN_TABLE["branch-in-nested-function"]
    scored = score_rows(rows, {}, lane_scopes=LANE, evidence_by_path={PATH: [evidence]})
    assert [(s.long_name, s.cov, s.flag) for s in scored] == [
        ("outer( )", 0.5, "measured"), ("inner( )", 0.5, "measured")]


# --- a producer-record adapter's evidence joins nothing ----------------------

def _scored_bytes(rows, coverage, **kwargs) -> str:
    return "".join(scored_tsv_lines(score_rows(rows, coverage, lane_scopes=LANE, **kwargs)))


def test_evidence_with_no_hit_lines_joins_nothing():
    rows = [OUTER, INNER, _row(30, 40)]
    producer = {PATH: [FnCoverage("outer", 1, 20, True, 4, 1), FnCoverage("inner", 5, 8, False, 0, 0)]}
    keeps_records = FileEvidence(None, array("I", [6, 12, 31]), ((6, 2, 0),))

    assert join_evidence(rows, keeps_records) == []
    without = _scored_bytes(rows, producer)
    assert _scored_bytes(rows, producer, evidence_by_path={PATH: [keeps_records]}) == without
    assert _scored_bytes(rows, producer, evidence_by_path={"src/b.ts": [keeps_records]}) == without
    assert _scored_bytes(rows, producer, evidence_by_path={}) == without
    assert _scored_bytes(rows, producer, evidence_by_path=None) == without


def test_evidence_for_a_path_without_rows_moves_nothing():
    rows = [OUTER]
    producer = {PATH: [FnCoverage("outer", 1, 20, True, 4, 1)]}
    elsewhere = {"src/b.ts": [_ev(hit=[1, 2], branches=[(1, 2, 2)])]}
    assert (_scored_bytes(rows, producer, evidence_by_path=elsewhere)
            == _scored_bytes(rows, producer))


# --- check 3: the brute-force innermost-span oracle --------------------------

def _innermost_by_scan(spans: list[tuple[int, int]], line: int) -> tuple[int, int] | None:
    """The tightest span holding the line, the later start on a length tie,
    found by looking at every span."""
    best = None
    for start, end in spans:
        if start <= line <= end and (best is None
                                     or (end - start, -start) < (best[1] - best[0], -best[0])):
            best = (start, end)
    return best


def _nested_spans(rnd: random.Random, start: int, end: int, depth: int) -> list[tuple[int, int]]:
    """Random properly nested spans inside [start, end]: siblings, children that
    open on their parent's first line, one-line spans and repeated spans."""
    spans = []
    cursor = start
    while cursor <= end and depth < 4:
        child_start = cursor + rnd.choice([0, 0, 1, 3])
        child_end = min(end, child_start + rnd.choice([0, 0, 2, 6, 15, 40]))
        if child_start > end:
            break
        spans.append((child_start, child_end))
        if rnd.random() < 0.15:
            spans.append((child_start, child_end))
        spans.extend(_nested_spans(rnd, child_start, child_end, depth + 1))
        cursor = child_end + rnd.choice([1, 1, 2, 5])
    return spans


def _owner_per_line(rows, line) -> tuple[int, int] | None:
    records = join_evidence(rows, _ev(hit=[line]))
    assert len(records) <= 1
    return (records[0].start, records[0].end) if records else None


@pytest.mark.parametrize("seed", range(12))
def test_every_line_joins_the_span_a_linear_scan_finds(seed):
    rnd = random.Random(seed)
    spans = _nested_spans(rnd, 1, 300, 0)
    rnd.shuffle(spans)
    rows = [_row(start, end, f"f{i}( )") for i, (start, end) in enumerate(spans)]

    for line in range(0, 320):
        assert _owner_per_line(rows, line) == _innermost_by_scan(spans, line), line


@pytest.mark.parametrize("seed", range(12))
def test_a_whole_file_of_evidence_counts_each_line_once_in_its_owner(seed):
    rnd = random.Random(1000 + seed)
    spans = _nested_spans(rnd, 1, 300, 0)
    rows = [_row(start, end) for start, end in spans]
    hit = {line for line in range(1, 320) if rnd.random() < 0.6}
    missed = set(range(1, 320)) - hit
    branches = [(line, 2, rnd.randint(0, 2)) for line in range(1, 320, 7)]

    expected: dict[tuple[int, int], list[int]] = {}
    for line in range(1, 320):
        owner = _innermost_by_scan(spans, line)
        if owner is not None:
            counts = expected.setdefault(owner, [0, 0, 0, 0])
            counts[2] += 1
            counts[3] += line in hit
    for line, total, covered in branches:
        owner = _innermost_by_scan(spans, line)
        if owner is not None:
            expected[owner][0] += total
            expected[owner][1] += covered

    records = join_evidence(rows, _ev(hit, missed, branches))
    assert {(fn.start, fn.end): [fn.branches_total, fn.branches_covered,
                                 fn.statements_total, fn.statements_covered]
            for fn in records} == expected
    assert all(fn.invoked == (fn.statements_covered > 0) for fn in records)


# --- check 2: istanbul's own attribution, as line evidence -------------------

UNIT_FIXTURES = {
    "artifact": istanbul_fixtures.ARTIFACT,
    "statements": istanbul_fixtures.STATEMENTS,
    "declared-arrows": istanbul_fixtures.DECLARED_ARROWS,
    "chosen-arrow": istanbul_fixtures.CHOSEN_ARROW,
}
# The recordings the accuracy suite keeps; its probe_repo module needs filelock,
# which the unit suite's install does not carry, so the folder is named here.
RECORDINGS = Path(__file__).resolve().parents[1] / "accuracy" / "coverage_oracles" / "recorded"
RECORDED = sorted(path for path in RECORDINGS.glob("*/*.json")
                  if "files" not in json.loads(path.read_bytes()))


def _istanbul_records(cov: dict) -> list[FnCoverage]:
    return coverage_istanbul._file_coverage(copy.deepcopy(cov))


def _line(position: dict) -> int:
    return position["start"]["line"]


def _stmt_lines(cov: dict) -> dict[str, int]:
    return {sid: _line(stmt) for sid, stmt in cov.get("statementMap", {}).items()
            if isinstance(stmt.get("start", {}).get("line"), int)}


def _branch_lines(cov: dict) -> dict[str, int]:
    return {bid: coverage_istanbul._branch_line(bid, branch)
            for bid, branch in cov.get("branchMap", {}).items()}


def _as_rows(records: list[FnCoverage]) -> list[InventoryRow]:
    """Inventory spans equal to the fnMap spans istanbul attributed to."""
    return [_row(fn.start, fn.end, fn.name) for fn in records]


def _evidence(cov: dict) -> FileEvidence:
    """A line is hit when any statement starting on it ran; each branch arm counts once."""
    hits = cov.get("s", {})
    lines: dict[int, bool] = {}
    for sid, line in _stmt_lines(cov).items():
        lines[line] = lines.get(line, False) or hits.get(sid, 0) > 0
    branches = [(line, len(cov["b"][bid]), sum(1 for h in cov["b"][bid] if h > 0))
                for bid, line in _branch_lines(cov).items()]
    return _ev([ln for ln, ran in lines.items() if ran],
               [ln for ln, ran in lines.items() if not ran], branches)


def _only(cov: dict, group: str, key: str) -> dict:
    """The file entry with one statement or one branch left in it."""
    counters = {"statementMap": "s", "branchMap": "b"}
    single = dict(cov)
    for mapped in counters:
        keep = key if mapped == group else None
        single[mapped] = {k: v for k, v in cov.get(mapped, {}).items() if k == keep}
        single[counters[mapped]] = {k: v for k, v in cov.get(counters[mapped], {}).items()
                                    if k == keep}
    return single


def _istanbul_owner(cov: dict, group: str, key: str) -> tuple[int, int] | None:
    total = "statements_total" if group == "statementMap" else "branches_total"
    owners = [(fn.start, fn.end) for fn in coverage_istanbul._file_coverage(_only(cov, group, key))
              if getattr(fn, total)]
    return owners[0] if owners else None


def _multi_statement_lines(cov: dict) -> set[int]:
    lines = list(_stmt_lines(cov).values())
    return {line for line in lines if lines.count(line) > 1}


def _lines_where_spans_meet(cov: dict) -> set[int]:
    """The lines of the file's inventory spans where two spans meet: an inner
    span starts or ends on the line while an outer one holds it. Only there can
    istanbul, which reads columns, and the join, which reads lines, pick
    different owners: on any other line a column sits in exactly the spans its
    line does. Every span is inner to one: an enclosing function, or the file's
    top level, which both sides of the differential read as owner None. So a
    module-level `const arrow = (value) => value * 2;` meets on its line."""
    return {line for row in _as_rows(_istanbul_records(cov)) for line in (row.start, row.end)}


def _listed_off_the_meetings(cov: dict, lines: list[int], branch_ids: list[str]) -> list:
    """The listed parted lines, and the listed branch ids, that sit on a line
    where no two spans meet: a parting the rule does not explain."""
    meets = _lines_where_spans_meet(cov)
    branch_lines = _branch_lines(cov)
    return ([line for line in lines if line not in meets]
            + [bid for bid in branch_ids if branch_lines[bid] not in meets])


def _differential(cov: dict) -> tuple[list, list]:
    """(statement lines whose owners part, branches whose owners part)."""
    rows = _as_rows(_istanbul_records(cov))
    parted_lines, parted_branches = [], []
    for sid, line in _stmt_lines(cov).items():
        if _istanbul_owner(cov, "statementMap", sid) != _owner_per_line(rows, line):
            parted_lines.append(line)
    for bid, line in _branch_lines(cov).items():
        if _istanbul_owner(cov, "branchMap", bid) != _owner_per_line(rows, line):
            parted_branches.append(bid)
    return sorted(set(parted_lines)), sorted(parted_branches)


def _branch_totals(records: list[FnCoverage]) -> dict:
    totals: dict[tuple[int, int], tuple[int, int]] = {}
    for fn in records:
        total, covered = totals.get((fn.start, fn.end), (0, 0))
        totals[(fn.start, fn.end)] = (total + fn.branches_total, covered + fn.branches_covered)
    return {span: counts for span, counts in totals.items() if counts[0]}


def _statement_gap(istanbul: list[FnCoverage], joined: list[FnCoverage]) -> dict:
    """Per span, the statements istanbul counts past the lines the join counts."""
    gap: Counter = Counter()
    for fn in istanbul:
        gap[(fn.start, fn.end)] += fn.statements_total
    for fn in joined:
        gap[(fn.start, fn.end)] -= fn.statements_total
    return {span: count for span, count in gap.items() if count}


def _extra_statements(cov: dict, rows: list[InventoryRow]) -> dict:
    """Per span, the statements its multi-statement lines hold past their first:
    istanbul counts a line holding n statements n times, the join once."""
    extra: Counter = Counter()
    for line, count in Counter(_stmt_lines(cov).values()).items():
        owner = _owner_per_line(rows, line)
        if count > 1 and owner is not None:
            extra[owner] += count - 1
    return dict(extra)


def _without(cov: dict, parted_lines: list[int], parted_branches: list[str]) -> dict:
    """The file entry with its listed partings set aside: each parted branch
    and every statement on a parted line."""
    kept = copy.deepcopy(cov)
    for bid in parted_branches:
        kept["branchMap"].pop(bid)
        kept["b"].pop(bid)
    for sid, line in _stmt_lines(cov).items():
        if line in parted_lines:
            kept["statementMap"].pop(sid)
            kept["s"].pop(sid)
    return kept


def _totals_agree(cov: dict, parted_lines: list[int], parted_branches: list[str]) -> None:
    """With the listed partings set aside, each span's branch totals agree, and
    its statement totals differ only by what its multi-statement lines hold."""
    cov = _without(cov, parted_lines, parted_branches)
    istanbul = _istanbul_records(cov)
    rows = _as_rows(istanbul)
    joined = join_evidence(rows, _evidence(cov))
    assert _branch_totals(joined) == _branch_totals(istanbul)
    assert _statement_gap(istanbul, joined) == _extra_statements(cov, rows)


# Where line evidence cannot follow istanbul's columns, each listed by hand.
# declared-arrows: `const double = (value) => value * 2;` holds the declaration
# statement (the encloser's, it starts before the arrow) and the arrow's body
# statement on one line, at lines 2 and 5. chosen-arrow: `return flag ? (x) =>
# x : null;` holds the return (choose's) and the arrow's body on line 2, and
# its ternary opens ahead of the arrow, so istanbul gives branch "1" to choose
# and a line can only give it to the arrow on that line.
UNIT_PARTED = {
    "artifact": ([], []),
    "statements": ([], []),
    "declared-arrows": ([2, 5], []),
    "chosen-arrow": ([2], ["1"]),
}


@pytest.mark.parametrize("name", UNIT_FIXTURES)
def test_the_join_attributes_each_unit_fixture_as_istanbul_does(name):
    (cov,) = UNIT_FIXTURES[name].values()
    cov = coverage_istanbul._require_counters(copy.deepcopy(cov))
    parted_lines, parted_branches = _differential(cov)

    assert (parted_lines, parted_branches) == UNIT_PARTED[name]
    assert set(parted_lines) <= _multi_statement_lines(cov)
    assert _listed_off_the_meetings(cov, *UNIT_PARTED[name]) == []
    _totals_agree(cov, parted_lines, parted_branches)


# Every recorded file whose owners part, each parting listed by hand: (statement
# lines, branch ids). A file not named here parts nowhere. Two shapes recur. A
# line holding two or more statements: `const arrow = (value) => value * 2;` on
# shapes line 60 holds the declaration (the encloser's) and the arrow's body. A
# statement or branch that starts on a function's first line ahead of where
# istanbul opens that function: `return values.map((value) => {` on shapes line
# 46 is withCallback's return, and a line can only give it to the callback that
# opens on it. The second shape also parts single-statement lines (the trailing
# comments name them). The conductor's ruling of 2026-10-04 on check 2 admits
# both shapes on a line where two spans meet, and only there: the test below
# holds every listed row to such a line.
SHAPES_ISTANBUL = ([46, 60], [])    # one statement: 46
SHAPES_JSX = ([46, 60, 94], [])     # one statement: 46
SHAPES_V8 = ([60, 86], [])          # one statement: 60, 86
VITEST = {"js/shapes.js": SHAPES_ISTANBUL, "jsx/shapes.jsx": SHAPES_JSX,
          "mjs/shapes.mjs": SHAPES_ISTANBUL, "ts/shapes.ts": SHAPES_ISTANBUL,
          "tsx/shapes.tsx": SHAPES_JSX, "vue/Shapes.vue": SHAPES_ISTANBUL}
RECORDED_PARTED = {
    "c8-12.0.0/call.json": {"mjs/shapes.mjs": SHAPES_V8},
    "c8-12.0.0/idle.json": {"mjs/shapes.mjs": SHAPES_V8},
    "jest-babel-30.5.2/call.json": {"js/shapes.js": SHAPES_ISTANBUL, "ts/shapes.ts": SHAPES_ISTANBUL},
    "jest-babel-30.5.2/idle.json": {"js/shapes.js": SHAPES_ISTANBUL, "ts/shapes.ts": SHAPES_ISTANBUL},
    "jest-v8-30.5.2/call.json": {"js/shapes.js": SHAPES_V8, "ts/shapes.ts": SHAPES_V8},
    "jest-v8-30.5.2/idle.json": {"js/shapes.js": SHAPES_V8, "ts/shapes.ts": SHAPES_V8},
    "negative-counters/coverage-final.json": {
        # one statement: 422, 475, 492
        "src/n01.ts": ([422, 475, 492, 516], ["80", "93"]),
        # one statement: 246
        "src/n03.ts": ([246], ["21"]),
        # one statement: 275, 301, 323
        "src/n06.ts": ([160, 275, 301, 317, 318, 323], []),
        # one statement: 110, 470, 473, 481
        "src/n07.ts": ([110, 351, 470, 473, 481], []),
        "src/n09.ts": ([258], []),
        # one statement: 647, 649, 1900
        "src/n10.ts": ([594, 647, 649, 683, 788, 845, 896, 1835, 1900],
                       ["67", "68", "76", "77"]),
        # one statement: all seven
        "src/n11.ts": ([1140, 1174, 1219, 1418, 1431, 1443, 1469], []),
        # one statement: 193, 199
        "src/n14.ts": ([26, 193, 199], []),
        # one statement: 152, 213, 228, 250, 251
        "src/n17.ts": ([23, 114, 152, 166, 213, 228, 250, 251], ["28"]),
        # one statement: 377, 553, 585, 913, 1009
        "src/n18.ts": ([377, 382, 389, 390, 553, 585, 913, 983, 1005, 1009, 1179, 1189,
                        1234], []),
        "src/n19.ts": ([13, 87], []),
        # one statement: 93, 133, 194, 304
        "src/n21.ts": ([68, 74, 93, 118, 120, 133, 142, 194, 266, 285, 304], []),
        # one statement: every line but 90
        "src/n22.ts": ([29, 90, 98, 109, 121, 156, 166, 171, 180, 215, 230, 235, 245, 251,
                        256, 263, 271], []),
        "src/n23.ts": ([49], []),
        # one statement: 127, 422, 798, 1301
        "src/n24.ts": ([127, 201, 237, 422, 514, 798, 1301], ["101"]),
    },
    "nyc-18.0.0/call.json": {"js/shapes.js": SHAPES_ISTANBUL},
    "nyc-18.0.0/idle.json": {"js/shapes.js": SHAPES_ISTANBUL},
    "vitest-istanbul-5.0.1/call.json": VITEST,
    "vitest-istanbul-5.0.1/idle.json": VITEST,
    "vitest-v8-5.0.1/call.json": VITEST,
    "vitest-v8-5.0.1/idle.json": VITEST,
}


@pytest.mark.parametrize("recording", RECORDED_PARTED)
def test_every_listed_parting_sits_where_two_spans_meet(recording):
    """A listed line or branch on a line where no two spans meet is a parting
    the rule does not explain, and fails here whether or not it parts."""
    artifact = json.loads((RECORDINGS / recording).read_bytes())
    for key, (lines, branch_ids) in RECORDED_PARTED[recording].items():
        assert _listed_off_the_meetings(artifact[key], lines, branch_ids) == [], key


def _file_branch_totals(records: list[FnCoverage]) -> tuple[int, int]:
    return (sum(fn.branches_total for fn in records), sum(fn.branches_covered for fn in records))


@pytest.mark.parametrize("path", RECORDED, ids=lambda path: f"{path.parent.name}-{path.name}")
def test_the_join_attributes_every_recorded_istanbul_file_as_istanbul_does(path):
    """Owners part exactly where RECORDED_PARTED lists, and nowhere else. A
    file's branch totals agree with every branch kept. Once the partings are
    set aside, each span's branch totals agree and its statement totals differ
    only by what its multi-statement lines hold."""
    artifact = json.loads(path.read_bytes())
    parted = {}
    for key, cov in artifact.items():
        coverage_istanbul._admit_hits(cov)
        lines, branches = _differential(cov)
        if lines or branches:
            parted[key] = (lines, branches)
        istanbul = _istanbul_records(cov)
        joined = join_evidence(_as_rows(istanbul), _evidence(cov))
        assert _file_branch_totals(joined) == _file_branch_totals(istanbul), key
        _totals_agree(cov, lines, branches)
    assert parted == RECORDED_PARTED.get(f"{path.parent.name}/{path.name}", {})


# --- variations: memory ------------------------------------------------------

FILES, LINES = 31_459, 200


def _synthetic_tree() -> dict[str, list[FileEvidence]]:
    """One lane's evidence for a 31,459-file tree at 200 lines per file: three
    lines in four hit, and a two-arm branch every tenth line."""
    hit = array("I", [line for line in range(1, LINES + 1) if line % 4])
    missed = array("I", [line for line in range(1, LINES + 1) if not line % 4])
    return {f"src/pkg{index // 500}/file{index}.ts": [FileEvidence(
        array("I", hit), array("I", missed),
        tuple((line, 2, line % 3) for line in range(1, LINES + 1, 10)))]
        for index in range(FILES)}


def test_the_evidence_of_a_31459_file_tree_is_measured(capsys):
    """The lcov reader sets its memory budget from this number."""
    tracemalloc.start()
    try:
        tree = _synthetic_tree()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    with capsys.disabled():
        print(f"\nevidence_by_path for {FILES:,} files at {LINES} lines: "
              f"tracemalloc peak {peak / 2**20:.1f} MiB")
    assert len(tree) == FILES
    assert peak < 512 * 2**20
