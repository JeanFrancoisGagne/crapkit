"""Digest and trend totals. Pure.

The digest speaks only on change (an unchanged week is silence, per the
empty-channel rule) and keeps to a handful of numbers: totals delta, the worst
per-function regressions, improvements, and new over-ceiling functions.
"""
from __future__ import annotations

from typing import Callable, NamedTuple

from .invariants import check_rollup, check_totals
from .score import CRAP_PLACES, ScoredRow, crap_load, grade, over_ceiling
from .keys import key_names, key_of

# scope -> the ceiling its rows are judged against
_CeilingOf = Callable[[str], int]


class Totals(NamedTuple):
    functions: int
    over_target: int
    crap_load: float
    avg: float
    pct_over: float  # normalized: absolute counts grow with the repo; this does not


class Digest(NamedTuple):
    quiet: bool
    lines: list[str]


def _ceiling_rule(target: int, scope_targets: dict[str, int] | None) -> _CeilingOf:
    """The pair form of the ceiling rule, for the pure callers that hold no
    Config and hand `totals` a target and a scope map (score, verify, ratchet,
    the coverage summary's per-scope block). A command that holds a Config
    passes `Config.ceiling_of` itself, as `build_digest` takes it; the two
    spell the same rule, a scope's own target, else the repo's."""
    own = scope_targets or {}
    return lambda scope: own.get(scope, target)


def _over_count(rows, ceiling_of: _CeilingOf) -> int:
    return sum(1 for r in rows if over_ceiling(r.crap, ceiling_of(r.scope)))


def _totals_by(rows: list[ScoredRow], ceiling_of: _CeilingOf) -> Totals:
    return totals_from_counts(len(rows), _over_count(rows, ceiling_of),
                              crap_load(r.crap for r in rows))


def totals_from_counts(functions: int, over_target: int, load: float) -> Totals:
    """The rounding rule, in one place. A caller that already has the three sums
    (store.run_totals reads them off the rollup) must round them exactly the
    way a caller holding the rows does, or the same run reads two ways. The
    sums are checked against their bounds first (`invariants.check_totals`)."""
    check_totals(functions, over_target, load)
    return Totals(
        functions=functions,
        over_target=over_target,
        crap_load=round(load, 2),
        avg=round(load / functions, 4) if functions else 0.0,
        pct_over=round(100.0 * over_target / functions, 2) if functions else 0.0,
    )


def totals(rows: list[ScoredRow], *, target: int,
           scope_targets: dict[str, int] | None = None) -> Totals:
    return _totals_by(rows, _ceiling_rule(target, scope_targets))


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
    read two ways depending on which command asked. Each grade is checked
    against the README's band table (`invariants.check_rollup`).
    """
    rollup = {scope: {"functions": t.functions, "over_target": t.over_target,
                      "crap_load": t.crap_load, "grade": grade(t.over_target, t.functions)}
              for scope, t in by_scope.items()}
    check_rollup(rollup)
    return rollup


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


_Key = tuple[str, str]  # path and ordinal key name identify one function across runs
_Move = tuple[float, ScoredRow, ScoredRow]  # (delta, before, after)
_Delta = tuple[float, ScoredRow]


class _Changes(NamedTuple):
    """What moved between two runs, bucketed the way the digest reports it."""
    regressions: list[_Delta]
    improvements: list[_Delta]
    appeared: list[ScoredRow]
    newly_scored: list[ScoredRow]

    def nothing_moved(self) -> bool:
        return not (self.regressions or self.improvements or self.appeared or self.newly_scored)


def _by_key(rows: list[ScoredRow]) -> dict[_Key, ScoredRow]:
    names = key_names(rows)
    found = {}
    for row in rows:
        key = key_of(names, row)
        if key not in found or row.crap > found[key].crap:
            found[key] = row._replace(long_name=key[1])
    return found


def _moves(prev_by_key: dict[_Key, ScoredRow],
           cur_by_key: dict[_Key, ScoredRow]) -> list[_Move]:
    """Every function measured in both runs, paired with how far its CRAP moved."""
    return [(row.crap - prev_by_key[key].crap, prev_by_key[key], row)
            for key, row in cur_by_key.items() if key in prev_by_key]


def _regressions(moves: list[_Move]) -> list[_Delta]:
    return [(delta, after) for delta, _before, after in moves if delta > 0.01]


def _improvements(moves: list[_Move], ceiling_of: _CeilingOf) -> list[_Delta]:
    """Only a function that WAS over its ceiling improves; drift below it is not news."""
    return [(delta, after) for delta, before, after in moves
            if delta < -0.01 and over_ceiling(before.crap, ceiling_of(before.scope))]


def _unseen(prev_by_key: dict[_Key, ScoredRow], cur_by_key: dict[_Key, ScoredRow],
            ceiling_of: _CeilingOf) -> list[ScoredRow]:
    """Functions the previous run holds no row for; code under its ceiling is not news."""
    return [row for key, row in cur_by_key.items()
            if key not in prev_by_key and over_ceiling(row.crap, ceiling_of(row.scope))]


def _split_by_scope(rows: list[ScoredRow],
                    scored_before: set[str]) -> tuple[list[ScoredRow], list[ScoredRow]]:
    """The rows whose scope the previous run scored, then the rest.

    A missing row is a new function only when the previous run measured its
    scope. A scope added to crapkit.toml since then leaves the older run with
    no row for code that was already there, and calling that code new
    announces old debt as this week's."""
    return ([row for row in rows if row.scope in scored_before],
            [row for row in rows if row.scope not in scored_before])


def _changes_between(prev: list[ScoredRow], cur: list[ScoredRow],
                     ceiling_of: _CeilingOf) -> _Changes:
    prev_by_key, cur_by_key = _by_key(prev), _by_key(cur)
    moves = _moves(prev_by_key, cur_by_key)
    appeared, newly_scored = _split_by_scope(_unseen(prev_by_key, cur_by_key, ceiling_of),
                                             {row.scope for row in prev})
    return _Changes(
        regressions=_regressions(moves),
        improvements=_improvements(moves, ceiling_of),
        appeared=appeared,
        newly_scored=newly_scored,
    )


def _totals_line(t_prev: Totals, t_cur: Totals) -> str:
    return (f"CRAP load {t_prev.crap_load} -> {t_cur.crap_load}; "
            f"over ceiling {t_prev.over_target} -> {t_cur.over_target}; "
            f"functions {t_prev.functions} -> {t_cur.functions}")


def _fn_line(prefix: str, row: ScoredRow) -> str:
    return f"{prefix}: {row.path} {row.long_name} (crap {row.crap:.1f})"


def _by_move(delta: _Delta) -> tuple:
    """Smallest move first, compared at 4 places so equal moves tie and list by
    path. In binary floating point 10.4 - 9.0 is 1.3999999999999986 and
    6.6 - 5.2 is 1.4000000000000004: two functions that rose by 1.4 listed in
    that noise's order, and the five-line cut kept whichever it put first."""
    move, row = delta
    return round(move, CRAP_PLACES), row.path, row.start


def _largest_rise_first(delta: _Delta) -> tuple:
    move, row = delta
    return _by_move((-move, row))


def _regression_lines(regressions: list[_Delta], top: int) -> list[str]:
    ranked = sorted(regressions, key=_largest_rise_first)
    return [_fn_line(f"regressed +{delta:.1f}", row) for delta, row in ranked[:top]]


def _worst(rows: list[ScoredRow], top: int) -> list[ScoredRow]:
    """Highest CRAP first at 4 places: ccn 25 at 80% coverage and ccn 5 at none
    both score 30, which the floats read as 29.999999999999996 and 30.0."""
    return sorted(rows, key=lambda r: (-round(r.crap, CRAP_PLACES), r.path, r.start))[:top]


def _appeared_lines(appeared: list[ScoredRow], top: int) -> list[str]:
    return [_fn_line("new over ceiling", row) for row in _worst(appeared, top)]


def _newly_scored_lines(newly_scored: list[ScoredRow], top: int) -> list[str]:
    return [_fn_line(f"newly scored over ceiling in scope {row.scope}", row)
            for row in _worst(newly_scored, top)]


def _improvement_lines(improvements: list[_Delta], top: int) -> list[str]:
    ranked = sorted(improvements, key=_by_move)  # moves are negative: largest drop first
    return [_fn_line(f"improved {delta:.1f}", row) for delta, row in ranked[:top]]


def build_digest(prev: list[ScoredRow], cur: list[ScoredRow], *,
                 ceiling_of: _CeilingOf, top: int = 5) -> Digest:
    """`ceiling_of` is `Config.ceiling_of`, the same rule `trend` counts by:
    a row is judged against its own scope's ceiling, so a new function under
    that ceiling is not news even when it sits over the repo's. A function in a
    scope `prev` scored nothing in reads "newly scored", never "new"."""
    t_prev, t_cur = _totals_by(prev, ceiling_of), _totals_by(cur, ceiling_of)
    changed = _changes_between(prev, cur, ceiling_of)
    if t_prev == t_cur and changed.nothing_moved():
        return Digest(quiet=True, lines=[])
    return Digest(quiet=False, lines=[
        _totals_line(t_prev, t_cur),
        *_regression_lines(changed.regressions, top),
        *_appeared_lines(changed.appeared, top),
        *_newly_scored_lines(changed.newly_scored, top),
        *_improvement_lines(changed.improvements, top),
    ])
