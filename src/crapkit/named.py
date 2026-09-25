"""How a message names a list of files or functions: the first three, in the
order the caller gives them, then a count of the rest.

A lane scoped to forty declared paths listed all forty, which pushed the
sentence saying what to do off the end of a line nobody reads that far into.
Three are enough to find the rest by. A caller that wants them sorted sorts
them first.
"""
from __future__ import annotations

SHOWN = 3


def first_few(names) -> str:
    """`a, b, c and 2 more`, or the names alone when there are three or fewer."""
    names = list(names)
    shown = ", ".join(names[:SHOWN])
    rest = len(names) - SHOWN
    return f"{shown} and {rest} more" if rest > 0 else shown
