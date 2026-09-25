"""Brute-force schedules, the oracle for LPT (longest processing time first).

optimal() tries every assignment of jobs to slots, so it is exact and only
fit for up to about 8 jobs. Graham (1969), "Bounds on multiprocessing timing
anomalies", SIAM J. Appl. Math. 17(2):416-429, Theorem 1: LPT's makespan is at
most (4/3 - 1/(3k)) times the optimum on k identical machines.
"""
from __future__ import annotations

from fractions import Fraction
from itertools import product


def _loads(jobs, assignment, slots: int) -> list:
    loads = [0] * slots
    for job, slot in zip(jobs, assignment):
        loads[slot] += job
    return loads


def optimal(jobs: list, slots: int):
    """The smallest makespan any assignment of `jobs` to `slots` machines reaches."""
    if not jobs:
        return 0
    slots = max(1, min(slots, len(jobs)))
    return min(max(_loads(jobs, assignment, slots))
               for assignment in product(range(slots), repeat=len(jobs)))


def graham_bound(slots: int) -> Fraction:
    return Fraction(4, 3) - Fraction(1, 3 * max(1, slots))
