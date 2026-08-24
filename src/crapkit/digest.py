"""Digest and trend totals. Pure.

The digest speaks only on change (an unchanged week is silence, per the
empty-channel rule) and keeps to a handful of numbers: totals delta, the worst
per-function regressions, improvements, and new over-target functions.
"""
from __future__ import annotations

from typing import NamedTuple

from .score import ScoredRow, grade


class Totals(NamedTuple):
    functions: int
    over_target: int
    crap_load: float
    avg: float
    pct_over: float  # normalized: absolute counts grow with the repo; this does not


class Digest(NamedTuple):
    quiet: bool
    lines: list[str]


def _over_count(rows, target: int, scope_targets) -> int:
    return sum(1 for r in rows if r.crap > (scope_targets or {}).get(r.scope, target))


def totals_from_counts(functions: int, over_target: int, load: float) -> Totals:
    """The rounding rule, in one place. A caller that already has the three sums
    (store.run_totals adds them up inside the scan) must round them exactly the
    way a caller holding the rows does, or the same run reads two ways."""
    return Totals(
        functions=functions,
        over_target=over_target,
        crap_load=round(load, 2),
        avg=round(load / functions, 4) if functions else 0.0,
        pct_over=round(100.0 * over_target / functions, 2) if functions else 0.0,
    )


def totals(rows: list[ScoredRow], *, target: int,
           scope_targets: dict[str, int] | None = None) -> Totals:
    return totals_from_counts(len(rows), _over_count(rows, target, scope_targets),
                              sum(r.crap for r in rows))


def scope_totals(rows: list[ScoredRow], *, target: int,
                 scope_targets: dict[str, int] | None = None) -> dict[str, Totals]:
    by_scope: dict[str, list[ScoredRow]] = {}
    for r in rows:
        by_scope.setdefault(r.scope, []).append(r)
    return {scope: totals(srows, target=target, scope_targets=scope_targets)
            for scope, srows in sorted(by_scope.items())}


def scope_rollup(by_scope: dict[str, Totals]) -> dict[str, dict]:
    """The per-scope block coverage --json and trend --json both publish.

    One shaping in one place: the two commands reach their Totals differently
    (rows in hand vs a GROUP BY), and a second shaping would let the same run
    read two ways depending on which command asked.
    """
    return {scope: {"functions": t.functions, "over_target": t.over_target,
                    "crap_load": t.crap_load, "grade": grade(t.over_target, t.functions)}
            for scope, t in by_scope.items()}


def latest_comparable_pair(runs: list[dict]) -> tuple[dict, dict] | None:
    """The newest pair of runs with IDENTICAL lane sets.

    Comparing a --lane subset run against a full run flips every out-of-subset
    function to no-lane and manufactures phantom regressions; only like-for-like
    pairs digest.
    """
    for i in range(len(runs) - 1, 0, -1):
        cur = runs[i]
        cur_lanes = set(cur["lanes"])
        for j in range(i - 1, -1, -1):
            if set(runs[j]["lanes"]) == cur_lanes:
                return runs[j], cur
    return None


def skipped_runs(runs: list[dict], pair: tuple[dict, dict] | None) -> list[dict]:
    """The runs the pair stepped over: everything after its older half except
    its newer half. Empty exactly when the pair is the newest run and the one
    before it.

    Stepping over a run is the right move — a subset run never compares — but a
    digest that says nothing about it reports an older delta in this week's
    voice. On the flagship consumer that printed runs 17 -> 19 while run 22 sat
    in the store, and nothing on any surface said 22 existed.
    """
    if pair is None:
        return []
    prev, cur = pair
    return [r for r in runs[runs.index(prev) + 1:] if r["id"] != cur["id"]]


_Key = tuple[str, str]  # (path, long_name): what identifies a function across runs
_Move = tuple[float, ScoredRow, ScoredRow]  # (delta, before, after)
_Delta = tuple[float, ScoredRow]


class _Changes(NamedTuple):
    """What moved between two runs, bucketed the way the digest reports it."""
    regressions: list[_Delta]
    improvements: list[_Delta]
    appeared: list[ScoredRow]

    def nothing_moved(self) -> bool:
        return not (self.regressions or self.improvements or self.appeared)


def _by_key(rows: list[ScoredRow]) -> dict[_Key, ScoredRow]:
    return {(r.path, r.long_name): r for r in rows}


def _moves(prev_by_key: dict[_Key, ScoredRow],
           cur_by_key: dict[_Key, ScoredRow]) -> list[_Move]:
    """Every function measured in both runs, paired with how far its CRAP moved."""
    return [(row.crap - prev_by_key[key].crap, prev_by_key[key], row)
            for key, row in cur_by_key.items() if key in prev_by_key]


def _regressions(moves: list[_Move]) -> list[_Delta]:
    return [(delta, after) for delta, _before, after in moves if delta > 0.01]


def _improvements(moves: list[_Move], target: int) -> list[_Delta]:
    """Only a function that WAS over target improves; drift below it is not news."""
    return [(delta, after) for delta, before, after in moves
            if delta < -0.01 and before.crap > target]


def _appeared(prev_by_key: dict[_Key, ScoredRow], cur_by_key: dict[_Key, ScoredRow],
              target: int) -> list[ScoredRow]:
    """Functions the previous run never saw; new code under target is not news."""
    return [row for key, row in cur_by_key.items()
            if key not in prev_by_key and row.crap > target]


def _changes_between(prev: list[ScoredRow], cur: list[ScoredRow], target: int) -> _Changes:
    prev_by_key, cur_by_key = _by_key(prev), _by_key(cur)
    moves = _moves(prev_by_key, cur_by_key)
    return _Changes(
        regressions=_regressions(moves),
        improvements=_improvements(moves, target),
        appeared=_appeared(prev_by_key, cur_by_key, target),
    )


def _totals_line(t_prev: Totals, t_cur: Totals) -> str:
    return (f"CRAP load {t_prev.crap_load} -> {t_cur.crap_load}; "
            f"over target {t_prev.over_target} -> {t_cur.over_target}; "
            f"functions {t_prev.functions} -> {t_cur.functions}")


def _fn_line(prefix: str, row: ScoredRow) -> str:
    return f"{prefix}: {row.path} {row.long_name} (crap {row.crap:.1f})"


def _regression_lines(regressions: list[_Delta], top: int) -> list[str]:
    return [_fn_line(f"regressed +{delta:.1f}", row)
            for delta, row in sorted(regressions, key=lambda x: -x[0])[:top]]


def _appeared_lines(appeared: list[ScoredRow], top: int) -> list[str]:
    return [_fn_line("new over target", row)
            for row in sorted(appeared, key=lambda r: -r.crap)[:top]]


def _improvement_lines(improvements: list[_Delta], top: int) -> list[str]:
    return [_fn_line(f"improved {delta:.1f}", row)
            for delta, row in sorted(improvements, key=lambda x: x[0])[:top]]


def build_digest(prev: list[ScoredRow], cur: list[ScoredRow], *, target: int, top: int = 5) -> Digest:
    t_prev, t_cur = totals(prev, target=target), totals(cur, target=target)
    changed = _changes_between(prev, cur, target)
    if t_prev == t_cur and changed.nothing_moved():
        return Digest(quiet=True, lines=[])
    return Digest(quiet=False, lines=[
        _totals_line(t_prev, t_cur),
        *_regression_lines(changed.regressions, top),
        *_appeared_lines(changed.appeared, top),
        *_improvement_lines(changed.improvements, top),
    ])
