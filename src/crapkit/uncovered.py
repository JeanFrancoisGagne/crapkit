"""Dark lines per function: which lines inside a span no lane ever ran.

The line-level truth is the one the diff-coverage check already reads — every
lane's missing-line set, intersected, so a line stays dark only when NO lane
ran it. What this module adds is a verdict on whether the artifacts on disk
still describe the working tree. Line numbers from an artifact built before the
last edit point at code that has moved, which is worse than no numbers at all:
then the lines are [] and a note names the lane to rerun.

The intersection is also the seam a lane can fill in from the walk it was
already doing, so the fold lives here next to the rule it obeys rather than in
the reader that produces the numbers.
"""
from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import NamedTuple

from .invocation import _self


class MissingLines(NamedTuple):
    """Per-file dead lines, plus the reason there are none to report.

    A populated `note` overrides everything: no span gets lines, because the
    only lines available would be the wrong ones. `drift` answers per file: a
    file whose bytes moved since a lane measured it gets that lane's note and
    no lines, and every other file keeps its own.
    """
    by_path: dict[str, set[int]]
    note: str
    drift: SourceDrift | None = None

    def moved(self, path: str) -> str:
        """Why this file's lines point at bytes it no longer holds, or ""."""
        return self.drift.note_for(path) if self.drift is not None else ""

    def in_span(self, path: str, start: int, end: int) -> list[int]:
        if self.note or self.moved(path):
            return []
        return sorted(n for n in self.by_path.get(path, ()) if start <= n <= end)

    def note_for(self, path: str, flag: str = "", scope: str = "") -> str:
        """Why this path has no dark lines, or "" when the artifacts answered.

        A file no artifact mentioned is not a file with full coverage, and an
        empty list with no note is exactly how that lie would read.

        Three causes read the same on the surface and want different moves, so
        the note names which one it is. cc-only is decided first and outranks
        everything: the scope asked for no coverage, so no artifact was ever
        going to speak for it and no lane is worth naming. A stale or missing
        artifact is `self.note`, and rerunning coverage on a settled tree clears
        it. A file absent from every artifact is `flag: untested`: nothing
        imports it, so coverage never emitted a record for it.
        """
        if flag == "cc-only":
            return _cc_only_note(path, scope)
        why = self.note or self.moved(path)
        if why:
            return why
        if path in self.by_path:
            return ""
        return _absent_note(path, flag)


class SourceDrift:
    """Per file, whether a lane that measured it has seen its bytes move since.

    Built from the digests each lane's stamp recorded (lane_sources), and asked
    one path at a time: `brief` asks about one file, so it hashes one file, not
    every file under every scope. A file no record holds is not judged here; no
    lane measured it, and the absent note says so.
    """

    def __init__(self, root: Path, recorded) -> None:
        self._root = root
        self._recorded = tuple(recorded)
        self._notes: dict[str, str] = {}

    def note_for(self, path: str) -> str:
        if path not in self._notes:
            self._notes[path] = self._first_moved(path)
        return self._notes[path]

    def _first_moved(self, path: str) -> str:
        from .errors import GitError
        from .lane_sources import file_moved

        for lane, sources in self._recorded:
            try:
                if file_moved(self._root, sources, path):
                    return _moved_note(lane, path)
            except GitError as exc:
                return _unknown_note(lane, path, exc)
        return ""


def _moved_note(lane, path: str) -> str:
    return (f"lane {lane.name!r}: {path} changed since {lane.artifact} measured it, so its "
            f"line numbers there are stale — rerun `{_self()} coverage` to measure it again")


def _unknown_note(lane, path: str, exc) -> str:
    return (f"lane {lane.name!r}: git cannot say whether {path} changed since {lane.artifact} "
            f"measured it ({exc}), so its line numbers there are withheld — rerun "
            f"`{_self()} coverage` once git answers")


def _cc_only_note(path: str, scope: str) -> str:
    """The note for a scope that declared coverage_optional.

    It names that setting rather than a lane: the stale-artifact note used to
    win here and sent readers to commit and rerun coverage for a scope no lane
    covers, which changes nothing.
    """
    return (f"scope {scope!r} sets coverage_optional = true, so no artifact "
            f"can name uncovered lines for {path}")


def _absent_note(path: str, flag: str) -> str:
    """The note for a file no artifact mentioned, told apart by the score's flag."""
    absent = f"no lane artifact measured {path}"
    if flag != "untested":
        return absent
    return (f"{absent} (flag untested: no test imports it, so coverage records "
            f"nothing for it; write the first test that imports {path})")


def _parse_missing(lane, root: Path, artifact: Path) -> dict[str, set[int]]:
    """One lane's missing lines, read off the file by the lane's format adapter.

    Off the file, not out of a string: every declared lane's artifact would
    otherwise be decoded whole, one after another, on one heap. The adapter
    lookup is the lane run's own, so an unknown parser is refused here in the
    words the run uses, never read as the other format.
    """
    from .coverage_format import lane_format

    return lane_format(lane).missing(lane, root, artifact)


# --- the fold a lane's own walk can fill in --------------------------------
#
# An istanbul lane decodes every member of its artifact to score it. The dead
# lines fall out of that same decode, so the lane hands them here instead of
# leaving this module to reopen the file and decode it all again.

def _artifact_key(artifact: Path) -> tuple | None:
    """Identity plus enough state to notice a rewrite, or None when the file
    cannot be stat'd.

    Not the sha256, though the walk has one: the reader that asks does not, so
    keying on a digest would buy back the second full read this whole path
    exists to avoid.
    """
    try:
        stat = os.stat(artifact)
    except OSError:
        return None
    return (os.path.abspath(artifact), stat.st_mtime_ns, stat.st_size)


def _fold_into(missing: dict[str, set[int]], lines_by_path: dict[str, set[int]]) -> None:
    """A file two lanes measured keeps a line dead only when NO lane ran it.
    Per path this is an intersection, so lanes may arrive in any order."""
    for path, lines in lines_by_path.items():
        missing[path] = missing[path] & lines if path in missing else set(lines)


class DeadLineFold:
    """One run's missing-line intersection, filled by its lane workers.

    Each lane drops its map after adding it. The run keeps one intersection,
    rather than every lane's map, and no other run can consume its state.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._missing: dict[str, set[int]] = {}
        self._sources: set[tuple] = set()

    def add(self, artifact: Path, dead: dict[str, set[int]]) -> None:
        key = _artifact_key(artifact)
        if key is None:
            return
        with self._lock:
            _fold_into(self._missing, dead)
            self._sources.add(key)

    def take(self, wanted: set) -> tuple[dict[str, set[int]], set]:
        """Transfer the map once, refusing any artifact rewritten since its walk."""
        with self._lock:
            missing, sources = self._missing, self._sources
            self._missing, self._sources = {}, set()
        if not sources or not sources <= wanted:
            return {}, set()
        return missing, sources


def _lane_artifacts(root: Path, cfg) -> list[tuple]:
    """(lane, artifact, key) for every lane whose artifact is on disk."""
    found = []
    for lane in cfg.lanes:
        artifact = root / lane.artifact
        if artifact.is_file():
            found.append((lane, artifact, _artifact_key(artifact)))
    return found


def missing_by_path(root: Path, cfg, *, folded: DeadLineFold | None = None) -> dict[str, set[int]]:
    """Union of the lanes' line-level truth; a file two lanes measured keeps a
    line dead only when NO lane ran it.

    A lane whose artifact was walked for its coverage this run already handed
    its dead lines over; the rest are read off the file here. Both routes fold
    the same way and the fold is order-independent, so which lane took which
    route cannot move the answer.
    """
    lanes = _lane_artifacts(root, cfg)
    wanted = {key for _, _, key in lanes}
    missing, covered = folded.take(wanted) if folded is not None else ({}, set())
    for lane, artifact, key in lanes:
        if key not in covered:
            _fold_into(missing, _parse_missing(lane, root, artifact))
    return missing


def _artifact_state(root: Path, lane, scope_paths: dict, git) -> str:
    """What stops this lane's artifact from naming line numbers, or "" when nothing does."""
    from .lanes import lane_sources_moved

    if not (root / lane.artifact).is_file():
        return f"lane {lane.name!r}: no artifact at {lane.artifact}"
    moved = lane_sources_moved(root, lane, scope_paths, git)
    if moved:
        return (f"lane {lane.name!r}: {moved}, so the line numbers in {lane.artifact} are "
                f"stale — rerun `{_self()} coverage` to measure the tree as it is")
    return ""


def lane_views(root: Path, cfg, git=None) -> list[dict]:
    """{name, note, blackout} for every declared lane: why its line numbers are
    unusable ("" when its artifact still describes the tree), and whether that
    withholds every file's lines or only the files the note names.

    A lane whose stamp recorded digests withholds only the files whose bytes
    moved. Any other stale lane still withholds every file, because nothing
    says which of its lines survived. The report's banner reads this list.

    Without `git` the reads start together, scoped to the lanes' scope paths
    (lanes.staleness_reads).
    """
    from .lanes import recorded_sources, staleness_reads

    with staleness_reads(root, cfg.lanes, cfg.scope_paths, git) as facts:
        return [{"name": lane.name, "note": _artifact_state(root, lane, cfg.scope_paths, facts),
                 "blackout": recorded_sources(root, lane) is None}
                for lane in cfg.lanes]


def lane_states(root: Path, cfg, git=None) -> list[tuple[str, str]]:
    """(lane name, why its line numbers are unusable) for every declared lane,
    "" for a lane whose artifact still describes the tree."""
    return [(view["name"], view["note"]) for view in lane_views(root, cfg, git)]


def _judged(root: Path, cfg, git) -> tuple[str, SourceDrift]:
    """The note that withholds every file, from the lanes only git can judge,
    and the per-file drift of the lanes whose stamps recorded digests."""
    by_digest, by_git = _split_by_stamp(root, cfg.lanes)
    return _blackout(root, by_git, cfg.scope_paths, git), SourceDrift(root, by_digest)


def _split_by_stamp(root: Path, lanes) -> tuple[list, list]:
    """(lane, digests) for each lane whose stamp recorded them, and the lanes
    whose stamp did not."""
    from .lanes import recorded_sources

    by_digest, by_git = [], []
    for lane in lanes:
        sources = recorded_sources(root, lane)
        if sources is None:
            by_git.append(lane)
        else:
            by_digest.append((lane, sources))
    return by_digest, by_git


def _blackout(root: Path, lanes, scope_paths: dict, git) -> str:
    from .lanes import staleness_reads

    with staleness_reads(root, lanes, scope_paths, git) as facts:
        notes = [_artifact_state(root, lane, scope_paths, facts) for lane in lanes]
    return "; ".join(note for note in notes if note)


def load_uncovered(root: Path, cfg, git=None) -> MissingLines:
    """The dark lines the lane artifacts on disk report, or the note saying why not.

    A half-written artifact degrades to a note here, unlike in verify: naming a
    function's dark lines is a convenience nothing gates on, and it must never
    turn a question about the worklist into a tooling exit code.
    """
    from .errors import ToolError

    if not cfg.lanes:
        return MissingLines({}, "no [[lane]] declared, so no artifact can say which lines are dark")
    note, drift = _judged(root, cfg, git)
    if note:
        return MissingLines({}, note)
    try:
        return MissingLines(missing_by_path(root, cfg), "", drift)
    except ToolError as exc:
        return MissingLines({}, f"unreadable lane artifact: {exc}")
