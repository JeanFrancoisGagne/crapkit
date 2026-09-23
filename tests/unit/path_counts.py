"""Scored rows grouped per path the way SnapshotStore.count_by_path groups them.

doctor.unmeasured_directories reads per-path counts, because that is what
production hands it. The rule tests write their cases as rows, which read
better than tuples of counts, and go through this grouping to reach the rule.
test_narrow_reads.py checks the SQL grouping against this one, so the rule
tests and the store cannot drift apart unnoticed.
"""
from __future__ import annotations


def path_counts(rows, flag: str = "untested", skip: frozenset = frozenset()) -> list[tuple]:
    """(path, functions, functions whose flag is not `flag`), in path order,
    leaving out the rows of the `skip` scopes."""
    counts: dict[str, list] = {}
    for row in rows:
        if row.scope in skip:
            continue
        entry = counts.setdefault(row.path, [0, 0])
        entry[0] += 1
        entry[1] += row.flag != flag
    return [(path, n, other) for path, (n, other) in sorted(counts.items())]
