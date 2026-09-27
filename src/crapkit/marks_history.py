"""The marks file's git history: every past revision a command reads, one reader.

`ratchet report` and `brief` read mark ages and repayments off the commits that
changed the marks file, and verify judges a deleted or emptied marks file
against the newest marks a commit since the baseline held. The log walks no
renames on its own (gitio.file_log says why), so when the first commit that
touched the marks file renamed it with `git mv`, this module goes on reading
the log of the old path from that commit's parent, back through every rename
git pairs. A renamed marks file keeps every age and every repayment.

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
    patches: list[tuple[int, str]] = []
    path, rev = ratchet_file, None
    while path is not None:
        segment, path, rev = _segment(root, path, rev)
        patches[:0] = segment
    return patches


def _segment(root: Path, path: str, rev: str | None):
    """The log of `path` up to `rev`, and where the history goes on: the path
    its first commit renamed to it and that commit's parent, or (None, None)
    when that commit created it. The renaming commit's patch adds every line
    under the new name; it reads instead as what the rename changed, so a pure
    rename changes no mark and moves only the clock."""
    log = file_log(root, path, rev)
    old = _renamed_from(root, log[0].commit, path) if log else None
    patches = [(entry.timestamp, entry.patch) for entry in log]
    if old is None:
        return patches, None, None
    first = log[0]
    patches[0] = (first.timestamp, _rename_patch(root, first.commit, old, path))
    return patches, old, f"{first.commit}^"


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
        data = blob_at(root, commit, ratchet_file)
        if data and data.strip():
            return commit, RatchetFile.committed(root / ratchet_file, data)
    return None
