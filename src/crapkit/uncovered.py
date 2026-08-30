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


class MissingLines(NamedTuple):
    """Per-file dead lines, plus the reason there are none to report.

    A populated `note` overrides everything: no span gets lines, because the
    only lines available would be the wrong ones.
    """
    by_path: dict[str, set[int]]
    note: str

    def in_span(self, path: str, start: int, end: int) -> list[int]:
        if self.note:
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
        if self.note:
            return self.note
        if path in self.by_path:
            return ""
        return _absent_note(path, flag)


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
    """One lane's missing lines, read off the file.

    Off the file, not out of a string: every declared lane's artifact would
    otherwise be decoded whole, one after another, on one heap.

    An unrecognised parser raises, in the same words `lanes._read_and_parse`
    uses. This dispatch used to fall through to the coverage.py reader, so the
    day a third parser joins SUPPORTED_PARSERS its lane would land here and
    blame a perfectly good artifact for being unparseable.
    """
    from . import covstream
    from .errors import ToolError

    if lane.parser == "istanbul":
        return covstream.parse_istanbul_missing_file(artifact, repo_root=str(root))
    if lane.parser == "coveragepy":
        return covstream.parse_coveragepy_missing_file(artifact, path_prefix=lane.path_prefix)
    raise ToolError(f"lane {lane.name!r}: parser {lane.parser!r} not implemented yet")


# --- the fold a lane's own walk can fill in --------------------------------
#
# An istanbul lane decodes every member of its artifact to score it. The dead
# lines fall out of that same decode, so the lane hands them here instead of
# leaving this module to reopen the file and decode it all again.

_FOLD_LOCK = Lock()
_folded: dict[str, set[int]] = {}
_folded_from: set[tuple] = set()


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


def fold_dead_lines(artifact: Path, dead: dict[str, set[int]]) -> None:
    """Take one lane's dead lines into the run's fold so the lane can drop them.

    Handing the map over immediately is the point. Keeping all 13 lanes' maps
    alive until the reader folded them peaked at 204.9 MB against 100.3 MB
    folding as they arrive, on the same 13 artifacts. Lanes parse on a thread
    pool, hence the lock.
    """
    key = _artifact_key(artifact)
    if key is None:
        return
    with _FOLD_LOCK:
        _fold_into(_folded, dead)
        _folded_from.add(key)


def _take_folded(wanted: set) -> tuple[dict[str, set[int]], set]:
    """The fold and the artifact keys it covers, or ({}, set()) when it speaks
    for an artifact `wanted` does not name.

    Emptied either way, and handed over rather than copied, because the caller
    folds the remaining lanes straight into it. An artifact rewritten since its
    walk keys differently and so lands outside `wanted`: the whole fold is then
    dropped and every lane read again, rather than one stale map being served.
    """
    global _folded, _folded_from

    with _FOLD_LOCK:
        folded, sources = _folded, _folded_from
        _folded, _folded_from = {}, set()
    if not sources or not sources <= wanted:
        return {}, set()
    return folded, sources


def _lane_artifacts(root: Path, cfg) -> list[tuple]:
    """(lane, artifact, key) for every lane whose artifact is on disk."""
    found = []
    for lane in cfg.lanes:
        artifact = root / lane.artifact
        if artifact.is_file():
            found.append((lane, artifact, _artifact_key(artifact)))
    return found


def missing_by_path(root: Path, cfg) -> dict[str, set[int]]:
    """Union of the lanes' line-level truth; a file two lanes measured keeps a
    line dead only when NO lane ran it.

    A lane whose artifact was walked for its coverage this run already handed
    its dead lines over; the rest are read off the file here. Both routes fold
    the same way and the fold is order-independent, so which lane took which
    route cannot move the answer.
    """
    lanes = _lane_artifacts(root, cfg)
    missing, covered = _take_folded({key for _, _, key in lanes})
    for lane, artifact, key in lanes:
        if key not in covered:
            _fold_into(missing, _parse_missing(lane, root, artifact))
    return missing


def _artifact_state(root: Path, lane, scope_paths: dict, git) -> str:
    """What stops this lane's artifact from naming line numbers, or "" when nothing does."""
    from .lanes import lane_unchanged

    if not (root / lane.artifact).is_file():
        return f"lane {lane.name!r}: no artifact at {lane.artifact}"
    if not lane_unchanged(root, lane, scope_paths, git):
        return (f"lane {lane.name!r}: files in its scopes changed since {lane.artifact} "
                "was written (uncommitted edits count), so its line numbers are stale — "
                "commit or revert them, then rerun `crapkit coverage`")
    return ""


def lane_states(root: Path, cfg, git=None) -> list[tuple[str, str]]:
    """(lane name, why its line numbers are unusable) for every declared lane,
    "" for a lane whose artifact still describes the tree.

    `load_uncovered` joins these into one note and throws the per-lane detail
    away, which is the right shape for "no lines for any path" and the wrong one
    for a reader who wants to know WHICH lane to rerun. The report's staleness
    banner reads this list; the joined note is built from it, so a lane cannot be
    fresh in one and stale in the other.
    """
    from .gitio import GitFacts

    facts = git if git is not None else GitFacts(root)
    return [(lane.name, _artifact_state(root, lane, cfg.scope_paths, facts))
            for lane in cfg.lanes]


def _staleness_note(root: Path, cfg, git) -> str:
    return "; ".join(note for _, note in lane_states(root, cfg, git) if note)


def load_uncovered(root: Path, cfg, git=None) -> MissingLines:
    """The dark lines the lane artifacts on disk report, or the note saying why not.

    A half-written artifact degrades to a note here, unlike in verify: naming a
    function's dark lines is a convenience nothing gates on, and it must never
    turn a question about the worklist into a tooling exit code.
    """
    from .errors import ToolError
    from .gitio import GitFacts

    if not cfg.lanes:
        return MissingLines({}, "no [[lane]] declared, so no artifact can say which lines are dark")
    note = _staleness_note(root, cfg, git or GitFacts(root))
    if note:
        return MissingLines({}, note)
    try:
        return MissingLines(missing_by_path(root, cfg), "")
    except ToolError as exc:
        return MissingLines({}, f"unreadable lane artifact: {exc}")
