"""The churn window's raw log, kept on disk deflated, and refreshed instead of rewalked.

`churn_cache` stores the DERIVED per-file map. Coupling needs the structure that
map threw away — which files shared a commit — so `brief` and `worklist
--batches` re-walked twelve months of history on every invocation: 6.9 s of git
diffing every commit's tree, at an unmoved HEAD, for a stream they read once.
Off disk the same 628k lines take 0.22 s.

This is that stream, written down. Deflated, because the log is 23 MB of highly
repetitive path text that compresses to 4.7 MB, and reading 4.7 MB is what makes
the copy worth having. The whole compressed file is read before the first line
goes out (its CRC is the only way to know a log is not half-written); the 23 MB
of text it holds never is.

Two things are stored that the served lines do not carry. The commit date (%ct)
rides along on every header line, so an aged-out commit can be expired without
asking git; and the key records the HEAD the log was built from, so a HEAD that
grew from it costs `git log cached..HEAD` instead of the window — 0.77 s instead
of 6.9 s at a day-old HEAD, for the same 628k lines. Commit date is not author
date: `--since` filters on the committer's clock while the recency weight uses
the author's, and a rebased commit has two different ones.

A cache is disposable. An unreadable, torn or unkeyable log reads as cold, never
as a crash, and a HEAD the cached log is not an ancestor of (a rewind, a rebase,
a force-push) rebuilds rather than prepends.
"""
from __future__ import annotations

import codecs
import json
import os
import zlib
from collections.abc import Iterator
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path
from typing import BinaryIO

from .errors import GitError
from .gitio import _git_lines, head_commit, is_ancestor

# Versioned like churn_cache's map, and for the same reason: a version that
# writes another key shape writes another file, so two installs on one tree
# both stay warm instead of rewriting each other's key on every run.
LOG_NAME = "churn-log-v2.z"
# The name 0.4.4 wrote, with this very key shape. Its pair is adopted where no
# v2 log stands yet and deleted once one does: a 4.4 MB file nothing will read
# again is not something to leave in every upgraded repo.
LEGACY_NAME = "churn-log.z"
LOG_FORMAT = "--format=%x01%an%x02%at%x02%ct"
CHUNK = 1 << 20
# The key's format marker: these logs hold root-relative paths (--relative),
# the only kind that joins against ls-files rows when the root sits below the
# repo top. A key without it names a top-relative log, and that one is cold —
# served OR refreshed, it would feed every consumer paths that match nothing.
RELATIVE_PATHS = "root-relative"


def log_lines(root: Path, months: int) -> Iterator[str]:
    """The churn window's log, streamed, in the format every consumer parses.

    Same lines as `gitio.churn_log_lines`, off disk whenever the key still holds.
    """
    return (_shipped(line) for line in _stored_lines(root, months))


def has_cache(root: Path) -> bool:
    """Whether a laid-down log exists to serve or refresh from, whatever its key.

    The per-file map uses this to choose its source: a map-only command must
    never pay the log's disk footprint into being, but ignoring a log already
    on disk would re-buy the walk the log exists to end."""
    path = root / ".crapkit" / LOG_NAME
    return path.is_file() and _key_path(path).is_file()


def sweep_legacy(root: Path) -> None:
    """0.4.4's log pair: renamed onto the v2 names where none stand yet, deleted
    once they do.

    Adopted rather than validated here — the two key shapes are the same, so the
    key check downstream decides warm or cold exactly as it would have. Both
    files go either way, because nothing reads the old names again.
    """
    old = root / ".crapkit" / LEGACY_NAME
    if not old.is_file():
        return
    path = root / ".crapkit" / LOG_NAME
    if not path.exists() and not _key_path(path).exists():
        _adopt(old, path)
    _drop(old)
    _drop(_key_path(old))


def _adopt(old: Path, path: Path) -> None:
    """Best effort, and either rename alone is safe: a log without its key reads
    as cold, and a key without its log reads as cold too."""
    try:
        _key_path(old).replace(_key_path(path))
        old.replace(path)
    except OSError:
        return


def _drop(path: Path) -> None:
    """Best effort: a read-only .crapkit keeps its litter, never loses a command."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _stored_lines(root: Path, months: int) -> Iterator[str]:
    """The same log with its commit dates still attached — the on-disk form."""
    sweep_legacy(root)
    path = root / ".crapkit" / LOG_NAME
    key = _cache_key(root, months)
    served = _cached(path, key)
    if served is not None:
        return served
    return _tee(_source(root, months, path, key), path, key)


def _source(root: Path, months: int, path: Path, key: dict | None) -> Iterator[str]:
    refreshed = _refreshed(root, months, path, key)
    if refreshed is not None:
        return refreshed
    return _window_log(root, months)


def _shipped(line: str) -> str:
    """An enriched header down to the shipped `%an\\x02%at`; path lines pass through."""
    if not line.startswith("\x01"):
        return line
    head, sep, _ = line.rstrip("\n").rpartition("\x02")
    return head + "\n" if sep else line


def _commit_time(line: str) -> int:
    """The committer timestamp off a header line; 0 when there is none to read."""
    raw = line.rstrip("\n").rpartition("\x02")[2]
    return int(raw) if raw.isdigit() else 0


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _cache_key(root: Path, months: int) -> dict | None:
    """None when HEAD is unreadable — then there is nothing safe to key on."""
    try:
        head = head_commit(root)
    except GitError:
        return None
    return {"head": head, "months": months, "date": _utc_date(),
            "paths": RELATIVE_PATHS}


def _key_path(path: Path) -> Path:
    return path.with_suffix(".json")


def _read_key(path: Path) -> dict | None:
    try:
        doc = json.loads(_key_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _key_fields(doc: dict) -> dict:
    return {field: doc.get(field) for field in ("head", "months", "date", "paths")}


def _cached(path: Path, key: dict | None) -> Iterator[str] | None:
    """The stored log when it answers exactly this key, else None."""
    stored = _read_key(path)
    if key is None or stored is None or _key_fields(stored) != key:
        return None
    return _read_log(path, stored)


def _read_log(path: Path, stored: dict) -> Iterator[str] | None:
    """Integrity first: the verdict on a torn log has to land before line one."""
    try:
        blob = path.read_bytes()
    except OSError:
        return None
    if len(blob) != stored.get("size") or zlib.crc32(blob) != stored.get("crc"):
        return None
    return _inflate(blob)


def _inflate(blob: bytes) -> Iterator[str]:
    """Inflate a chunk at a time and hand back whole lines: 4.7 MB of deflate is
    23 MB of text, and only the compressed side is ever resident.

    An INCREMENTAL utf-8 decoder, because a deflate chunk boundary can fall in
    the middle of a multi-byte character and author names are full of them.
    """
    dec = zlib.decompressobj()
    utf8 = codecs.getincrementaldecoder("utf-8")("replace")
    tail = ""
    for start in range(0, len(blob), CHUNK):
        text = tail + utf8.decode(dec.decompress(blob[start:start + CHUNK]))
        lines = text.split("\n")
        tail = lines.pop()
        yield from (line + "\n" for line in lines)
    if tail:
        yield tail


def _refreshed(root: Path, months: int, path: Path, key: dict | None) -> Iterator[str] | None:
    """The cached log carried forward to this HEAD, or None when only a walk will do."""
    stored = _read_key(path)
    if key is None or not _refreshable(root, stored, key):
        return None
    cached = _read_log(path, stored)
    cutoff = _window_cutoff(root, months)
    if cached is None or cutoff is None:
        return None
    return _within(chain(_fresh_commits(root, stored["head"], key["head"]), cached), cutoff)


def _refreshable(root: Path, stored: dict | None, key: dict) -> bool:
    """True only for a cached log this HEAD grew from: same window, same path
    format, and behind us."""
    if stored is None or stored.get("months") != key["months"]:
        return False
    if stored.get("paths") != key["paths"]:
        return False  # a top-relative log: prepending would stack fresh commits on wrong paths
    if stored.get("head") == key["head"]:
        return True
    return is_ancestor(root, str(stored.get("head")), key["head"])


def _fresh_commits(root: Path, base: str, head: str) -> Iterator[str]:
    """Nothing to walk when the log already ends at this HEAD — a re-dating is free."""
    if base == head:
        return iter(())
    return _range_log(root, base, head)


def _within(lines: Iterator[str], cutoff: int) -> Iterator[str]:
    """Commit blocks below the window floor, dropped whole with their paths."""
    keep = False
    for line in lines:
        if line.startswith("\x01"):
            keep = _commit_time(line) >= cutoff
        if keep:
            yield line


def _tee(source: Iterator[str], path: Path, key: dict | None) -> Iterator[str]:
    """Stream the log out and lay the compressed copy down as the lines go past.

    Written under a pid-unique .part and renamed at the end, so a reader that
    stops early, a crash, or a second crapkit running beside this one never
    leaves a truncated log looking valid.
    """
    part = _open_part(path, key)
    if part is None:
        yield from source
        return
    comp = zlib.compressobj(1)
    try:
        for line in source:
            # normalized: the seam promises lines, not their endings, and a
            # stub that strips them would be written back as one long line.
            part.write(comp.compress(line.rstrip("\n").encode("utf-8") + b"\n"))
            yield line
        part.write(comp.flush())
    except BaseException:
        _discard(part, path)
        raise
    _keep(part, path, key)


def _part_path(path: Path) -> Path:
    return path.with_suffix(f".{os.getpid()}.part")


def _open_part(path: Path, key: dict | None) -> BinaryIO | None:
    """None when there is nothing to key on or nowhere to write: a read-only
    .crapkit costs the speedup, never the command."""
    if key is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return _part_path(path).open("wb")
    except OSError:
        return None


def _discard(part: BinaryIO, path: Path) -> None:
    """Never raises: a cleanup error must not mask the failure that caused it."""
    try:
        part.close()
    except OSError:
        pass
    _part_path(path).unlink(missing_ok=True)


def _keep(part: BinaryIO, path: Path, key: dict | None) -> None:
    """Rename, then key. The key is written last and carries the log's size and
    CRC, so a log without one — or with one that describes other bytes — is cold."""
    try:
        part.close()
        _part_path(path).replace(path)
        blob = path.read_bytes()
        stamp = {**key, "size": len(blob), "crc": zlib.crc32(blob)}
        _key_path(path).write_text(json.dumps(stamp, sort_keys=True), encoding="utf-8")
    except OSError:
        _part_path(path).unlink(missing_ok=True)


def _window_log(root: Path, months: int) -> Iterator[str]:
    """The whole window, from git. The expensive one. --relative for the same
    reason gitio.churn_log_lines carries it: consumers join these paths against
    root-relative rows, and a root below the repo top matched nothing without it."""
    return _git_lines(root, "log", "--relative", f"--since={months} months ago",
                      LOG_FORMAT, "--name-only")


def _range_log(root: Path, base: str, head: str) -> Iterator[str]:
    """Only what HEAD added on top of the cached log."""
    return _git_lines(root, "log", "--relative", f"{base}..{head}", LOG_FORMAT, "--name-only")


def _window_cutoff(root: Path, months: int) -> int | None:
    """git's own reading of "N months ago": rev-parse prints the --max-age it would
    hand rev-list, so a re-dated log expires exactly what a fresh --since would.

    None when git will not answer — and then a refresh would be guessing at the
    window, which is what the full walk is for.
    """
    try:
        out = "".join(_git_lines(root, "rev-parse", f"--since={months} months ago"))
    except GitError:
        return None
    digits = out.strip().rpartition("=")[2]
    return int(digits) if digits.isdigit() else None
