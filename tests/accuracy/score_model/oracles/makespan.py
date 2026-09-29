"""Brute-force schedules, the oracle for LPT (longest processing time first).

optimal() tries every way to split the jobs into at most k groups (a job
joins a machine already in use or the first idle one, which skips the k!
relabellings of one split), so it is exact and fit for up to about 9 jobs.
Graham (1969), "Bounds on multiprocessing timing anomalies", SIAM J. Appl.
Math. 17(2):416-429, Theorem 1: LPT's makespan is at most (4/3 - 1/(3k))
times the optimum on k identical machines. Any list schedule, whatever the
job order, stays within 2 - 1/k of the optimum (Graham 1966, "Bounds for
certain multiprocessing anomalies", Bell System Technical Journal 45(9)).
"""
from __future__ import annotations

from fractions import Fraction


def _best(jobs: list, loads: list, slots: int, bound):
    if not jobs:
        return max(loads, default=0)
    job, rest = jobs[0], jobs[1:]
    choices = range(min(len(loads) + 1, slots))
    return min(_placed(rest, loads, choice, job, slots) for choice in choices)


def _placed(rest: list, loads: list, choice: int, job, slots: int):
    grown = list(loads) + ([0] if choice == len(loads) else [])
    grown[choice] += job
    return _best(rest, grown, slots, None)


def optimal(jobs: list, slots: int):
    """The smallest makespan any assignment of `jobs` to `slots` machines reaches."""
    return _best(sorted(jobs, reverse=True), [], max(1, slots), None)


def graham_bound(slots: int) -> Fraction:
    return Fraction(4, 3) - Fraction(1, 3 * max(1, slots))


def list_bound(slots: int) -> Fraction:
    return 2 - Fraction(1, max(1, slots))


def lpt(jobs: list, slots: int):
    """Longest job first, each to the least-loaded machine: the makespan."""
    loads = [0] * max(1, slots)
    for job in sorted(jobs, reverse=True):
        loads[loads.index(min(loads))] += job
    return max(loads)
