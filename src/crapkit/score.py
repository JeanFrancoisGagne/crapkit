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


_FIELD_TYPES = (str, str, str, int, int, int, int, int, int, int, int,
                float, str, float, str, int)
_SCORED_HEADER = "\t".join(ScoredRow._fields)
# One %s per field, built once. %s of every field is str() of it, so the bytes
# do not move; a row is a tuple, so it IS the argument list and the per-row
# generator, the join and the concatenation all disappear (179 -> 116 ms on
# 140,922 rows). Derived from _fields rather than written out, so a new column
# cannot leave the template a field short.
_SCORED_ROW = "\t".join(["%s"] * len(ScoredRow._fields)) + "\n"


def scored_tsv_lines(rows: list[ScoredRow]) -> Iterator[str]:
    """Header then one newline-terminated line per row. With no rows the header
    is the empty string, so the file stays the single newline it always was."""
    yield (_SCORED_HEADER if rows else "") + "\n"
    for r in rows:
        yield _SCORED_ROW % r


def parse_scored_row(line: str) -> ScoredRow:
    """One exported line back to a row. str() of every field round-trips through
    its own constructor, floats included, so the re-emitted bytes are identical."""
    parts = line.split("\t")
    if len(parts) != len(_FIELD_TYPES):
        raise ValueError(f"scored row has {len(parts)} fields, expected {len(_FIELD_TYPES)}: {line!r}")
    return ScoredRow(*[cast(part) for cast, part in zip(_FIELD_TYPES, parts)])


def parse_scored_tsv(text: str) -> list[ScoredRow]:
    return [parse_scored_row(line) for line in text.splitlines()
            if line.strip() and line != _SCORED_HEADER]


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


def _remedy(ccn: int, score: float, ceiling: int) -> str:
    if ccn > ceiling:
        return "decompose"
    return "ok" if score <= ceiling else "add-tests"


def _finish(row, cov: float, flag: str, *, target: int, scope_targets) -> ScoredRow:
    # cc-only is the pre-commit hook's rule: crap IS ccn, so _remedy can only
    # answer ok or decompose. Feeding it cov=0 through the formula would say
    # add-tests about code no test can reach.
    score = float(row.ccn) if flag == "cc-only" else crap(row.ccn, cov)
    ceiling = scope_targets.get(row.scope, target) if scope_targets else target
    # Positional, and NOT *row: cognitive is last in both tuples with four
    # fields between, so splicing the row in whole lands it in cov. Building
    # this row is a third of the join's cost at 140,922 rows — **row._asdict()
    # built a throwaway dict per row and looked every field up by name.
    return ScoredRow(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
                     row[8], row[9], row[10],
                     cov, flag, score, _remedy(row[7], score, ceiling), row[11])


def _named_overlay_cov(row, by_key: dict) -> tuple[float, str]:
    # by_key is grouped on (path, long_name) up front: filtering the whole
    # path's candidates per row made this O(rows x candidates) per file
    # (measured 2,998,554 comparisons on a whole-repo rescore). The nearest-start
    # tie-break is unchanged, and min() still keeps the FIRST nearest twin
    # because the group holds the baseline's own order.
    named = by_key.get((row.path, row.long_name))
    if named:
        return min(named, key=lambda c: abs(c.start - row.start)).cov, "measured"
    return 0.0, "untested"


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
) -> list[ScoredRow]:
    """Rescore fresh complexity against a BASELINE run's coverage.

    Joins by function NAME only (edits shift spans, names survive), nearest
    start among same-name twins. A renamed or new function joins NOTHING —
    a span join here would hand it a neighbour's stale number and mislead
    the preview. Coverage values are the baseline's; the caller labels them
    stale.
    """
    by_key: dict[tuple[str, str], list[ScoredRow]] = {}
    for r in baseline_scored:
        if r.flag == "measured":
            by_key.setdefault((r.path, r.long_name), []).append(r)

    scored = []
    for row in rows:
        cov, flag = (_cov_without_join(row, lane_scopes, cc_only_scopes)
                     or _named_overlay_cov(row, by_key))
        scored.append(_finish(row, cov, flag, target=target, scope_targets=scope_targets))
    return scored


def score_rows(
    rows: list[InventoryRow],
    coverage_by_path: dict[str, list[FnCoverage]],
    *,
    lane_scopes: set[str],
    target: int = 6,
    scope_targets: dict[str, int] | None = None,
    cc_only_scopes: frozenset[str] = frozenset(),
) -> list[ScoredRow]:
    start_index = _start_index(coverage_by_path)
    scored = []
    for r in rows:
        cov, flag = (_cov_without_join(r, lane_scopes, cc_only_scopes)
                     or _span_join_cov(r, coverage_by_path, start_index))
        scored.append(_finish(r, cov, flag, target=target, scope_targets=scope_targets))
    return scored
