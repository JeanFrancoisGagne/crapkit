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
