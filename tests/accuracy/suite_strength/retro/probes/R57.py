"""R57: a path's churn weight was a plain float sum in the order its commits were
listed, and a carried commit table lists a merged branch's commits in another
order than git's own log, so at a 4-decimal rounding edge the carried weight
and a cold rebuild rounded apart (0.577 against 0.5769).

    <retro venv python> R57.py WORKTREE

history_oracles' check builds a merged branch whose dates it chose for a
rounding edge and passes at the commit before the fix: a replay runs Python
3.12, whose sum() compensates for rounding (CPython gh-100425), so the order no
longer shows there. crapkit supports Python 3.11 (requires-python >= 3.11 at
both commits), whose sum() adds floats left to right. This probe gives
crapkit.churn that 3.11 sum and hands the edge to the function the fix changed,
crapkit.churn._weight, which both commits have: three recency weights (the
fix's own unit test names them: rita 0.003149365420286381, sam 0.5, sue
0.07380063457971359) listed in two orders. Each order must give the correctly
rounded sum.
"""
# source: exact rational arithmetic (fractions.Fraction over the three binary floats): the sum is 3.2e-17 below 0.57695, so rounded to 4 places it is 0.5769 in any order; Python 3.11's sum() of floats adds left to right (the 3.12 "What's New" entry for gh-100425 names the change)
from __future__ import annotations

from fractions import Fraction
import sys

RITA, SAM, SUE = 0.003149365420286381, 0.5, 0.07380063457971359
ORDERS = {"rita first": [0, 1, 2], "log order": [1, 2, 0]}


def _sum_as_python_3_11(values, start=0):
    """Python 3.11's float sum: one addition after another, rounding each."""
    total = start
    for value in values:
        total += value
    return total


def main(argv: list[str]) -> int:
    import crapkit.churn as churn

    churn.sum = _sum_as_python_3_11  # module global: churn's own calls to sum() find it first
    weights = {0: RITA, 1: SAM, 2: SUE}
    exact = float(round(sum(map(Fraction, weights.values())), 4))
    got = {order: churn._weight(seqs, weights) for order, seqs in ORDERS.items()}
    assert got == {order: exact for order in ORDERS}, f"the weights read {got}; the exact sum rounds to {exact}"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
