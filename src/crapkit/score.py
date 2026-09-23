"""CRAP scoring and the coverage join. Pure.

The inventory is the master list: every function gets a scored row. Coverage
joins by path plus span overlap. Flags never conflate: measured (a lane's
artifact spoke about the file), untested (lane covers the scope, artifact
silent on this function), no-lane (no lane covers the scope at all), cc-only
(the scope declares coverage_optional, so no coverage number can exist).
The three zero-coverage flags all score cov=0; the flag says whether the
missing number is a testing gap, a tooling gap, or by design.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple

from .coverage_istanbul import FnCoverage
from .errors import ToolError
from .keys import require_unambiguous
from .records import decode_record, encode_record, record_lines
from .snapshot import InventoryRow


def crap(ccn: int, cov: float) -> float:
    return ccn * ccn * (1.0 - cov) ** 3 + ccn


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
        if o <= 0:
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
    if score <= ceiling:
        return "ok"
    return "split-lines" if shared_span else "add-tests"


def _finish(row, cov: float, flag: str, *, target: int, scope_targets,
            shared_span: bool = False) -> ScoredRow:
    # cc-only is the pre-commit hook's rule: crap IS ccn, so remedy can only
    # answer ok or decompose. Feeding it cov=0 through the formula would say
    # add-tests about code no test can reach.
    score = float(row.ccn) if flag == "cc-only" else crap(row.ccn, cov)
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


def _named_overlay_cov(row, by_key: dict, positions: dict) -> tuple[float, str]:
    # Search only this signature's candidates, keeping the baseline order for
    # equal-distance starts. Occurrence separates siblings at that start.
    named = _nearest_overlay(row, by_key.get((row.path, row.long_name)))
    unique = len(positions[(row.path, row.long_name, row.start)]) == 1
    match = _overlay_match(row, named, unique)
    return (match.cov, "measured") if match is not None else (0.0, "untested")


def _overlay_positions(rows) -> dict:
    positions: dict[tuple, set[int]] = {}
    for row in rows:
        positions.setdefault((row.path, row.long_name, row.start), set()).add(row.occurrence)
    return positions


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
        if o <= 0:
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
        return 0.0, "untested"
    return match.coverage, "measured"


def overlay_stale_coverage(
    rows: list[InventoryRow],
    baseline_scored: list["ScoredRow"],
    *,
    lane_scopes: set[str],
    target: int = 6,
    scope_targets: dict[str, int] | None = None,
    cc_only_scopes: frozenset[str] = frozenset(),
    baseline_run_id: int | None = None,
) -> list[ScoredRow]:
    """Rescore fresh complexity against a BASELINE run's coverage.

    Joins by function name, nearest start among same-name twins, and occurrence
    when callbacks share a line. A renamed or new function joins NOTHING —
    a span join here would hand it a neighbour's stale number and mislead
    the preview. A function on a span another one shares, or a Python def on
    its own def line, scores as uncovered, as score_rows scores it, so the
    preview never passes what the next coverage run fails. Coverage values are
    the baseline's; the caller labels them stale. A legacy-identity refusal
    names BASELINE_RUN_ID, the run the baseline rows came from.
    """
    require_unambiguous(rows)
    require_unambiguous(baseline_scored, run_id=baseline_run_id)
    positions = _overlay_positions(rows)
    by_key: dict[tuple[str, str], list[ScoredRow]] = {}
    for r in baseline_scored:
        if r.flag == "measured":
            by_key.setdefault((r.path, r.long_name), []).append(r)

    shared = _shared_source_spans(rows, lane_scopes, cc_only_scopes)
    scored = []
    for row in rows:
        verdict = _cov_without_join(row, lane_scopes, cc_only_scopes)
        on_shared = _on_shared_span(row, verdict, shared)
        cov, flag = verdict or _floored_overlay_cov(row, on_shared, by_key, positions)
        scored.append(_finish(row, cov, flag, target=target, scope_targets=scope_targets,
                              shared_span=on_shared))
    return scored


def _floored_overlay_cov(row, on_shared: bool, by_key: dict, positions: dict) -> tuple[float, str]:
    """Uncovered on a shared span, the floor score_rows gives a measured one.

    Joining by name there handed two functions edited onto one line their old
    separate numbers, and the preview called ok what the coverage run scores
    untested. Tests cannot lift the floor: only splitting the span can.
    """
    if on_shared:
        return 0.0, "untested"
    return _named_overlay_cov(row, by_key, positions)


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
    if (row.path, row.start, row.end) in ambiguous or shares_its_def_line(row):
        return 0.0, "untested"
    return _span_join_cov(row, coverage_by_path, start_index)


def _on_shared_span(row, verdict, shared: dict) -> bool:
    """A row some lane measures, on a span it shares with another function or
    with its own def statement. Measured yet or not: tests would only make the
    span measured, and then it scores as uncovered."""
    return verdict is None and (shares_its_def_line(row)
                                or (row.path, row.start, row.end) in shared)


def score_rows(
    rows: list[InventoryRow],
    coverage_by_path: dict[str, list[FnCoverage]],
    *,
    lane_scopes: set[str],
    target: int = 6,
    scope_targets: dict[str, int] | None = None,
    cc_only_scopes: frozenset[str] = frozenset(),
    shared_spans: SharedSpanFold | None = None,
) -> list[ScoredRow]:
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
