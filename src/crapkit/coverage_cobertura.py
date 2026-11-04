"""The Cobertura adapter: a report's per-class line hits and branch conditions
as per-file evidence.

gcovr --cobertura, coverage.py's `coverage xml` and istanbul's cobertura
reporter write it. Each `<class>` names a file, and each `<line>` under its
`<lines>` gives a hit count; a `branch="true"` line's `condition-coverage`
(`50% (1/2)`) gives its conditions taken of its total. Cobertura keeps no
function records crapkit can trust for spans, so this reader hands score's
evidence join per-file line and branch evidence and no FnCoverage: `<method>`
elements are not read. Classes that name one file merge by line: a line is
hit when any class hits it, and its conditions take the larger covered count
over the one total every class must report.

Keys: a filename resolves under the first `<source>`, in document order, that
names a file in this checkout. An absolute source goes to the placing step;
a relative source, or a report with no source, takes the lane's path_prefix
and then repopath's reported entry. A filename no source places keeps the
first source's key and reason for the lane's wrong-tree check. The bytes go
through covstream's HashingReader and its XML walk, which own the digest and
the decode; Cobertura has no producer facts (no data file, no shards).
"""
from __future__ import annotations

import re
from array import array
from pathlib import Path
from typing import TYPE_CHECKING

from . import covstream
from .errors import ToolError
from .repopath import Reported, absolute, file_separators
from .score import FileEvidence

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET

    from .config import Lane
    from .repopath import Unplaced

_REGENERATE = covstream.REGENERATE
_COUNT = re.compile(r"[0-9]+")
_CONDITIONS = re.compile(r"[0-9.]+% \(([0-9]+)/([0-9]+)\)")
_BRANCH = {"true": True, "false": False}
# The largest line number array("I") holds. A line past it, or below 1, sits in
# no span a function or a changed range names, so the evidence leaves it out.
_LAST_LINE = 2 ** 32 - 1
_TAGS = ("source", "class")


class _Refusal(ValueError):
    """An element crapkit cannot read, worded without the artifact's name."""


def _count(value: str | None, what: str, line: str) -> int:
    """A nonnegative integer attribute; `line` says where, or is empty."""
    if value is None:
        raise _Refusal(f"{line or 'a line'} has no {what}")
    if _COUNT.fullmatch(value) is None:
        raise _Refusal(f"{line}{': ' if line else ''}{what} {value!r} is not a nonnegative integer")
    return int(value)


def _conditions(value: str, line: str) -> tuple[int, int]:
    """(total, covered) from `condition-coverage`, such as `50% (1/2)`."""
    found = _CONDITIONS.fullmatch(value)
    if found is None:
        raise _Refusal(f"{line}: condition-coverage {value!r} is not a percentage and "
                       "(covered/total)")
    covered, total = int(found[1]), int(found[2])
    if covered > total:
        raise _Refusal(f"{line}: condition-coverage {value!r} covers more conditions than it has")
    return total, covered


def _branch(value: str | None, line: str) -> bool:
    """A line with no `branch` attribute holds no branch."""
    if value is None:
        return False
    if value not in _BRANCH:
        raise _Refusal(f"{line}: branch {value!r} is neither true nor false")
    return _BRANCH[value]


def _numbered(line: int) -> bool:
    """A line array("I") holds and a span can own."""
    return 0 < line <= _LAST_LINE


class _File:
    """One key's evidence while the report is read: each line's hit and each
    branch line's (total, covered)."""

    __slots__ = ("lines", "arms")

    def __init__(self, earlier: FileEvidence | None) -> None:
        self.lines: dict[int, bool] = {}
        self.arms: dict[int, tuple[int, int]] = {}
        if earlier is not None:
            self.lines.update(dict.fromkeys(earlier.missed_lines, False))
            self.lines.update(dict.fromkeys(earlier.hit_lines, True))
            self.arms.update((line, (total, covered)) for line, total, covered in earlier.branches)

    def add_line(self, element: ET.Element) -> None:
        """One `<line>`: its hit, and its conditions when it is a branch line
        that reports them."""
        at = _count(element.get("number"), "number", "")
        line = f"line {at}"
        self.lines[at] = self.lines.get(at, False) or _count(element.get("hits"), "hits", line) > 0
        conditions = element.get("condition-coverage")
        if _branch(element.get("branch"), line) and conditions is not None:
            self._arm(at, *_conditions(conditions, line))

    def _arm(self, at: int, total: int, covered: int) -> None:
        known = self.arms.get(at, (total, 0))
        if known[0] != total:
            raise _Refusal(f"line {at} reports {known[0]} conditions in one class and {total} "
                           "in another")
        self.arms[at] = (total, max(covered, known[1]))

    def evidence(self) -> FileEvidence:
        """The compact form a later class for the key merges into."""
        hit, missed = array("I"), array("I")
        for line in filter(_numbered, sorted(self.lines)):
            (hit if self.lines[line] else missed).append(line)
        branches = tuple((line, *self.arms[line]) for line in sorted(self.arms)
                         if _numbered(line) and self.arms[line][0])
        return FileEvidence(hit, missed, branches)


class _Keys:
    """The key of each filename a class names, under the report's sources."""

    def __init__(self, root: Path, path_prefix: str) -> None:
        self.reported = Reported(root)
        self.sources: list[str] = []
        self.unplaced: dict[str, Unplaced] = {}
        self._root = root
        self._prefix = (path_prefix.rstrip("/") + "/") if path_prefix else ""
        self._known: dict[str, str] = {}

    def __call__(self, written: str) -> str:
        key = self._known.get(written)
        if key is None:
            key = self._known[written] = self._key(file_separators(written))
        return key

    def _key(self, name: str) -> str:
        """The first source under which `name` is a file here, else the first
        source's key, its placing reason recorded."""
        first = None
        for path in self._paths(name):
            key, reason = self._keyed(path)
            if reason is None and (self._root / key).is_file():
                return key
            first = first or (key, reason)
        key, reason = first
        if reason is not None:
            self.unplaced[key] = reason
        return key

    def _paths(self, name: str) -> list[str]:
        if absolute(name) or not self.sources:
            return [name]
        return [f"{source}/{name}" if source else name for source in self.sources]

    def _keyed(self, path: str) -> tuple[str, Unplaced | None]:
        """An absolute path goes to the placing step, never under the prefix;
        a relative one takes the prefix, less any leading `./`."""
        if absolute(path):
            key = self.reported(path)
            return key, self.reported.unplaced.pop(key, None)
        while path.startswith("./"):
            path = path[2:]
        return self.reported(self._prefix + path), None


class _Report:
    """The sources and the classes read so far, each key's evidence compact."""

    def __init__(self, keys: _Keys) -> None:
        self._keys = keys
        self.files: dict[str, FileEvidence] = {}

    def take(self, element: ET.Element) -> None:
        if element.tag == "source":
            self._keys.sources.append(file_separators((element.text or "").strip()).rstrip("/"))
            return
        written = element.get("filename")
        if not written:
            raise _Refusal("a class names no filename")
        key = self._keys(written)
        record = _File(self.files.get(key))
        try:
            for line in element.iterfind("lines/line"):
                record.add_line(line)
        except _Refusal as refusal:
            raise _Refusal(f"{written}: {refusal}") from None
        self.files[key] = record.evidence()


def _measured(evidence: dict[str, FileEvidence], name: str) -> dict[str, FileEvidence]:
    """A report with no class scores every function untested if it is let through."""
    if not evidence:
        raise ToolError(f"cobertura artifact {name} is empty (zero classes) - the coverage run "
                        "measured nothing; rerun the lane and check that its command runs "
                        "the tests")
    return evidence


def _evidence(path: Path, name: str, keys: _Keys) -> tuple[dict[str, FileEvidence], str]:
    """Each key's FileEvidence and the sha256 of the artifact's bytes, one
    pass. An element crapkit cannot read is refused naming the artifact."""
    what = f"cobertura artifact {name}"
    with open(path, "rb") as raw:
        reader = covstream.HashingReader(raw)
        report = _Report(keys)
        try:
            for element in covstream.xml_elements(reader, what, _TAGS):
                report.take(element)
        except _Refusal as refusal:
            raise ToolError(f"{what}: {refusal}; {_REGENERATE}") from None
        return _measured(report.files, name), reader.hexdigest()


# --- the adapter a lane reads through ------------------------------------------

WRONG_TREE_FIX = ("These filenames, under the report's <source> roots, name files outside this "
                  "checkout: rerun the lane here rather than reusing a report copied in or "
                  "restored from a CI cache, and keep the producer's root (gcovr --root, "
                  "coverage.py's source) inside this checkout")
# Reached only by a path this platform cannot open: the reported entry places
# every other spelling of this checkout before the wrong-tree check reads a key.
ABSOLUTE_FIX = ("The reader places every absolute <source> root that resolves under this "
                "checkout, and these could not be opened here: rerun the lane on this machine "
                "rather than reusing a report written somewhere else")
UNMEASURED_READING = ("or the report's filenames are relative to a <source> these scopes do not "
                      "start at, which path_prefix rebases")
TAKES_PATH_PREFIX = True
# No producer facts: Cobertura has many producers, and crapkit knows of no data
# file, shard or dropping they share outside the report, so each one is empty.
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
    filename no source placed, keyed as read, with the placing reason."""
    keys = _Keys(Path(root), lane.path_prefix)
    evidence, digest = _evidence(artifact, lane.artifact, keys)
    if unplaced is not None:
        unplaced.update(keys.unplaced)
    return {key: [] for key in evidence}, evidence, digest


def missing(lane: Lane, root: Path, artifact: Path) -> dict[str, set[int]]:
    """The lines with hits 0, per measured file."""
    evidence, _ = _evidence(artifact, lane.artifact, _Keys(Path(root), lane.path_prefix))
    return {key: set(file.missed_lines) for key, file in evidence.items()}


def contexts(lane: Lane, root: Path, artifact: Path, source_path: str) -> dict[int, list[str]]:
    """Cobertura records no per-line test contexts."""
    return {}
