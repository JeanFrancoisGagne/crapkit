"""Dark lines per function: which lines inside a span no lane ever ran.

The line-level truth is the one the diff-coverage check already reads — every
lane's missing-line set, intersected, so a line stays dark only when NO lane
ran it. What this module adds is a verdict on whether the artifacts on disk
still describe the working tree. Line numbers from an artifact built before the
last edit point at code that has moved, which is worse than no numbers at all:
then the lines are [] and a note names the lane to rerun.
"""
from __future__ import annotations

from pathlib import Path
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


def missing_by_path(root: Path, cfg) -> dict[str, set[int]]:
    """Union of the lanes' line-level truth; a file two lanes measured keeps a
    line dead only when NO lane ran it."""
    missing: dict[str, set[int]] = {}
    for lane in cfg.lanes:
        artifact = root / lane.artifact
        if not artifact.is_file():
            continue
        for path, lines in _parse_missing(lane, root, artifact).items():
            missing[path] = missing[path] & lines if path in missing else set(lines)
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
