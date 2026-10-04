"""CRAP scoring and the coverage join. Pure.

The inventory is the master list: every function gets a scored row. Coverage
joins by path plus span overlap. Flags never conflate: measured (a lane's
artifact spoke about the file), untested (lane covers the scope, artifact
silent on this function), excluded (the artifact measured the file and was
told to leave this function out), no-lane (no lane covers the scope at all),
cc-only (the scope declares coverage_optional, so no coverage number can
exist). The four zero-coverage flags all score cov=0; the flag says whether
the missing number is a testing gap, a tooling gap, or by design, and the two
by-design flags score crap = ccn.
"""
from __future__ import annotations

from array import array
from collections.abc import Iterable, Iterator, Mapping
import heapq
import math
import sys
from typing import NamedTuple

from .covstream import REGENERATE
from .errors import ToolError
from .keys import require_unambiguous
from .records import decode_record, encode_record, record_lines
from .snapshot import InventoryRow


class FnCoverage(NamedTuple):
    """One function's coverage as a producer measured it, the record every
    coverage reader hands the join."""
    name: str
    start: int
    end: int
    invoked: bool
    branches_total: int
    branches_covered: int
    statements_total: int = 0
    statements_covered: int = 0
    # The producer was told to leave the function out, so no test moves its number.
    excluded: bool = False
    # The reader lists every function the producer measured in this file, so a
    # function missing from the list was left out on purpose.
    full_listing: bool = False

    @property
    def coverage(self) -> float:
        if self.branches_total > 0:
            return self.branches_covered / self.branches_total
        if self.statements_total > 0:
            return self.statements_covered / self.statements_total
        return 1.0 if self.invoked else 0.0


def coverage_count(value: object, field: str) -> int:
    """Admit a producer's count before attribution or ratio arithmetic."""
    if type(value) is float and value.is_integer():
        value = int(value)
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer count, got {value!r}; "
                         f"{REGENERATE}")
    return value


# --- the innermost-span rule ---------------------------------------------------
# span_owners reads a span as a list whose [2] is its end line, whose `opens`
# index holds the (line, column) position it opens at, and whose [SPAN_FINISH]
# holds the (line, column) position it closes at.
SPAN_FINISH = 10


def _push_started(heap: list, ordered: list[list], nxt: int, point: tuple, opens: int) -> int:
    while nxt < len(ordered) and ordered[nxt][opens] <= point:
        span = ordered[nxt]
        line, column = span[opens]
        heapq.heappush(heap, (span[2] - line, -line, -column, nxt, span))
        nxt += 1
    return nxt


def _drop_ended(heap: list, point: tuple) -> None:
    """Discard spans that closed before this point. Safe to do lazily and only at
    the top: query points only increase, so anything popped here can never
    contain a later point either."""
    while heap and heap[0][-1][SPAN_FINISH] < point:
        heapq.heappop(heap)


def span_owners(fn_spans: list[list], points: set[tuple[int, int]],
                opens: int) -> dict[tuple[int, int], list | None]:
    """position -> innermost containing span, each span opening at its `opens`
    position. A hit inside a nested function belongs to that function, never
    to its encloser — else the nested one reads through its encloser and the
    encloser answers for lines it can't fix. A counter that starts on a
    function's line but ahead of where that function opens is the encloser's.

    Sweeping spans by start into a heap keyed (lines, -start, index) settles
    that in O((F + Q) log F) instead of a scan per query. The index term is
    load-bearing: it is the sorted position, so an exact tie on (lines, -start)
    resolves to the span the old linear scan met first."""
    ordered = sorted(fn_spans, key=lambda s: s[opens])
    heap: list[tuple] = []
    owners: dict[tuple[int, int], list | None] = {}
    nxt = 0
    for point in sorted(points):
        nxt = _push_started(heap, ordered, nxt, point, opens)
        _drop_ended(heap, point)
        owners[point] = heap[0][-1] if heap else None
    return owners


# --- the coverage evidence join -------------------------------------------------
# A coverage format with no function records (lcov DA and BRDA, Go coverprofile
# blocks, Cobertura and JaCoCo lines) hands per-file line and branch evidence.
# score gives each line and branch to the innermost inventory span holding it
# and builds the records the readers that list functions build themselves.

class FileEvidence(NamedTuple):
    """One file's coverage evidence from one lane.

    hit_lines and missed_lines are compact sorted arrays of line numbers
    (array("I")), and branches holds (line, total, covered) triples. hit_lines
    is None when the adapter keeps its own function records (coverage.py,
    istanbul): the join then builds nothing, and missed_lines still feeds the
    dead-line fold."""
    hit_lines: array | None
    missed_lines: array
    branches: tuple[tuple[int, int, int], ...] = ()


# A joined span keeps FnCoverage's eight counted fields first, as the istanbul
# adapter's mutable span does, opens at its start line's first column and
# closes at its end line's last.
_INVOKED, _B_TOTAL, _B_COV, _S_TOTAL, _S_COV = 3, 4, 5, 6, 7
_LINE_OPENS = 8
_COUNTED = slice(0, 8)


class _JoinedCoverage(FnCoverage):
    """A record the join built on one inventory span, which it speaks for
    alone. A span that owns no evidence has no record and scores untested, so
    the selection never lends it the record of a span it holds or sits in."""
    __slots__ = ()


def _speaks_for(row, fn: FnCoverage) -> bool:
    """A producer record joins any row it overlaps; a joined one, only the rows
    on its own span."""
    return type(fn) is not _JoinedCoverage or (fn.start, fn.end) == (row.start, row.end)


def _evidence_spans(rows) -> list[list]:
    """One mutable span per distinct (start, end) line span among the rows, in
    line order, named for the first row on it. Rows on a shared span get one
    record, which the shared-span floor then refuses for all of them."""
    spans: dict[tuple[int, int], list] = {}
    for row in rows:
        spans.setdefault((row.start, row.end), [
            row.long_name, row.start, row.end, False, 0, 0, 0, 0,
            (row.start, 0), None, (row.end, sys.maxsize)])
    return [spans[key] for key in sorted(spans)]


def _evidence_points(evidence: FileEvidence) -> set[tuple[int, int]]:
    lines = {*evidence.hit_lines, *evidence.missed_lines}
    lines.update(line for line, _, _ in evidence.branches)
    return {(line, 0) for line in lines}


def _attach_lines(owners: dict, lines, hit: int) -> None:
    """Count each line as a statement of the span that owns it, run when hit."""
    for line in lines:
        span = owners[(line, 0)]
        if span is not None:
            span[_S_TOTAL] += 1
            span[_S_COV] += hit
            span[_INVOKED] = span[_INVOKED] or hit == 1


def _attach_triples(owners: dict, branches) -> None:
    for line, total, covered in branches:
        span = owners[(line, 0)]
        if span is not None:
            span[_B_TOTAL] += total
            span[_B_COV] += covered


def join_evidence(rows, evidence: FileEvidence) -> list[FnCoverage]:
    """One FnCoverage per inventory span of this file's rows that owns at least
    one evidence line or branch, each line and branch given to its innermost
    span by span_owners. Branch counts come from the owned triples, statement
    counts from the owned hit and missed lines, and the span was invoked when
    any owned line was hit. A line outside every span is dropped. An adapter
    that keeps its own records (hit_lines None) joins nothing."""
    if evidence.hit_lines is None:
        return []
    spans = _evidence_spans(rows)
    owners = span_owners(spans, _evidence_points(evidence), _LINE_OPENS)
    _attach_lines(owners, evidence.hit_lines, 1)
    _attach_lines(owners, evidence.missed_lines, 0)
    _attach_triples(owners, evidence.branches)
    return [_JoinedCoverage(*span[_COUNTED]) for span in spans
            if span[_S_TOTAL] or span[_B_TOTAL]]


# A ratchet mark holds a CRAP score at this many decimal places, and a
# ranking compares scores and moves at them, so equal ones tie.
CRAP_PLACES = 4


def crap(ccn: int, cov: float) -> float:
    """ccn^2 * (1 - cov)^3 + ccn, cubed by two products. IEEE 754 rounds a
    product correctly on every platform and leaves pow() to each libm: through
    `** 3`, Windows and Linux parted in the last bit on some inputs, and a
    score on an exact 4 dp tie printed the side pow() fell on."""
    uncovered = 1.0 - cov
    return ccn * ccn * (uncovered * uncovered * uncovered) + ccn


# CRAP(18, 2/3) is 30 exactly, and its double is 30.000000000000004. Over ccn 1 to
# 60 and every coverage fraction up to 400ths, a CRAP double strays at most 5.2
# units in its last place from the exact value, while an exact CRAP that is not a
# whole-number ceiling misses it by at least 1/total^3: 1/64,000,000 at
# CRAP(1, 399/400). A score within a relative 2^-48 of its ceiling, 16 to 32 units
# in the ceiling's last place, is the ceiling. The store's rollup SQL multiplies
# by the same factor.
AT_CEILING = 1 + 2 ** -48


def over_ceiling(score: float, ceiling: int) -> bool:
    """README's `crap > ceiling`, decided for the exact CRAP rather than its double."""
    return score > ceiling * AT_CEILING


def crap_load(scores: Iterable[float]) -> float:
    """The CRAP load: the exact sum of the scores, rounded once (math.fsum).

    The builtin sum() adds left to right on Python 3.11 and SQLite's SUM() in
    scan order before 3.43, so a load a hair above a 2 dp tie printed one way
    or the other by row order. Every surface that prints a load sums with this."""
    return math.fsum(scores)


def flagged_crap(ccn: int, cov: float, flag: str) -> float:
    """The CRAP a row scores under its flag. cc-only and excluded follow the
    pre-commit hook's rule: crap IS ccn, since no coverage number can exist for
    them, and feeding cov=0 through the formula would read the absent number
    as 0."""
    return float(ccn) if flag in _SCORED_ON_CCN else crap(ccn, cov)


_GRADES = ((0.02, "A"), (0.05, "B"), (0.10, "C"), (0.20, "D"))


def grade(over_target: int, total: int) -> str:
    """One letter for over-target density; A+ is reserved for zero debt."""
    if over_target == 0:
        return "A+"
    ratio = over_target / total
    for bound, letter in _GRADES:
        if ratio < bound:
            return letter
    return "F"


class ScoredRow(NamedTuple):
    scope: str
    path: str
    long_name: str
    start: int
    end: int
    ccn_std: int
    ccn_mod: int
    ccn: int
    nloc: int
    params: int
    nesting: int
    cov: float
    flag: str
    crap: float
    remedy: str
    cognitive: int = 0  # Sonar-spec cognitive complexity; reporting only, never gated
    occurrence: int = 0  # Positive source order on one start line; 0 is legacy
    inline_body: int = 0  # FunctionRecord.inline_body: stored, never exported


_FIELD_TYPES = (str, str, str, int, int, int, int, int, int, int, int,
                float, str, float, str, int, int)
# The export's columns end at occurrence, as the inventory export's do
# (snapshot.INVENTORY_COLUMNS), and brief's `scored` object publishes the same
# seventeen.
SCORED_COLUMNS = ScoredRow._fields[:ScoredRow._fields.index("inline_body")]
_SCORED_HEADER = "\t".join(SCORED_COLUMNS)
_SCORED_HEADERS = {"\t".join(SCORED_COLUMNS[:-1]): 16, _SCORED_HEADER: 17}


def scored_tsv_lines(rows: list[ScoredRow]) -> Iterator[str]:
    """Header then one newline-terminated line per row. With no rows the header
    is the empty string, so the file stays the single newline it always was."""
    yield (_SCORED_HEADER if rows else "") + "\n"
    width = len(SCORED_COLUMNS)
    for r in rows:
        yield encode_record(r[:width]) + "\n"


def parse_scored_row(line: str) -> ScoredRow:
    """One exported line back to a row. str() of every field round-trips through
    its own constructor, floats included, so the re-emitted bytes are identical."""
    parts = decode_record(line)
    return _scored_parts(parts)


def _scored_parts(parts: list[str]) -> ScoredRow:
    if len(parts) not in (16, 17):
        raise ValueError(f"scored row has {len(parts)} fields, expected 16 or 17: {parts!r}")
    row = ScoredRow(*[cast(part) for cast, part in zip(_FIELD_TYPES, parts)])
    if row.occurrence < 0:
        raise ValueError("scored occurrence must be nonnegative")
    return row


def parse_scored_tsv(text: str) -> list[ScoredRow]:
    lines = [line for line in record_lines(text) if line.strip()]
    if not lines:
        return []
    count = _SCORED_HEADERS.get(lines[0])
    if count is not None:
        lines = lines[1:]
    return [_scored_line(line, count) for line in lines]


def _scored_line(line: str, count: int | None) -> ScoredRow:
    parts = decode_record(line)
    fields = len(parts)
    if count is not None and fields != count:
        raise ValueError(f"scored row has {fields} fields, expected {count}")
    return _scored_parts(parts)


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start) + 1)


def _best_match(row: InventoryRow, candidates: list[FnCoverage]) -> FnCoverage | None:
    # Exact start beats raw overlap, and on remaining ties the tightest span wins:
    # a nested function must join its own entry, never its enclosing function's
    # (an enclosing match would inherit the parent's coverage and understate risk).
    # Identical-span twins (two lanes measuring the same file) keep the BETTER
    # measurement — the true union of branch hits is at least the max, and lane
    # declaration order must never change a score.
    best, best_key = None, None
    for fn in candidates:
        o = _overlap(row.start, row.end, fn.start, fn.end)
        if o <= 0 or not _speaks_for(row, fn):
            continue
        key = (fn.start == row.start, o, -(fn.end - fn.start), fn.coverage)
        if best_key is None or key > best_key:
            best, best_key = fn, key
    return best


def remedy(ccn: int, score: float, ceiling: int, shared_span: bool = False) -> str:
    """The first thing that can lower this score. A function sharing its source
    line span with another scores as uncovered whatever its tests do, so
    add-tests there is advice nobody can follow: splitting the definitions is,
    and the run after the split says whether tests are still owed."""
    if ccn > ceiling:
        return "decompose"
    if not over_ceiling(score, ceiling):
        return "ok"
    return "split-lines" if shared_span else "add-tests"


# No coverage number can exist for these, by the scope's choice or the
# producer's: crap IS ccn, the pre-commit hook's rule.
_SCORED_ON_CCN = ("cc-only", "excluded")


def _finish(row, cov: float, flag: str, *, target: int, scope_targets,
            shared_span: bool = False) -> ScoredRow:
    # cc-only and excluded score crap = ccn, so remedy can only answer ok or
    # decompose. Feeding cov=0 through the formula would say add-tests about
    # code no test can reach.
    score = flagged_crap(row.ccn, cov, flag)
    ceiling = scope_targets.get(row.scope, target) if scope_targets else target
    # Positional, and NOT *row: cognitive and occurrence trail both tuples with four
    # fields between, so splicing the row in whole lands it in cov. Building
    # this row is a third of the join's cost at 140,922 rows — **row._asdict()
    # built a throwaway dict per row and looked every field up by name.
    return ScoredRow(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
                     row[8], row[9], row[10],
                     cov, flag, score, remedy(row[7], score, ceiling, shared_span), row[11], row[12],
                     row[13])


def _nearest_overlay(row, candidates) -> list:
    if not candidates:
        return []
    start = min(candidates, key=lambda c: abs(c.start - row.start)).start
    return [c for c in candidates if c.start == start]


def _same_occurrence(row, candidates):
    if not row.occurrence:
        return None
    return next((c for c in candidates if c.occurrence == row.occurrence), None)


def _overlay_match(row, candidates, unique: bool):
    exact = _same_occurrence(row, candidates)
    if exact is not None:
        return exact
    if unique and len({c.occurrence for c in candidates}) == 1:
        return candidates[0]
    return None


def _named_overlay_cov(row, by_key: dict, positions: dict) -> tuple[float, str] | None:
    """The cov and flag of the baseline row this function joins by name, or None.

    Search only this signature's candidates, keeping the baseline order for
    equal-distance starts. Occurrence separates siblings at that start.
    """
    named = _nearest_overlay(row, by_key.get((row.path, row.long_name)))
    unique = len(positions[(row.path, row.long_name, row.start)]) == 1
    match = _overlay_match(row, named, unique)
    return None if match is None else (match.cov, match.flag)


class _OverlayIndex(NamedTuple):
    """The baseline rows a fresh function can join by name: the measured and
    excluded ones, whose number and flag it takes, and the untested ones, whose
    verdict it keeps."""
    measured: dict
    untested: dict
    positions: dict


def _overlay_index(rows, baseline_scored) -> _OverlayIndex:
    by_flag: dict[str, dict] = {"measured": {}, "untested": {}}
    for r in baseline_scored:
        into = "measured" if r.flag in _JOINED_FLAGS else r.flag
        if into in by_flag:
            by_flag[into].setdefault((r.path, r.long_name), []).append(r)
    return _OverlayIndex(by_flag["measured"], by_flag["untested"], _overlay_positions(rows))


def _joined_overlay_cov(row, index: _OverlayIndex) -> tuple[float, str] | None:
    """The baseline's number for this function, else the untested verdict the
    baseline gave it, else None: the baseline holds no row this function joins,
    so nothing measured it (a function added or renamed since that run)."""
    return (_named_overlay_cov(row, index.measured, index.positions)
            or _named_overlay_cov(row, index.untested, index.positions))


def _overlay_positions(rows) -> dict:
    positions: dict[tuple, set[int]] = {}
    for row in rows:
        positions.setdefault((row.path, row.long_name, row.start), set()).add(row.occurrence)
    return positions


# The flags of rows no coverage artifact joins: no lane covers the scope, or the
# scope asks for none. Their cov of 0.0 stands in for a number nobody measured.
UNJOINED = ("no-lane", "cc-only")


def unjoined(flag: str) -> bool:
    """Whether a row with this flag has no measurement behind its cov."""
    return flag in UNJOINED


def _cov_without_join(row, lane_scopes: set, cc_only_scopes) -> tuple[float, str] | None:
    """The verdict for a row no coverage artifact can speak about, else None.

    coverage_optional is checked FIRST: such a scope needs no lane, so the
    no-lane fallback would otherwise hide it behind a tooling gap it does
    not have.
    """
    if row.scope in cc_only_scopes:
        return 0.0, "cc-only"
    if row.scope not in lane_scopes:
        return 0.0, "no-lane"
    return None


def _start_index(coverage_by_path: dict) -> dict[str, dict[int, list[FnCoverage]]]:
    """path -> {start line: the candidates declaring it}, in candidate order.

    91.5% of joinable rows share a start line with some candidate, and the scan
    that found it compared every candidate on the path: 936,818 pairwise
    comparisons on the consumer repo. Bucket order is the candidates' own order, which
    is what keeps a dead-even tie resolving to the same twin.
    """
    index = {}
    for path, candidates in coverage_by_path.items():
        buckets: dict[int, list[FnCoverage]] = {}
        for fn in candidates:
            buckets.setdefault(fn.start, []).append(fn)
        index[path] = buckets
    return index


def _best_exact(row, bucket) -> FnCoverage | None:
    """The winner among candidates whose start EQUALS the row's, or None when
    none of them overlaps it.

    _best_match's key leads with (fn.start == row.start): True here and False
    for every candidate outside this bucket, so a winner here is the winner
    over the whole path. The remaining terms are that key's tail, and
    max(a_start, b_start) is row.start by construction.
    """
    best, best_key = None, None
    for fn in bucket:
        o = min(row.end, fn.end) - row.start + 1
        if o <= 0 or not _speaks_for(row, fn):
            continue
        key = (o, -(fn.end - fn.start), fn.coverage)
        if best_key is None or key > best_key:
            best, best_key = fn, key
    return best


def _span_join_cov(row, coverage_by_path: dict, start_index: dict) -> tuple[float, str]:
    candidates = coverage_by_path.get(row.path)
    if candidates is None:
        return 0.0, "untested"
    # The bucket answers for most rows; the scan is the fallback for a row that
    # starts where no candidate does, or whose bucket overlaps it nowhere.
    match = _best_exact(row, start_index[row.path].get(row.start, ()))
    if match is None:
        match = _best_match(row, candidates)
    if match is None:
        return _unlisted(candidates)
    return (0.0, "excluded") if match.excluded else (match.coverage, "measured")


def _unlisted(candidates: list) -> tuple[float, str]:
    """A function the artifact does not list in a file it measured.

    istanbul lists every function it instruments, and an `istanbul ignore
    next` or `v8 ignore next` hint drops one from that list, so a file whose
    full listing names others left this one out on purpose. A file listed
    with no functions at all proves nothing, and neither does a hand-built
    entry with no statements, nor coverage.py's list, which folds two defs of
    one name into one region: their gaps stay untested.
    """
    if any(fn.full_listing for fn in candidates):
        return 0.0, "excluded"
    return 0.0, "untested"


def overlay_stale_coverage(
    rows: list[InventoryRow],
    baseline_scored: list["ScoredRow"],
    *,
    lane_scopes: set[str],
    target: int = 6,
    scope_targets: dict[str, int] | None = None,
    cc_only_scopes: frozenset[str] = frozenset(),
    baseline_run_id: int | None = None,
    unjoined: set | None = None,
) -> list[ScoredRow]:
    """Rescore fresh complexity against a BASELINE run's coverage.

    Joins by function name, nearest start among same-name twins, and occurrence
    when callbacks share a line. A renamed or new function joins NOTHING —
    a span join here would hand it a neighbour's stale number and mislead
    the preview. A function on a span another one shares, or a Python def on
    its own def line that the baseline did not read excluded, scores as
    uncovered, as score_rows scores it, so the preview never passes what the
    next coverage run fails. Coverage values are
    the baseline's; the caller labels them stale. A legacy-identity refusal
    names BASELINE_RUN_ID, the run the baseline rows came from.

    A function nothing measured still scores at cov 0.0 and flag `untested`,
    the values this preview has always given it. `unjoined`, when passed,
    collects each such returned row so a caller can say so beside the number:
    a row whose scope no lane covers or asks for none, and a row the baseline
    holds nothing to join by name.
    """
    require_unambiguous(rows)
    require_unambiguous(baseline_scored, run_id=baseline_run_id)
    index = _overlay_index(rows, baseline_scored)
    shared = _shared_source_spans(rows, lane_scopes, cc_only_scopes)
    scored = []
    for row in rows:
        verdict = _cov_without_join(row, lane_scopes, cc_only_scopes)
        on_shared = _on_shared_span(row, verdict, shared)
        joined = verdict or _floored_overlay_cov(row, shared, index)
        cov, flag = joined or (0.0, "untested")
        scored.append(_finish(row, cov, flag, target=target, scope_targets=scope_targets,
                              shared_span=on_shared))
        _collect_unjoined(unjoined, scored[-1], verdict is not None or joined is None)
    return scored


def _collect_unjoined(found: set | None, row: ScoredRow, stand_in: bool) -> None:
    if found is not None and stand_in:
        found.add(row)


# The flags a baseline row carries when a lane's artifact spoke about it.
_JOINED_FLAGS = ("measured", "excluded")


def _floored_overlay_cov(row, shared: dict, index: _OverlayIndex) -> tuple[float, str] | None:
    """Uncovered on a shared span, the floor score_rows gives a measured one.

    Joining by name there handed two functions edited onto one line their old
    separate numbers, and the preview called ok what the coverage run scores
    untested. Tests cannot lift the floor: only splitting the span can. An
    excluded function stays excluded on its own def line, as score_rows
    scores it.
    """
    if (row.path, row.start, row.end) in shared:
        return 0.0, "untested"
    return _def_line_floor(row, _joined_overlay_cov(row, index))


class SharedSpanFold:
    """The source line spans more than one function declares in one run.

    The join cannot tell whose coverage is whose there, so each such function
    scores as uncovered and the run names the spans instead of ending. Filled
    while scoring; the caller reports it once.
    """

    def __init__(self) -> None:
        self.sites: list[list[InventoryRow]] = []

    def add(self, members: list[InventoryRow]) -> None:
        self.sites.append(members)


def _identity(row) -> tuple:
    """What separates two functions on one span: the name, and the occurrence
    that tells sibling callbacks on a line apart."""
    return row.long_name, row.occurrence


def _join_member(members: list, row) -> None:
    """A third function on the span joins its members; another copy of one that
    is already there (the same function in a second scope) does not."""
    if all(_identity(member) != _identity(row) for member in members):
        members.append(row)


def _shared_source_spans(rows, lane_scopes: set, cc_only_scopes) -> dict:
    """span -> the distinct functions declaring it, for spans more than one does."""
    first, collisions = {}, {}
    for row in rows:
        if _cov_without_join(row, lane_scopes, cc_only_scopes) is not None:
            continue
        span = row.path, row.start, row.end
        seen = first.setdefault(span, row)
        if _identity(seen) != _identity(row):
            _join_member(collisions.setdefault(span, [seen]), row)
    return collisions


def _ambiguous_spans(shared: dict, coverage_by_path: dict, start_index: dict) -> dict:
    """The shared spans a measurement would speak about, so the join would hand
    every function on the span one function's number.

    Through 0.7.4 this raised and ended the run. One consumer repo holds 591
    shared spans, 459 of them measured, so a run died on the first one it met
    and the repo never finished a coverage run at all. Refusing the ambiguous
    number is the specified behaviour; ending the run over it was not, the
    shape _note_unanalyzable settled for unreadable files.
    """
    ambiguous = {}
    for span, members in shared.items():
        if _span_join_cov(members[0], coverage_by_path, start_index)[1] == "measured":
            ambiguous[span] = members
    return ambiguous


# The files coverage.py reads. It is the one parser for Python, and it counts
# lines and arcs where istanbul counts calls per function.
_LINE_COUNTED_SUFFIXES = (".py",)


def shares_its_def_line(row) -> bool:
    """A Python function whose body starts on its `def` statement's last line.

    The `def` statement runs when the module is imported, and coverage.py
    counts lines and arcs, not calls: an uncalled `def one(x): return x` reads
    1 of its 2 branches covered, and a called one reads its line run. A body
    that starts on the line a multi-line signature's colon ends, or that goes
    on inside brackets, is read as that same statement. No test can tell a
    called def from an uncalled one there, so the function takes the
    shared-span floor and its remedy, split-lines. The reader marks the shape
    (`inline_body`); a row stored before the mark keeps the one-line test. The
    coverage run, rescore's preview and a rejudged packet all ask this one
    question. istanbul keeps a call counter per function, so a one-line
    TypeScript function keeps its number.
    """
    return ((row.start == row.end or row.inline_body == 1)
            and row.path.lower().endswith(_LINE_COUNTED_SUFFIXES))


def _joined_cov(row, ambiguous: dict, coverage_by_path: dict,
                start_index: dict) -> tuple[float, str]:
    """Uncovered on an ambiguous span or a def line, never the neighbour's
    number: the honest floor for a function whose measurement cannot be told
    from another's."""
    if (row.path, row.start, row.end) in ambiguous:
        return 0.0, "untested"
    return _def_line_floor(row, _span_join_cov(row, coverage_by_path, start_index))


def _def_line_floor(row, joined: tuple[float, str] | None) -> tuple[float, str] | None:
    """Uncovered on a def line, where coverage.py cannot show a call. An
    excluded def was never measured at all, so it keeps its flag. None, no
    baseline row to join, floors the same way on a def line."""
    if (joined is None or joined[1] != "excluded") and shares_its_def_line(row):
        return 0.0, "untested"
    return joined


def _on_shared_span(row, verdict, shared: dict) -> bool:
    """A row some lane measures, on a span it shares with another function or
    with its own def statement. Measured yet or not: tests would only make the
    span measured, and then it scores as uncovered."""
    return verdict is None and (shares_its_def_line(row)
                                or (row.path, row.start, row.end) in shared)


def _rows_by_path(rows, paths) -> dict[str, list[InventoryRow]]:
    by_path: dict[str, list[InventoryRow]] = {}
    for row in rows:
        if row.path in paths:
            by_path.setdefault(row.path, []).append(row)
    return by_path


def _with_evidence(rows, coverage_by_path: dict, evidence_by_path: Mapping | None) -> dict:
    """The candidates with each lane's joined records after the producer
    records on that path. Every lane joins on its own: hits are never unioned,
    and on identical spans the selection keeps the better record."""
    if not evidence_by_path:
        return coverage_by_path
    by_path = _rows_by_path(rows, evidence_by_path)
    merged = dict(coverage_by_path)
    for path, lanes in evidence_by_path.items():
        joined = _joined_records(by_path.get(path, ()), lanes)
        if joined:
            merged[path] = [*merged.get(path, ()), *joined]
    return merged


def _joined_records(rows_of_path, lanes) -> list[FnCoverage]:
    return [fn for evidence in lanes for fn in join_evidence(rows_of_path, evidence)]


def score_rows(
    rows: list[InventoryRow],
    coverage_by_path: dict[str, list[FnCoverage]],
    *,
    lane_scopes: set[str],
    target: int = 6,
    scope_targets: dict[str, int] | None = None,
    cc_only_scopes: frozenset[str] = frozenset(),
    shared_spans: SharedSpanFold | None = None,
    evidence_by_path: Mapping[str, list[FileEvidence]] | None = None,
) -> list[ScoredRow]:
    """evidence_by_path maps a path to one FileEvidence per lane. Each lane's
    joined records join that path's candidates before the selection runs, so a
    joined record and a producer record are judged by one rule."""
    coverage_by_path = _with_evidence(rows, coverage_by_path, evidence_by_path)
    start_index = _start_index(coverage_by_path)
    shared = _shared_source_spans(rows, lane_scopes, cc_only_scopes)
    ambiguous = _ambiguous_spans(shared, coverage_by_path, start_index)
    for members in ambiguous.values():
        if shared_spans is not None:
            shared_spans.add(members)
    scored = []
    for r in rows:
        verdict = _cov_without_join(r, lane_scopes, cc_only_scopes)
        cov, flag = verdict or _joined_cov(r, ambiguous, coverage_by_path, start_index)
        scored.append(_finish(r, cov, flag, target=target, scope_targets=scope_targets,
                              shared_span=_on_shared_span(r, verdict, shared)))
    return scored
