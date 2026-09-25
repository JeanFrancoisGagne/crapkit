"""The marks file's git history: every past revision a command reads, one reader.

`ratchet report` and `brief` read mark ages and repayments off the commits that
changed the marks file, and verify judges a deleted or emptied marks file
against the newest marks a commit since the baseline held. The log walks no
renames, so a marks file renamed with `git mv` starts its history at the
rename; this module names that commit once, so every command that counts from
it prints the same line.

gitio answers the git questions; this module decides what they mean for the
marks. A git read that fails raises GitError, and the stand-in walk names what
it was reading, since a history the clone does not hold is not a history that
never held marks.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from .errors import GitError
from .gitio import blob_at, commit_renames, commits_touching, file_log, shallow_fix
from .ratchetfile import RatchetFile


class MarksHistory(NamedTuple):
    """The marks file's commits, oldest first, as (timestamp, patch); the commit
    its history starts at, None when no commit touched it; and the path that
    commit renamed to the marks file, None when it created it."""
    patches: list[tuple[int, str]]
    first: str | None
    renamed_from: str | None


def marks_history(root: Path, ratchet_file: str) -> MarksHistory:
    log = file_log(root, ratchet_file)
    first = log[0].commit if log else None
    return MarksHistory([(entry.timestamp, entry.patch) for entry in log], first,
                        first and _renamed_from(root, first, ratchet_file))


def _renamed_from(root: Path, commit: str, ratchet_file: str) -> str | None:
    """The path `commit` renamed to the marks file, or None when it created it."""
    renames = commit_renames(root, commit)
    return next((old for old, new in renames.items() if new == ratchet_file), None)


def rename_warning(ratchet_file: str, history: MarksHistory) -> str | None:
    """The one line a reader of mark ages prints when the history starts at a
    rename, or None when the marks file was never renamed."""
    if not history.renamed_from:
        return None
    return (f"warning: {ratchet_file}'s history starts at {history.first[:11]}, the commit "
            f"that renamed it from {history.renamed_from}, so mark ages and repayments "
            "count from there")


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
