"""The marks file's git history: every past revision a command reads, one reader.

Every past revision of the marks file is read whole, through
`RatchetFile.committed` under the past-revision rule: UTF-8 with a byte-order
mark dropped, UTF-16 when a byte-order mark says so, any other byte as U+FFFD,
and never a refusal, since the file those bytes came from may be gone. No
reader parses patch lines. The readers:

- `ratchet report` and `brief` read mark ages and repayments off
  `marks_history`, the file's revisions parsed into marks, which
  `ratchet_report.mark_events` diffs.
- `ratchet report` on a deleted or emptied marks file reads `held_history`:
  the marks of the newest commit that held any are open, each aged by the
  history up to that commit, and the commit that deleted or emptied the file
  repays none.
- verify judges a deleted or emptied marks file against
  `newest_committed_marks`, the newest marks a commit since the baseline held.

The log walks no renames on its own (gitio.file_revisions says why), so when
the first commit that touched the marks file renamed it with `git mv`, this
module goes on reading the old path's revisions from that commit's parent,
back through every rename git pairs, and the renaming commit reads against the
old file's newest revision. A renamed marks file keeps every age and every
repayment.

gitio answers the git questions; this module decides what they mean for the
marks. A git read that fails raises GitError, and the stand-in walk names what
it was reading, since a history the clone does not hold is not a history that
never held marks.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from .errors import GitError
from .gitio import FileRevision, blob_at, commit_renames, file_revisions
from .keys import mark_key
from .ratchet import read_ratchet
from .ratchetfile import RatchetFile


class MarksRevision(NamedTuple):
    """One commit that touched the marks file, its revision parsed.

    `marks` is key -> crap as the commit left the file, the first mark under
    a key answering (keys.MarkIndex); None when the file was missing or held
    only blank lines. `before` is the same for the revision the commit
    changed. A merge changed nothing git shows, so its `before` is its own
    marks: `git log -p` prints no patch for a merge.
    """
    commit: str
    time: int
    marks: dict | None
    before: dict | None


def marks_history(root: Path, ratchet_file: str) -> list[MarksRevision]:
    """The marks file's revisions, oldest first, across every rename git
    pairs. [] when no commit touched it."""
    read = _RevisionReader(root / ratchet_file)
    return [MarksRevision(revision.commit, revision.time, read.marks(revision.data),
                          read.marks(revision.data if revision.merge else revision.before))
            for revision in _walk(root, ratchet_file)]


def held_history(root: Path, ratchet_file: str) -> list[MarksRevision]:
    """The history a missing or blank marks file reports from: each revision
    up to the newest one that held more than blank lines, and every later one
    as a clock tick that changes no mark and holds what that one held.

    Replayed as written, the commit that deleted or emptied the file dropped
    every mark, the burn-down counted each one as repaid, and `--enforce`
    passed a repayment quota the restored file fails. The newest revision
    that held marks says which are open: a replay cannot say which marks a
    merge kept, so a mark one branch repaid and the other loosened would read
    as open after the merge that kept the repayment.
    """
    history = marks_history(root, ratchet_file)
    last = next((index for index in range(len(history) - 1, -1, -1)
                 if history[index].marks is not None), -1)
    held = history[last].marks if last >= 0 else None
    return history[:last + 1] + [revision._replace(marks=held, before=held)
                                 for revision in history[last + 1:]]


def _walk(root: Path, ratchet_file: str) -> list[FileRevision]:
    """The file's revisions oldest first, back through every rename."""
    segments: list[list[FileRevision]] = []
    path, rev = ratchet_file, None
    while path is not None:
        segment = file_revisions(root, path, rev)
        segments.insert(0, segment)
        path = _renamed_from(root, segment[0].commit, path) if segment else None
        rev = f"{segment[0].commit}^" if path is not None else None
    return _stitched(segments)


def _stitched(segments: list[list[FileRevision]]) -> list[FileRevision]:
    """The segments, oldest first, as one history. Each later segment opens
    with the commit that renamed the file, whose log reads the file as
    created there; it reads instead against the old file's newest revision,
    so a pure rename changes no mark and moves only the clock."""
    revisions = list(segments[0])
    for segment in segments[1:]:
        old = revisions[-1].data if revisions else None
        revisions += [segment[0]._replace(before=old), *segment[1:]]
    return revisions


def _renamed_from(root: Path, commit: str, path: str) -> str | None:
    """The path `commit` renamed to `path`, or None when it created it."""
    renames = commit_renames(root, commit)
    return next((old for old, new in renames.items() if new == path), None)


class _RevisionReader:
    """Revisions read into marks, each distinct revision once.

    A revision decodes whole through RatchetFile.committed, and its rows read
    by ratchet.read_ratchet's rule. read_ratchet reads each row on its own, so
    a row that consecutive revisions share is read once: a history of a large
    marks file changes a few rows per commit.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._read: dict[bytes, dict | None] = {}
        self._rows = _Rows()

    def marks(self, data: bytes | None) -> dict | None:
        """key -> crap for one revision's bytes; None for no file or a blank one."""
        if data is None:
            return None
        if data not in self._read:
            self._read[data] = self._parsed(data)
        return self._read[data]

    def _parsed(self, data: bytes) -> dict | None:
        held = RatchetFile.committed(self._path, data)
        if held.blank:
            return None
        marks = filter(None, map(self._rows.__getitem__, (held.text or "").split("\n")))
        return dict(reversed(list(marks)))  # reversed: the first mark under a key wins


class _Rows(dict):
    """One row's (key, crap), or None for a row that holds no mark, read once."""

    def __missing__(self, row: str):
        entries = read_ratchet(row)[0]
        self[row] = (mark_key(entries[0]), entries[0].crap) if entries else None
        return self[row]


def newest_committed_marks(root: Path, base: str, ratchet_file: str):
    """(commit, marks) for the newest commit from `base` to HEAD whose marks file
    holds more than blank lines, or None when none does. A history this clone
    cannot read raises GitError naming what it was for, and in a shallow clone
    the fetch that brings the rest in (gitio.file_revisions)."""
    try:
        return _newest_marks(root, base, ratchet_file)
    except GitError as exc:
        raise GitError(f"cannot read the history of {ratchet_file} since the baseline "
                       f"{base[:11]} to stand in for the missing marks: {exc}") from exc


def _newest_marks(root: Path, base: str, ratchet_file: str):
    path = root / ratchet_file
    for revision in reversed(file_revisions(root, ratchet_file, f"{base}..HEAD")):
        held = _committed(path, revision.data)
        if held is not None:
            return revision.commit, held
    held = _committed(path, blob_at(root, base, ratchet_file))
    return None if held is None else (base, held)


def _committed(path: Path, data: bytes | None) -> RatchetFile | None:
    """The marks one revision held, or None when it held none or only blank
    lines. Blank reads as it does for the file on disk (RatchetFile.blank), so
    a byte-order mark and blank lines hold no marks."""
    held = None if data is None else RatchetFile.committed(path, data)
    return None if held is None or held.blank else held
