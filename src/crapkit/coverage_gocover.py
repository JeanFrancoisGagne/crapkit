"""The Go coverprofile adapter: `go test -coverprofile` blocks as per-file line evidence.

The first line is `mode: set`, `mode: count` or `mode: atomic`; a later mode
line of the same mode (profiles a CI script concatenated) is read past. Each
other line is `<importpath>/<file>.go:<l0>.<c0>,<l1>.<c1> <numStmt> <count>`.
Every line l0 to l1 of a block with numStmt above 0 is hit when any block over
it ran, else missed, so the duplicate blocks a -coverpkg run writes (one per
test binary) merge. A numStmt-0 block gives nothing, and no block gives a branch.

Keys: go.mod at the lane's cwd, or in the nearest directory above it inside the
checkout, names the module path. An import path under it keys to that
directory joined to the rest, then repopath's reported entry; any other import
path stays as written and names no file here. go.work is not read.
"""
from __future__ import annotations

import re
import sys
from array import array
from pathlib import Path
from typing import TYPE_CHECKING

from . import covstream
from .errors import ToolError
from .named import first_few
from .repopath import Reported
from .repotext import repo_text
from .score import FileEvidence

if TYPE_CHECKING:
    from .config import Lane
    from .repopath import Unplaced

_MODES = ("set", "count", "atomic")
_RANGE = re.compile(r"([0-9]+)\.[0-9]+,([0-9]+)\.[0-9]+")
_COUNT = re.compile(r"[0-9]+")
# go.mod's module directive, its path bare or quoted; a `//` comment is cut
# off the line first, and no module path holds `//`.
_MODULE = re.compile(r'module\s+["`]?([^"`\s]+)["`]?')
# A line's state in a file's bytearray: no block over it, missed, hit. Mapping
# through _AT_LEAST_MISSED marks a missed block's lines and keeps a hit a hit.
_MISSED, _HIT = 1, 2
_AT_LEAST_MISSED = bytes([_MISSED, _MISSED, _HIT]) + bytes(range(3, 256))
_PER_MODULE = ("declare one lane per module with cwd at the module, and test packages "
               "(./...), not files")


class _Refusal(ValueError):
    """A line crapkit cannot read, worded without the artifact's name."""


def _count(value: str, what: str, line: str) -> int:
    if _COUNT.fullmatch(value) is None:
        raise _Refusal(f"{line!r}: {what} {value!r} is not a nonnegative integer")
    return int(value)


def _mode(line: str) -> str:
    """The mode a `mode:` line names. Only the first line can fail the prefix:
    a later line reaches here because it starts `mode:`."""
    head, colon, mode = line.partition(":")
    if head != "mode" or not colon:
        raise _Refusal(f"the first line {line!r} is not a mode line (mode: set, count or "
                       "atomic): a run killed before it wrote the profile, or a file that is not "
                       "a Go coverprofile")
    if mode.strip() not in _MODES:
        raise _Refusal(f"mode {mode.strip()!r} is not set, count or atomic")
    return mode.strip()


def _position(position: str, line: str) -> tuple[str, int, int]:
    """The file and the first and last line of `<file>:<l0>.<c0>,<l1>.<c1>`."""
    written, colon, span = position.rpartition(":")
    if not colon:
        raise _Refusal(f"{line!r}: a block needs a file and a position range "
                       "<line>.<column>,<line>.<column>")
    found = _RANGE.fullmatch(span)
    if found is None:
        raise _Refusal(f"{line!r}: position range {span!r} is not <line>.<column>,<line>.<column>")
    if not written:
        raise _Refusal(f"{line!r}: a block names no file")
    return written, int(found[1]), int(found[2])


def _block(line: str) -> tuple[str, int, int, int, bool]:
    """A block line's file, first line, last line, statement count and hit."""
    fields = line.rsplit(" ", 2)
    if len(fields) != 3:
        raise _Refusal(f"{line!r}: a block needs a position range, a statement count and a "
                       "hit count")
    position, statements, count = fields
    written, first, last = _position(position, line)
    statements_n = _count(statements, "statement count", line)
    return written, first, last, statements_n, _count(count, "hit count", line) > 0


def _mark(states: bytearray, first: int, last: int, hit: bool) -> None:
    """Lines first to last: hit when `hit`, else missed unless already hit.
    Line 0 is no line. Go writes a block's lines from its own file, so the
    array never outgrows the longest file the profile names."""
    first = max(first, 1)
    if len(states) <= last:
        states.extend(bytes(last + 1 - len(states)))
    span = slice(first, last + 1)
    states[span] = bytes([_HIT]) * len(states[span]) if hit else states[span].translate(
        _AT_LEAST_MISSED)


def _evidence_of(states: bytearray) -> FileEvidence:
    hit = array("I", (line for line, state in enumerate(states) if state == _HIT))
    missed = array("I", (line for line, state in enumerate(states) if state == _MISSED))
    return FileEvidence(hit, missed, ())


class _Keys:
    """Each written path's key, and the written paths outside the module."""

    def __init__(self, root: Path, module_dir: str, module_path: str) -> None:
        self._reported = Reported(root)
        self._under = module_path + "/"
        self._base = module_dir + "/" if module_dir else ""
        self._keys: dict[str, str] = {}
        self.module_path = module_path
        self.outside: set[str] = set()

    def __call__(self, written: str) -> str:
        key = self._keys.get(written)
        if key is None:
            key = self._keys[written] = self._key(written)
        return key

    def _key(self, written: str) -> str:
        if written.startswith(self._under):
            return self._reported(self._base + written[len(self._under):])
        self.outside.add(written)
        return written


class _Profile:
    """The mode the profile declared and each key's line states so far."""

    def __init__(self, keys: _Keys) -> None:
        self._keys = keys
        self.mode: str | None = None
        self.files: dict[str, bytearray] = {}

    def take(self, line: str) -> None:
        if not line.strip():
            return
        if self.mode is None:
            self.mode = _mode(line)
        elif line.startswith("mode:"):
            self._again(_mode(line))
        else:
            self._block(line)

    def _again(self, mode: str) -> None:
        """A profile concatenated from runs of one mode reads on; two modes
        count different things, so they do not merge."""
        if mode != self.mode:
            raise _Refusal(f"a second mode line says mode: {mode} where the first said "
                           f"mode: {self.mode}, so the profiles of two runs were joined; "
                           "concatenate only profiles of one -covermode")

    def _block(self, line: str) -> None:
        written, first, last, statements, hit = _block(line)
        if statements:
            _mark(self.files.setdefault(self._keys(written), bytearray()), first, last, hit)


def _measured(profile: _Profile, name: str) -> dict[str, FileEvidence]:
    """A profile with no block scores every function untested if it is let through."""
    if not profile.files:
        what = "zero blocks" if profile.mode else "no mode line and zero blocks"
        raise ToolError(f"coverprofile artifact {name} is empty ({what}) - the coverage run "
                        "measured nothing; rerun the lane and check that its command runs the "
                        "tests of the scope's packages")
    return {key: _evidence_of(states) for key, states in profile.files.items()}


def _evidence(path: Path, name: str, keys: _Keys) -> tuple[dict[str, FileEvidence], str]:
    """Each key's FileEvidence and the sha256 of the artifact's bytes, one
    pass. A line crapkit cannot read is refused naming the artifact."""
    with open(path, "rb") as raw:
        reader = covstream.HashingReader(raw)
        profile = _Profile(keys)
        try:
            for line in covstream.lines(reader, name):
                profile.take(line)
        except _Refusal as refusal:
            raise ToolError(f"coverprofile artifact {name}: {refusal}; "
                            f"{covstream.REGENERATE}") from None
        return _measured(profile, name), reader.hexdigest()


# --- the module the lane stands in ----------------------------------------------

def _module_dir(root: Path, lane: Lane) -> str:
    """The root-relative directory of the go.mod at the lane's cwd or the
    nearest one above it inside the checkout."""
    parts = [part for part in lane.cwd.replace("\\", "/").split("/") if part not in ("", ".")]
    for depth in range(len(parts), -1, -1):
        where = "/".join(parts[:depth])
        if (root / where / "go.mod").is_file():
            return where
    raise ToolError(f"lane {lane.name!r}: no go.mod at its cwd {lane.cwd or '.'!r} or above it "
                    f"in this checkout, so no import path in {lane.artifact} names a file here; "
                    f"{_PER_MODULE}")


def _module_path(root: Path, module_dir: str) -> str:
    """The path go.mod's module directive names, read as repotext reads a
    repository file: a BOM read past, UTF-16 refused by name."""
    what = f"{module_dir}/go.mod" if module_dir else "go.mod"
    for line in repo_text(root / what, what).splitlines():
        found = _MODULE.fullmatch(line.split("//", 1)[0].strip())
        if found is not None:
            return found[1]
    raise ToolError(f"{what} has no module directive, so crapkit cannot map an import path to a "
                    "file; check that it is the go.mod `go mod init` wrote")


def _keys(lane: Lane, root: Path) -> _Keys:
    module_dir = _module_dir(root, lane)
    return _Keys(root, module_dir, _module_path(root, module_dir))


# --- the adapter a lane reads through ------------------------------------------

WRONG_TREE_FIX = ("These keys name files outside this checkout: rerun the lane here with cwd at "
                  "the module, so its go.mod maps each import path to a file in this checkout")
# Go writes import paths, never absolute ones: an absolute key comes from a
# tool that rewrote the profile.
ABSOLUTE_FIX = ("Go writes import paths, so these absolute keys come from a tool that rewrote "
                "the profile: point the lane at the profile `go test -coverprofile` wrote")
UNMEASURED_READING = ("or the profile's import paths sit outside the module the go.mod at the "
                      f"lane's cwd declares; {_PER_MODULE}")
TAKES_PATH_PREFIX = False
# No producer facts: `go test` writes the profile and nothing else in the
# tree, and merges its packages' profiles itself, so nothing shards.
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
    FileEvidence and the artifact digest, one pass. `unplaced` stays as handed:
    every key is an import path under the module or one left as written."""
    keys = _keys(lane, Path(root))
    evidence, digest = _evidence(artifact, lane.artifact, keys)
    _note_outside(lane, keys, len(evidence))
    return {key: [] for key in evidence}, evidence, digest


def _note_outside(lane: Lane, keys: _Keys, measured: int) -> None:
    """Name the import paths outside the module: they score nothing, and the
    scope check says nothing while any key reaches a scope."""
    if not keys.outside:
        return
    print(f"crapkit: lane {lane.name!r}: {len(keys.outside)} of the {measured} file(s) in "
          f"{lane.artifact} sit outside module {keys.module_path} "
          f"({first_few(sorted(keys.outside))}), so no function scores their lines; "
          f"{_PER_MODULE}", file=sys.stderr)


def missing(lane: Lane, root: Path, artifact: Path) -> dict[str, set[int]]:
    """The lines of numStmt-positive blocks that no block over them ran, per file."""
    evidence, _ = _evidence(artifact, lane.artifact, _keys(lane, Path(root)))
    return {key: set(file.missed_lines) for key, file in evidence.items()}


def contexts(lane: Lane, root: Path, artifact: Path, source_path: str) -> dict[int, list[str]]:
    """A coverprofile records no per-line test contexts."""
    return {}
