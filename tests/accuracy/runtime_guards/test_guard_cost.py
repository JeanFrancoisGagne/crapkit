"""What the checks cost: at most 1 percent of a run, and a per-row figure at a large repo's size.

crapkit.invariants adds each check's nanoseconds to a tally per site, and a
process started with CRAPKIT_INVARIANT_RECEIPT writes its tally into that
directory as one JSON file when it exits, as does each analysis-pool worker,
with how long the process ran. The ratio is the tallies' check time over the
processes' lifetimes, each measured with perf_counter_ns. The in-process
timing runs the row check over 137,715 rows, one large consumer repo's run,
and records nanoseconds per row on the JUnit report, as the test suite's
`invariant_ns_per_row` property.
"""
from __future__ import annotations

from fractions import Fraction
import importlib
import json
from time import perf_counter_ns

import pytest

from accuracy.kit import drive, exact
from accuracy.runtime_guards import corpora

RECEIPT_ENV = "CRAPKIT_INVARIANT_RECEIPT"
LARGE_REPO_ROWS = 137_715
CEILING = 6
# A guard on the guard: 20 microseconds a row is 2.75 s at the size above, far
# past anything the ratio below allows. The ratio is the budget.
MAX_NS_PER_ROW = 20_000


def _remedy(ccn: int, crap: float) -> str:
    """README's remedy table at CEILING."""
    if ccn > CEILING:
        return "decompose"
    return "ok" if crap <= CEILING else "add-tests"


def _row(ccn: int, cov: Fraction):
    crap = float(exact.crap(ccn, cov))
    return drive.to_crapkit("scored_row", ("src", "src/a.py", "f( x )", 3, 12, ccn, ccn, ccn, 8, 1,
                                           2, float(cov), "measured", crap, _remedy(ccn, crap), 3,
                                           1, 0))


def large_run() -> list:
    """LARGE_REPO_ROWS valid rows cycling through ccn 1 to 40 at coverage k/8."""
    kinds = [_row(ccn, Fraction(k, 8)) for ccn in range(1, 41) for k in range(9)]
    return [kinds[i % len(kinds)] for i in range(LARGE_REPO_ROWS)]


def test_the_row_check_over_a_large_repo_s_run(record_testsuite_property):
    rows = large_run()
    check_rows = importlib.import_module("crapkit.invariants").check_rows
    began = perf_counter_ns()
    check_rows(rows, lambda scope: CEILING)
    per_row = (perf_counter_ns() - began) / LARGE_REPO_ROWS
    record_testsuite_property("invariant_ns_per_row", round(per_row, 1))
    assert per_row < MAX_NS_PER_ROW


def spent_ns(tally: dict) -> int:
    return sum(site["ns"] for site in tally["sites"].values())


LIMIT = 0.01
# On a busy machine one preempted timed section passes the limit on its own: a
# member whose record checks tallied 1.4 ms read 44 ms once in three runs. A
# check that really costs more shows on every run, so a run gets up to three.
TRIES = 3


def lowest(measure) -> tuple[int, int]:
    """(check ns, run ns) of the cheapest of up to TRIES runs of measure(try),
    stopping at the first within LIMIT."""
    tries = []
    for attempt in range(TRIES):
        tries.append(measure(attempt))
        if tries[-1][0] <= LIMIT * tries[-1][1]:
            break
    return min(tries, key=lambda pair: pair[0] / pair[1])


def cost_of(root, receipts, *argv: str) -> tuple[int, int]:
    """A spawned run's check nanoseconds and its processes' lifetimes, summed
    over the command and its analysis-pool workers. They run side by side, so
    setting all their checks against the command's wall clock alone would count
    parallel work as serial."""
    receipts.mkdir()
    done = drive.Driver(root, spawn=True, env={RECEIPT_ENV: str(receipts)}).run(*argv)
    assert done.code == 0, done.stderr
    tallies = [json.loads(path.read_text(encoding="utf-8")) for path in receipts.glob("*.json")]
    return sum(map(spent_ns, tallies)), sum(tally["alive_ns"] for tally in tallies)


def _tallied() -> int:
    """The nanoseconds this process's checks have added up so far."""
    return sum(ns for _, ns in importlib.import_module("crapkit.invariants").COST.values())


def _seed_run(seed, work):
    """In process, where the tally is readable directly: one coverage run's
    check nanoseconds and wall clock."""
    driver = drive.Driver(seed.private_copy(work), date_now=seed.date_now)
    before, began = _tallied(), perf_counter_ns()
    done = driver.run("coverage")
    assert done.code == 0, done.stderr
    return _tallied() - before, perf_counter_ns() - began


@pytest.mark.process
def test_the_checks_cost_at_most_one_percent_of_a_seed_run(seed, tmp_path,
                                                           record_testsuite_property):
    spent, wall = lowest(lambda attempt: _seed_run(seed, tmp_path / f"seed{attempt}"))
    record_testsuite_property("invariant_cost_ratio", spent / wall)
    assert spent > 0, "the checks ran"
    assert spent / wall <= LIMIT


def _overall(costs) -> float:
    """The whole corpus's check time over its whole process time."""
    pairs = list(costs)
    return sum(spent for spent, _ in pairs) / sum(alive for _, alive in pairs)


def _member_cost(member, work) -> tuple[int, int]:
    def measure(attempt):
        root = corpora.member_repo(member, work / str(attempt))
        return cost_of(root, work / f"{attempt}-tallies", "coverage")
    return lowest(measure)


@pytest.mark.nightly
@pytest.mark.process
def test_the_checks_cost_at_most_one_percent_of_each_full_corpus_run(tmp_path,
                                                                     record_testsuite_property):
    root = corpora.full_corpus()
    members = corpora.members(root)
    assert members, f"the full corpus at {root} holds no member directory"
    costs = {member.name: _member_cost(member, tmp_path / member.name) for member in members}
    ratios = {name: spent / alive for name, (spent, alive) in costs.items()}
    record_testsuite_property("invariant_cost_ratios", json.dumps(ratios, sort_keys=True))
    record_testsuite_property("invariant_cost_ratio_corpus", _overall(costs.values()))
    assert _overall(costs.values()) <= LIMIT
    assert {name: ratio for name, ratio in ratios.items() if ratio > LIMIT} == {}
