"""The JaCoCo adapter: each sourcefile's line counters as per-file evidence.

Gradle's jacocoTestReport and Maven's jacoco:report write it. Each `<line nr mi
ci mb cb>` of a `<sourcefile>` counts when mi + ci > 0, is hit when ci > 0, and
holds the branch triple (nr, mb + cb, cb) when mb + cb > 0. `<class>`,
`<method>` and `<counter>` are not read: score's join gives lines to spans.
A key is the package path, `/`, the sourcefile name, under path_prefix; one key
read twice merges by line. Memory follows the largest `<package>`.
"""
from __future__ import annotations

import functools
import re
import sys
from array import array
from pathlib import Path
from typing import TYPE_CHECKING

from . import covstream
from .errors import ToolError
from .named import first_few
from .repopath import Reported, absolute
from .score import FileEvidence

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET

    from .config import Lane
    from .repopath import Unplaced

_COUNT = re.compile(r"[0-9]+")
_COUNTERS = ("mi", "ci", "mb", "cb")
# The largest line number array("I") holds. A line past it, or below 1, sits in
# no span a function or a changed range names, so the evidence leaves it out.
_LAST_LINE = 2 ** 32 - 1
# A package is the smallest element that carries the package path a key needs;
# groups around it are passed through at any depth, and never built.
_TAGS = ("package",)


class _Refusal(ValueError):
    """An element crapkit cannot read, worded without the artifact's name."""


def _count(value: str | None, what: str, where: str) -> int:
    """A nonnegative integer attribute of the element `where` names."""
    if value is None:
        raise _Refusal(f"{where} has no {what}")
    if _COUNT.fullmatch(value) is None:
        raise _Refusal(f"{where}: {what} {value!r} is not a nonnegative integer")
    return int(value)


def _numbered(line: int) -> bool:
    """A line array("I") holds and a span can own."""
    return 0 < line <= _LAST_LINE


class _File:
    """One key's evidence while the report is read: each counted line's hit
    and each branch line's (total, covered)."""

    __slots__ = ("lines", "arms")

    def __init__(self, earlier: FileEvidence | None) -> None:
        self.lines: dict[int, bool] = {}
        self.arms: dict[int, tuple[int, int]] = {}
        if earlier is not None:
            self.lines.update(dict.fromkeys(earlier.missed_lines, False))
            self.lines.update(dict.fromkeys(earlier.hit_lines, True))
            self.arms.update((line, (total, covered)) for line, total, covered in earlier.branches)

    def add_line(self, element: ET.Element) -> None:
        """One `<line>`: counted when it holds an instruction, hit when one ran."""
        at = _count(element.get("nr"), "nr", "a line")
        mi, ci, mb, cb = (_count(element.get(name), name, f"line {at}") for name in _COUNTERS)
        if mi + ci:
            self.lines[at] = self.lines.get(at, False) or ci > 0
        if mb + cb:
            total, covered = self.arms.get(at, (0, 0))
            self.arms[at] = (max(total, mb + cb), max(covered, cb))

    def evidence(self) -> FileEvidence:
        """The compact form a later sourcefile for the key merges into."""
        hit, missed = array("I"), array("I")
        for line in filter(_numbered, sorted(self.lines)):
            (hit if self.lines[line] else missed).append(line)
        branches = tuple((line, *self.arms[line]) for line in sorted(self.arms) if _numbered(line))
        return FileEvidence(hit, missed, branches)


class _Report:
    """Each key's evidence from the packages read so far."""

    def __init__(self, key) -> None:
        self._key = key
        self.files: dict[str, FileEvidence] = {}

    def take(self, package: ET.Element) -> None:
        name = package.get("name")
        if name is None:
            raise _Refusal("a package has no name")
        for sourcefile in package.iterfind("sourcefile"):
            self._file(name, sourcefile)

    def _file(self, package: str, sourcefile: ET.Element) -> None:
        """The default package's name is empty: its sourcefile keys alone."""
        name = sourcefile.get("name")
        if not name:
            raise _Refusal(f"package {package!r}: a sourcefile has no name")
        written = f"{package}/{name}" if package else name
        key = self._key(written)
        record = _File(self.files.get(key))
        try:
            for line in sourcefile.iterfind("line"):
                record.add_line(line)
        except _Refusal as refusal:
            raise _Refusal(f"{written}: {refusal}") from None
        self.files[key] = record.evidence()


def _measured(evidence: dict[str, FileEvidence], name: str) -> dict[str, FileEvidence]:
    """A report with no sourcefile scores every function untested if it is let through."""
    if not evidence:
        raise ToolError(f"jacoco artifact {name} is empty (zero sourcefiles) - the coverage run "
                        "measured nothing; rerun the lane and check that its command runs the "
                        "tests and then the report task")
    return evidence


def _keyer(reported: Reported, path_prefix: str):
    """The key of a package path and sourcefile name: under the prefix, placed
    by the reported entry, each written path keyed once."""
    prefix = (path_prefix.rstrip("/") + "/") if path_prefix else ""
    return functools.cache(lambda written: reported(prefix + written))


def _evidence(path: Path, name: str, reported: Reported,
              path_prefix: str) -> tuple[dict[str, FileEvidence], str]:
    """Each key's FileEvidence and the sha256 of the artifact's bytes, one
    pass. An element crapkit cannot read is refused naming the artifact."""
    what = f"jacoco artifact {name}"
    with open(path, "rb") as raw:
        reader = covstream.HashingReader(raw)
        report = _Report(_keyer(reported, path_prefix))
        try:
            for package in covstream.xml_elements(reader, what, _TAGS):
                report.take(package)
        except _Refusal as refusal:
            raise ToolError(f"{what}: {refusal}; {covstream.REGENERATE}") from None
        return _measured(report.files, name), reader.hexdigest()


# --- the adapter a lane reads through ------------------------------------------

WRONG_TREE_FIX = ("These keys, the report's package paths under path_prefix, name files outside "
                  "this checkout: set path_prefix to the source root inside this checkout "
                  "(src/main/java for Gradle and Maven) and rerun the lane here")
# JaCoCo writes package paths, never absolute ones: only an absolute
# path_prefix makes an absolute key.
ABSOLUTE_FIX = ("JaCoCo writes package paths, so these absolute keys come from path_prefix: "
                "set it to the source root relative to this checkout")
# An aggregate report holds every module's package paths and no module root, so
# one path_prefix places one module at most.
_PER_MODULE = ("for a multi-module build, declare one lane per module with path_prefix at that "
               "module's source root")
UNMEASURED_READING = ("or the report's package paths sit under a source root these scopes do not "
                      "start at, which path_prefix names (src/main/java for Gradle and Maven); "
                      f"{_PER_MODULE}")
TAKES_PATH_PREFIX = True
# No producer facts: the .exec data file belongs to the report task, which the
# lane's command runs, so no second lane collides on it and nothing shards.
SHARD_GLOB = None
COMBINE_RECIPE = None
DROPPINGS = ()


def data_file(lane: Lane) -> None:
    """No data file a second lane could collide on."""
    return None


def read(lane: Lane, root: Path, artifact: Path, *,
         unplaced: dict[str, Unplaced] | None = None
         ) -> tuple[dict[str, list], dict[str, FileEvidence], str]:
    """No function records (an empty list per measured file), each file's
    FileEvidence and the artifact digest, one pass. `unplaced` takes each
    absolute key the reported entry left unplaced, with its reason."""
    reported = Reported(Path(root))
    evidence, digest = _evidence(artifact, lane.artifact, reported, lane.path_prefix)
    if unplaced is not None:
        unplaced.update(reported.unplaced)
    _note_astray(lane, Path(root), evidence)
    return {key: [] for key in evidence}, evidence, digest


def _note_astray(lane: Lane, root: Path, evidence: dict[str, FileEvidence]) -> None:
    """Name the relative keys that name no file here. One path_prefix places
    one module of an aggregate report, and the rest score nothing with no word
    from the scope check while any key reaches a scope. Absolute keys go to
    the wrong-tree refusals instead."""
    astray = [key for key in evidence if not absolute(key) and not (root / key).is_file()]
    if not astray:
        return
    where = f"under path_prefix {lane.path_prefix!r}" if lane.path_prefix else "in this checkout"
    print(f"crapkit: lane {lane.name!r}: {len(astray)} of the {len(evidence)} sourcefile(s) in "
          f"{lane.artifact} name no file {where} ({first_few(sorted(astray))}), so no function "
          f"scores their lines; {_PER_MODULE}", file=sys.stderr)


def missing(lane: Lane, root: Path, artifact: Path) -> dict[str, set[int]]:
    """The counted lines with ci 0, per measured file."""
    evidence, _ = _evidence(artifact, lane.artifact, Reported(Path(root)), lane.path_prefix)
    return {key: set(file.missed_lines) for key, file in evidence.items()}


def contexts(lane: Lane, root: Path, artifact: Path, source_path: str) -> dict[int, list[str]]:
    """JaCoCo records no per-line test contexts."""
    return {}
