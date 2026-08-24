"""Watch-mode core. Pure: mtime snapshots in, changed paths out.

The polling loop in the CLI stays a thin shell around this; stdlib mtimes,
no filesystem-event dependency, works the same on every host.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path


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
