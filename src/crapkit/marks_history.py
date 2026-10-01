"""The marks file's git history: every past revision a command reads, one reader.

`ratchet report` and `brief` read mark ages and repayments off the commits that
changed the marks file. For a deleted or emptied marks file, verify judges
against the newest marks a commit since the baseline held, and ratchet report
reads as open the marks of the newest commit that held any, each aged by the
history up to that commit. Both read that commit's own revision of the file,
never a count or a replay of patch lines. The log walks no renames on its own
(gitio.file_log says why), so when the first commit that touched the marks
file renamed it with `git mv`, this module goes on reading the log of the old
path from that commit's parent, back through every rename git pairs. A
renamed marks file keeps every age and every repayment.

gitio answers the git questions; this module decides what they mean for the
marks. A git read that fails raises GitError, and the stand-in walk names what
it was reading, since a history the clone does not hold is not a history that
never held marks.
"""
from __future__ import annotations

from pathlib import Path

from .errors import GitError
from .gitio import (blob_at, commit_renames, commits_touching, file_log, revisions_patch,
                    shallow_fix)
from .ratchetfile import RatchetFile


def marks_history(root: Path, ratchet_file: str) -> list[tuple[int, str]]:
    """The marks file's commits, oldest first, as (timestamp, patch), across
    every rename git pairs. [] when no commit touched it."""
    return [(ts, patch) for ts, _, patch in _history(root, ratchet_file)]


def held_history(root: Path, ratchet_file: str) -> list[tuple]:
    """The history a missing or blank marks file reports from: each commit up
    to the newest one whose revision of the file held more than blank lines,
    and every later commit as a clock tick that changes no mark.

    Replayed as written, the commit that deleted or emptied the file dropped
    every mark, the burn-down counted each one as repaid, and `--enforce`
    passed a repayment quota the restored file fails.

    Each commit's own revision says whether it held marks, read newest first
    by the rule the stand-in reads with. A running count of the lines the
    patches added and removed said it wrong: `git log -p` prints no patch for
    a merge, so a line both sides of one added or removed counted twice.

    That newest commit's entry also carries the text its revision holds,
    (timestamp, patch, text): those are the open marks. Replaying the patches
    cannot say which marks a merge kept. A mark one branch repaid and the
    other loosened read as open after the merge that kept the repayment,
    because the loosening is the later commit and the merge prints no patch.
    """
    history = _history(root, ratchet_file)
    last, held = _newest_held(root, history)
    entries = [(ts, patch if index <= last else "") for index, (ts, _, patch) in enumerate(history)]
    if held is not None:
        entries[last] += (held.text,)
    return entries


def _newest_held(root: Path, history: list) -> tuple[int, RatchetFile | None]:
    """The index of the newest entry whose revision held marks, and that
    revision; (-1, None) when none did."""
    for index in range(len(history) - 1, -1, -1):
        held = _held(root, *history[index][1])
        if held is not None:
            return index, held
    return -1, None


def _history(root: Path, ratchet_file: str) -> list[tuple[int, tuple[str, str], str]]:
    """(timestamp, (commit, the file's path at it), patch) per commit, oldest first."""
    entries: list[tuple[int, tuple[str, str], str]] = []
    path, rev = ratchet_file, None
    while path is not None:
        segment, path, rev = _segment(root, path, rev)
        entries[:0] = segment
    return entries


def _segment(root: Path, path: str, rev: str | None):
    """The log of `path` up to `rev`, and where the history goes on: the path
    its first commit renamed to it and that commit's parent, or (None, None)
    when that commit created it. The renaming commit's patch adds every line
    under the new name; it reads instead as what the rename changed, so a pure
    rename changes no mark and moves only the clock."""
    log = file_log(root, path, rev)
    old = _renamed_from(root, log[0].commit, path) if log else None
    entries = [(entry.timestamp, (entry.commit, path), entry.patch) for entry in log]
    if old is None:
        return entries, None, None
    first = log[0]
    entries[0] = (first.timestamp, (first.commit, path),
                  _rename_patch(root, first.commit, old, path))
    return entries, old, f"{first.commit}^"


def _renamed_from(root: Path, commit: str, path: str) -> str | None:
    """The path `commit` renamed to `path`, or None when it created it."""
    renames = commit_renames(root, commit)
    return next((old for old, new in renames.items() if new == path), None)


def _rename_patch(root: Path, commit: str, old: str, new: str) -> str:
    """The marks one renaming commit changed: the old file at its parent
    against the new file at the commit."""
    return revisions_patch(blob_at(root, f"{commit}^", old) or b"",
                           blob_at(root, commit, new) or b"")


def newest_committed_marks(root: Path, base: str, ratchet_file: str):
    """(commit, marks) for the newest commit from `base` to HEAD whose marks file
    holds more than blank lines, or None when none does. A history this clone
    cannot read raises GitError naming what it was for."""
    try:
        return _newest_marks(root, base, ratchet_file)
    except GitError as exc:
        raise GitError(f"cannot read the history of {ratchet_file} since the baseline "
                       f"{base[:11]} to stand in for the missing marks: "
                       f"{exc}{shallow_fix(root)}") from exc


def _newest_marks(root: Path, base: str, ratchet_file: str):
    for commit in (*commits_touching(root, f"{base}..HEAD", ratchet_file), base):
        held = _held(root, commit, ratchet_file)
        if held is not None:
            return commit, held
    return None


def head_revision(root: Path, ratchet_file: str) -> str | None:
    """The text HEAD's revision of the marks file holds, or None when HEAD holds
    none or only blank lines."""
    held = _held(root, "HEAD", ratchet_file)
    return None if held is None else held.text


def _held(root: Path, commit: str, path: str) -> RatchetFile | None:
    """The marks file `commit` held at `path`, or None when it held none or
    only blank lines. Blank reads as it does for the file on disk
    (RatchetFile.blank), so a byte-order mark and blank lines hold no marks."""
    data = blob_at(root, commit, path)
    held = None if data is None else RatchetFile.committed(root / path, data)
    return None if held is None or held.blank else held
