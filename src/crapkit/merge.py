"""Function records produced by the single-pass analyzer."""
from __future__ import annotations

from typing import NamedTuple


class FunctionRecord(NamedTuple):
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
    cognitive: int = 0  # Sonar-spec, from the standard pass; reporting only
    occurrence: int = 0  # Positive source order on one start line; 0 is legacy


class UnanalyzableFile(list):
    """The record list for a file no reader could tokenize: empty, plus why.

    A pool worker is a separate process, so a refusal it collects in module
    state never reaches the parent; it has to travel back through the same
    channel the records use. Empty is the honest record set for a file nothing
    was read from, and every consumer already scores such a file as zero
    functions. `reason` rides along so a run can count and name its refusals
    instead of dying on the first one.
    """

    def __init__(self, reason: str = "") -> None:
        super().__init__()
        self.reason = reason
