"""Which coverage format reads a lane's artifact.

A lane names its format with `parser`, and each format is one adapter module:
coverage_istanbul for istanbul JSON, coverage_py for a coverage.py JSON report.
An adapter owns what its format decides when an artifact is read: function
coverage, dead lines, per-line test contexts, the path key it builds with the
inverse the wrong-tree check reads, and the advice a refusal gives. The lane
run, the dark-line fold and `explain --tests` look the adapter up here once and
ask it, so none of them compares parser strings of its own.

Runner and config knowledge stays with its owners: config validation, init,
doctor, the container guard and the shard hint still read `parser`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from . import coverage_istanbul, coverage_py
from .errors import ToolError

if TYPE_CHECKING:
    from pathlib import Path

    from .config import Lane
    from .coverage_istanbul import FnCoverage


class CoverageFormat(Protocol):
    """What every adapter module exposes."""

    WRONG_TREE_FIX: str
    ABSOLUTE_FIX: str
    UNMEASURED_READING: str

    def read(self, lane: Lane, root: Path, artifact: Path
             ) -> tuple[dict[str, list[FnCoverage]], dict[str, set[int]], str]: ...

    def missing(self, lane: Lane, root: Path, artifact: Path) -> dict[str, set[int]]: ...

    def contexts(self, lane: Lane, root: Path, artifact: Path,
                 source_path: str) -> dict[int, list[str]]: ...

    def as_reported(self, lane: Lane, key: str) -> str: ...


_FORMATS = {"istanbul": coverage_istanbul, "coveragepy": coverage_py}


def lane_format(lane: Lane) -> CoverageFormat:
    """The adapter for this lane's `parser`, or the refusal an unknown one earns.

    One lookup, so the lane run and the dark-line fold cannot word the refusal
    two ways, and neither can fall through to the other format's reader."""
    adapter = _FORMATS.get(lane.parser)
    if adapter is None:
        raise ToolError(f"lane {lane.name!r}: parser {lane.parser!r} not implemented yet")
    return adapter
