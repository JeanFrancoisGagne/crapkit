"""The churn window's commits, kept on disk so a moved HEAD folds in only the new ones.

`churn_cache` keys its per-file map on HEAD, so the first churn read after any
commit misses it, and the miss parsed the whole window again: on a large
consumer repo, 3.3 s to re-read a 635,761-line log of which a handful of lines
were new. This file keeps what the map is computed from, `churn.WindowCommits`:
each commit's author, author date and commit date, and each path's commits. A
miss at a HEAD that grew from the stored one walks `git log stored..HEAD`,
expires what aged out, and computes the map from the table.

Read only on a map miss. Expiry reads the commit date at git's own --since
cutoff while the weights read the author date, so a carried table answers what
a full parse of the same commits would, byte for byte. A HEAD the table is not
behind (a rewind, a rebase, a force-push), another window, another path format,
or a cutoff earlier than the stored one is a full rebuild. git's month
arithmetic is what moves the cutoff back: 6 months before Aug 31 is Mar 3, and
before Sep 1 it is Mar 1. A shallow clone keeps no table: deepening one adds
history under an unmoved HEAD, and only a walk sees it. (A full clone later cut
shallow keeps the commits its table already holds, as the stored log does.)

The file is one key line, then the table as JSON. The key line carries the
body's size and CRC: a torn or corrupted table reads as cold, never as a
crash, and a key that does not answer is refused before the body is parsed.
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path
from typing import NamedTuple

from .churn import Commit, WindowCommits, fold
from .churn_log import commits_since, grew_from, window_cutoff
from .errors import GitError
from .gitio import is_shallow
from .gitpaths import PATH_FORMAT

# Versioned in the name like the map and the log: a version that writes another
# shape writes another file, and two installs on one tree both stay warm.
COMMITS_NAME = "churn-commits-v1.json"
_KEY_TYPES = {"head": str, "cutoff": int, "size": int, "crc": int}


class _Stored(NamedTuple):
    head: str
    cutoff: int
    table: WindowCommits


def carried_commits(root: Path, months: int, head: str | None) -> WindowCommits | None:
    """The stored table brought to `head`, or None when only a full parse will do."""
    stored = _stored(root, months, head)
    if stored is None:
        return None
    cutoff = window_cutoff(root, months, head)
    if cutoff is None or cutoff < stored.cutoff:
        return None
    table = stored.table
    table.carry(fold(commits_since(root, stored.head, head)))
    table.expire(cutoff)
    _write(root, months, head, cutoff, table)
    return table


def store_commits(root: Path, months: int, head: str | None, cutoff: int | None,
                  table: WindowCommits) -> None:
    """Keep a table parsed in full for the next miss to carry, stamped with the
    floor its log was cut at. Only a dated one: a commit without its commit
    date can never be expired. And only one cut at a known floor: stamped with
    a floor read later, it could claim commits the walk left out."""
    if head is None or cutoff is None or not table.dated or _shallow(root):
        return
    _write(root, months, head, cutoff, table)


def _shallow(root: Path) -> bool:
    """A clone git cannot vouch for reads as shallow: keeping no table costs a
    walk at the next miss, keeping a wrong one costs every miss after it."""
    try:
        return is_shallow(root)
    except GitError:
        return True


def _stored(root: Path, months: int, head: str | None) -> _Stored | None:
    """The stored table when `head` grew from it in this window, else None."""
    if head is None:
        return None
    stored = _read(root / ".crapkit" / COMMITS_NAME, months)
    if stored is None or not grew_from(root, stored.head, head):
        return None
    return stored


def _read(path: Path, months: int) -> _Stored | None:
    try:
        blob = path.read_bytes()
    except OSError:
        return None
    line, _, body = blob.partition(b"\n")
    key = _key(line, months)
    if key is None or not _intact(body, key):
        return None
    table = _decode(body)
    return None if table is None else _Stored(key["head"], key["cutoff"], table)


def _key(line: bytes, months: int) -> dict | None:
    """The key line when it names this window and path format and carries every
    field a reader relies on."""
    key = _json(line)
    if not isinstance(key, dict) or (key.get("months"), key.get("paths")) != (months, PATH_FORMAT):
        return None
    return key if _typed(key) else None


def _json(data: bytes):
    try:
        return json.loads(data)
    except ValueError:
        return None


def _typed(key: dict) -> bool:
    return all(isinstance(key.get(field), kind) for field, kind in _KEY_TYPES.items())


def _intact(body: bytes, key: dict) -> bool:
    return len(body) == key["size"] and zlib.crc32(body) == key["crc"]


def _decode(body: bytes) -> WindowCommits | None:
    """The CRC vouches for the bytes; a shape they do not decode to still reads
    as cold rather than raising."""
    try:
        doc = json.loads(body)
        commits = {seq: Commit(author, at, ct) for seq, author, at, ct in doc["commits"]}
        return WindowCommits(list(doc["authors"]), commits, dict(doc["files"]))
    except (KeyError, TypeError, ValueError):
        return None


def _write(root: Path, months: int, head: str, cutoff: int, table: WindowCommits) -> None:
    """Best effort: a read-only .crapkit costs the speedup, never the command. A
    write torn by a crash or a second crapkit fails its CRC and reads as cold."""
    body = json.dumps(_encoded(table), separators=(",", ":")).encode("utf-8")
    key = {"head": head, "months": months, "paths": PATH_FORMAT, "cutoff": cutoff,
           "size": len(body), "crc": zlib.crc32(body)}
    path = root / ".crapkit" / COMMITS_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(key, sort_keys=True).encode("utf-8") + b"\n" + body)
    except OSError:
        return


def _encoded(table: WindowCommits) -> dict:
    return {"authors": table.authors,
            "commits": [[seq, *commit] for seq, commit in table.commits.items()],
            "files": table.files}
