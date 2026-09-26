"""Watch-mode core: which watched files hold other bytes than the last poll saw.

An mtime is the fast path and content is the verdict. A file whose mtime did not
move is taken as unchanged without being read. A file whose mtime moved, or
that appeared, has changed only when its git blob id differs from the one
recorded for it (`digests`). The ids come from the content record
(`lane_sources.record`), the one rule lane stamps use too: git's index answers
for a tracked file git calls unchanged, and git hashes the rest through the
repo's filters. So a touch, an editor saving the same bytes, or a checkout
rewriting a file's line endings that git stores as the same blob rescores
nothing, and a deleted file counts as changed.

The one change a poll cannot see is new content written under the file's old
mtime (cp -p, touch -r, robocopy, tar -x): the named limit the analysis stat
index shares. Hashing every file on every poll would close it, and what that
costs is measured in 0.9.0 with the rest of the content record.

The polling loop in the CLI stays a thin shell around this; stdlib mtimes,
no filesystem-event dependency, works the same on every host.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import NamedTuple

from .errors import GitError
from .lane_sources import record
from .named import first_few

# Pathspec characters a git read may carry. Windows caps a whole command line
# at 32,767, so a list past this reads the whole index instead of naming files.
_PATHSPEC_CHARS = 16_000


def digests(root: Path, paths) -> dict[str, str]:
    """path -> its git blob id, for each of `paths` that reads as a file now.
    One that cannot be opened is left out, so a poll reads it again, and it
    never reaches git, whose hash-object would refuse the whole batch over it.
    Raises GitError."""
    readable = [path for path in paths if _opens(root / path)]
    return record(root, readable, git_reach(readable))


def _opens(path: Path) -> bool:
    """Whether the file opens for reading now: False for one that is gone, a
    directory, or one the OS or another process refuses."""
    try:
        with path.open("rb"):
            return True
    except OSError:
        return False


def git_reach(paths: list[str]) -> tuple[str, ...]:
    """The pathspecs a git read over `paths` takes: the paths themselves while
    they fit on a command line, none (the whole index) past that."""
    fits = sum(len(path) + 3 for path in paths) <= _PATHSPEC_CHARS
    return tuple(paths) if fits else ()


def _read(root: Path, paths, fault: str) -> tuple[dict[str, str], str]:
    """The blob ids of `paths` and "", or none and git's error when git cannot
    give them. The error is named when it is not `fault`, the one the last
    poll already named."""
    try:
        return digests(root, paths), ""
    except GitError as exc:
        error = str(exc)
    if error != fault:
        print(f"crapkit watch: git could not read the content of {first_few(sorted(paths))} ({error}); "
              "fix what git reports. Until git answers, each poll asks again and rescores "
              "nothing it could not read", flush=True)
    return {}, error


def _stat_mtime(path: Path) -> float | None:
    """One stat, or None for anything that is not a readable regular file.

    `is_file()` followed by `stat()` paid the syscall TWICE per tracked file,
    every poll interval. The S_ISREG check keeps directories out, which is the
    only thing is_file() was buying.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime if stat.S_ISREG(st.st_mode) else None


def _entry_mtime(entry: os.DirEntry) -> float | None:
    """The same answer for an already-listed name. On Windows the times came
    with the listing, so this costs no syscall at all; the guard is for the
    hosts where it does one, and for a name that died between the two."""
    try:
        st = entry.stat()
    except OSError:
        return None
    return st.st_mtime if stat.S_ISREG(st.st_mode) else None


def _by_directory(files: list[str]) -> dict[str, dict[str, str]]:
    """{directory: {basename: path}} — the grouping one listing can answer.

    Paths are the repo-relative, slash-separated ones git reports. Anything
    shaped otherwise simply groups under the root and misses its listing, which
    costs it a stat and nothing else.
    """
    grouped: dict[str, dict[str, str]] = {}
    for rel in files:
        parent, _, base = rel.rpartition("/")
        grouped.setdefault(parent, {})[base] = rel
    return grouped


def _keep_wanted(entry: os.DirEntry, names: dict[str, str], out: dict[str, float]) -> None:
    """A listing walks a whole directory; only the tracked names are wanted,
    and only they are worth a stat on the hosts where one is charged."""
    rel = names.get(entry.name)
    if rel is None:
        return
    mtime = _entry_mtime(entry)
    if mtime is not None:
        out[rel] = mtime


def _list_directory(directory: Path, names: dict[str, str], out: dict[str, float]) -> None:
    """Everything one listing can answer. A directory that cannot be read (gone,
    refused, replaced by a file) answers nothing and leaves every name in it to
    its own stat, so it costs speed and never an entry."""
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                _keep_wanted(entry, names, out)
    except OSError:
        return


def _listed_mtimes(root: Path, grouped: dict[str, dict[str, str]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for parent, names in grouped.items():
        _list_directory(root / parent if parent else root, names, out)
    return out


def snapshot_mtimes(root: Path, files: list[str]) -> dict[str, float]:
    """The mtime of every tracked file that is one; the rest simply absent.

    One os.scandir per DIRECTORY, not one os.stat per FILE. On Windows the
    times ride in the listing itself, so a 14,152-file tree polled every two
    seconds dropped from 275 ms of stats to 46 ms of listings; elsewhere the
    listing at least answers from a directory handle instead of walking the
    whole path again once per file. Whatever the listing did not answer for
    still gets its own stat, so a newborn file, a name the filesystem spells
    with different case, and an unreadable directory all behave exactly as they
    did when every file was stat-ed.

    The result is built in `files` order, not listing order: two polls of one
    unchanged tree have to produce the same mapping, and no filesystem promises
    the order it enumerates in.
    """
    listed = _listed_mtimes(root, _by_directory(files))
    out: dict[str, float] = {}
    for rel in files:
        mtime = listed.get(rel)
        if mtime is None:
            mtime = _stat_mtime(root / rel)
        if mtime is not None:
            out[rel] = mtime
    return out


def changed_paths(before: dict[str, float], after: dict[str, float]) -> list[str]:
    """New, modified, and deleted paths between two snapshots, sorted."""
    moved = {p for p, m in after.items() if before.get(p) != m}
    return sorted(moved | (set(before) - set(after)))


class Snapshot(NamedTuple):
    """One poll's view: each file's mtime, the content recorded for it, and the
    git error its content read hit, "" when git answered.

    A file whose bytes could not be read has no mtime here, so the next poll
    reads it again, and it keeps the content recorded before, so a read that
    failed is never taken for a change."""
    mtimes: dict[str, float]
    content: dict[str, str]
    fault: str = ""


def snapshot(root: Path, files: list[str]) -> Snapshot:
    """The first poll: every watched file's mtime and content."""
    mtimes = snapshot_mtimes(root, files)
    content, fault = _read(root, list(mtimes), "")
    return Snapshot(_settled(mtimes, content, ()), content, fault)


def poll(root: Path, files: list[str], before: Snapshot) -> tuple[Snapshot, list[str]]:
    """The next snapshot, and the files whose content changed since `before`,
    deleted ones included, sorted."""
    mtimes = snapshot_mtimes(root, files)
    stirred = changed_paths(before.mtimes, mtimes)
    fresh, fault = _read(root, [path for path in stirred if path in mtimes], before.fault)
    content = {**_standing(before.content, mtimes, fresh), **fresh}
    moved = [path for path in stirred if before.content.get(path) != content.get(path)]
    return Snapshot(_settled(mtimes, content, set(stirred) - set(fresh)), content, fault), moved


def _standing(recorded: dict[str, str], mtimes: dict[str, float],
              fresh: dict[str, str]) -> dict[str, str]:
    """The recorded content that still stands: every file still there that no
    new read replaced, whether its mtime held or its read failed."""
    return {path: digest for path, digest in recorded.items()
            if path in mtimes and path not in fresh}


def _settled(mtimes: dict[str, float], content: dict[str, str], unread) -> dict[str, float]:
    """The mtimes the next poll compares against: those of the files whose
    content is recorded, except a file this poll could not read, which the
    next poll has to read again."""
    return {path: mtime for path, mtime in mtimes.items()
            if path in content and path not in unread}
