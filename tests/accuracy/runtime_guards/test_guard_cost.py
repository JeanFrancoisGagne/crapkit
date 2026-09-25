"""What the checks cost: at most 1 percent of a run, and a per-row figure at a large repo's size.

crapkit.invariants adds each check's nanoseconds to a tally per site, and a
process started with CRAPKIT_INVARIANT_RECEIPT writes its tally into that
directory as one JSON file when it exits, as does each analysis-pool worker,
with how long the process ran. The ratio is the tallies' check time over the
processes' lifetimes, each measured with perf_counter_ns. The in-process
timing runs the row check over 137,715 rows, one large consumer repo's run,
and records nanoseconds per row on the JUnit report as the
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


def test_the_row_check_over_a_large_repo_s_run(record_property):
    rows = large_run()
    check_rows = importlib.import_module("crapkit.invariants").check_rows
    began = perf_counter_ns()
    check_rows(rows, lambda scope: CEILING)
    per_row = (perf_counter_ns() - began) / LARGE_REPO_ROWS
    record_property("invariant_ns_per_row", round(per_row, 1))
    assert per_row < MAX_NS_PER_ROW


def spent_ns(tally: dict) -> int:
    return sum(site["ns"] for site in tally["sites"].values())


def cost_share(root, receipts, *argv: str) -> float:
    """What share of a spawned run's process time its checks took: every
    process's check nanoseconds over every process's lifetime. The command and
    its analysis-pool workers run side by side, so setting all their checks
    against the command's wall clock alone would count parallel work as serial."""
    receipts.mkdir()
    done = drive.Driver(root, spawn=True, env={RECEIPT_ENV: str(receipts)}).run(*argv)
    assert done.code == 0, done.stderr
    tallies = [json.loads(path.read_text(encoding="utf-8")) for path in receipts.glob("*.json")]
    return sum(map(spent_ns, tallies)) / sum(tally["alive_ns"] for tally in tallies)


def _tallied() -> int:
    """The nanoseconds this process's checks have added up so far."""
    return sum(ns for _, ns in importlib.import_module("crapkit.invariants").COST.values())


@pytest.mark.process
def test_the_checks_cost_at_most_one_percent_of_a_seed_run(seed, tmp_path, record_property):
    """In process, where the tally is readable directly: the checks' share of
    one coverage run over the seed corpus."""
    driver = drive.Driver(seed.private_copy(tmp_path / "seed"), date_now=seed.date_now)
    before, began = _tallied(), perf_counter_ns()
    done = driver.run("coverage")
    wall, spent = perf_counter_ns() - began, _tallied() - before
    record_property("invariant_cost_ratio", spent / wall)
    assert done.code == 0, done.stderr
    assert spent > 0, "the checks ran"
    assert spent / wall <= 0.01


@pytest.mark.nightly
@pytest.mark.process
def test_the_checks_cost_at_most_one_percent_of_each_full_corpus_run(tmp_path, record_property):
    members = corpora.members(corpora.full_corpus())
    assert members, f"no full corpus: set {corpora.CORPUS_ENV}"
    ratios = {}
    for member in members:
        root = corpora.member_repo(member, tmp_path / member.name)
        ratios[member.name] = cost_share(root, tmp_path / f"{member.name}-tallies", "coverage")
    record_property("invariant_cost_ratios", json.dumps(ratios, sort_keys=True))
    assert {name: ratio for name, ratio in ratios.items() if ratio > 0.01} == {}
