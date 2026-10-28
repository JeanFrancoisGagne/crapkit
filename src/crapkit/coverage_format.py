"""Which coverage format reads a lane's artifact.

A lane names its format with `parser`, and each format is one adapter module:
coverage_istanbul for istanbul JSON, coverage_py for a coverage.py JSON report.
An adapter owns what its format decides when an artifact is read: function
coverage, dead lines, per-line test contexts, the path key it builds, the
absolute keys it did not place with the placing step's reason (the record the
wrong-tree check reads), whether it joins the lane's path_prefix onto a
relative key, and the advice a refusal gives. The lane run, the dark-line fold
and `explain --tests` look the adapter up here once and ask it, so none of them
compares parser strings of its own.

An adapter also owns its producer's facts, which hold whatever runner starts
the producer, because `parser` names the producer and not the runner: where its
data file lands (doctor's shared data-file finding), the shards a killed
parallel run leaves with the recipe that combines them (the lane's missing
artifact refusal), and what a run drops in the tree (init's .gitignore). A
format with no such fact leaves it empty. Runner facts read the toolchain
table, so no module but this one compares parser strings
(tests/unit/test_parser_strings_live_in_one_module.py).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from . import coverage_istanbul, coverage_py
from .errors import ToolError

if TYPE_CHECKING:
    from pathlib import Path

    from .config import Lane
    from .repopath import Unplaced
    from .score import FileEvidence, FnCoverage


class CoverageFormat(Protocol):
    """What every adapter module exposes.

    `TAKES_PATH_PREFIX`: the reader joins the lane's path_prefix onto every
    relative key, so the key the runner wrote is the measured key less that
    prefix. `read` returns the function records, one score.FileEvidence per
    measured file, and the artifact's digest. A reader that keeps its own
    function records (coverage.py, istanbul) leaves hit_lines None and fills
    missed_lines with the dead lines; one that has none fills hit_lines too,
    and score_rows joins it to the inventory's spans. `read` fills `unplaced`,
    when handed one, with each absolute key it did not place, spelled as the
    report wrote it (`/` between directories), mapped to the placing step's
    reason.

    The producer facts: `data_file` is where the lane's data file lands, or
    None; `SHARD_GLOB` matches the shards a killed parallel run leaves in the
    lane's directory, and `COMBINE_RECIPE` is the commands that combine them,
    `{target}` the artifact path from there, both None for a format with no
    shards; `DROPPINGS` is what a run leaves in the tree for init to ignore."""

    WRONG_TREE_FIX: str
    ABSOLUTE_FIX: str
    UNMEASURED_READING: str
    TAKES_PATH_PREFIX: bool
    SHARD_GLOB: str | None
    COMBINE_RECIPE: tuple[str, ...] | None
    DROPPINGS: tuple[str, ...]

    def data_file(self, lane: Lane) -> str | None: ...

    def read(self, lane: Lane, root: Path, artifact: Path, *,
             unplaced: dict[str, Unplaced] | None = None
             ) -> tuple[dict[str, list[FnCoverage]], dict[str, FileEvidence], str]: ...

    def missing(self, lane: Lane, root: Path, artifact: Path) -> dict[str, set[int]]: ...

    def contexts(self, lane: Lane, root: Path, artifact: Path,
                 source_path: str) -> dict[int, list[str]]: ...


_FORMATS = {"istanbul": coverage_istanbul, "coveragepy": coverage_py}

# One version per key of _FORMATS. A change that moves what a reader reads
# raises its number and analyze.ANALYSIS_VERSION by one in the same change; a
# new reader enters at FIRST_READER_VERSION and raises nothing. Literals:
# tools/accuracy reads them with ast.literal_eval.
FIRST_READER_VERSION = 1
READER_VERSIONS = {"istanbul": 1, "coveragepy": 1}


def lane_format(lane: Lane) -> CoverageFormat:
    """The adapter for this lane's `parser`, or the refusal an unknown one earns.

    One lookup, so the lane run and the dark-line fold cannot word the refusal
    two ways, and neither can fall through to the other format's reader."""
    adapter = _FORMATS.get(lane.parser)
    if adapter is None:
        raise ToolError(f"lane {lane.name!r}: parser {lane.parser!r} not implemented yet")
    return adapter
