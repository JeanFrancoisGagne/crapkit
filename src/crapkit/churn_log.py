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

Two things are stored beyond the author and author date a churn header needs.
The commit date (%ct) rides along on every header line, so an aged-out commit
can be expired without asking git, and the lines are served that way: coupling
only asks which lines open a commit and the churn parser reads either header
shape, so no reader pays a pass to strip it. And the key records the HEAD the log was built from, so a HEAD that
grew from it costs `git log cached..HEAD` instead of the window — 0.77 s instead
of 6.9 s at a day-old HEAD, for the same 628k lines. Commit date is not author
date: `--since` filters on the committer's clock while the recency weight uses
the author's, and a rebased commit has two different ones. The key also records
the cutoff the log was cut at, because that cutoff can move back: git's month
arithmetic puts 6 months before Aug 31 on Mar 3 and before Sep 1 on Mar 1, and
a log cut at the later cutoff cannot be re-dated to the earlier one.

A cache is disposable. An unreadable, torn or unkeyable log reads as cold, never
as a crash, and a HEAD the cached log is not an ancestor of (a rewind, a rebase,
a force-push) rebuilds rather than prepends.
"""
from __future__ import annotations

import codecs
import json
import zlib
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from functools import lru_cache
from itertools import chain
from operator import methodcaller
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO, NamedTuple

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
# Characters of log text per compress call and file write. One call and one
# write per line cost 0.36-0.57 s over a 635k-line log; a megabyte at a time,
# 0.17-0.22 s, for the same deflated text.
TEE_BATCH = 1 << 20
_TRIM = methodcaller("rstrip", "\n")
# The key's format marker: these logs hold root-relative paths (--relative),
# the only kind that joins against ls-files rows when the root sits below the
# repo top. A key without it names a top-relative log, and that one is cold —
# served OR refreshed, it would feed every consumer paths that match nothing.
RELATIVE_PATHS = "root-relative"


class Window(NamedTuple):
    """The window's log and the --since cutoff it was cut at. The cutoff is None
    when git would not name one, or when a laid-down log recorded none."""
    lines: Iterator[str]
    cutoff: int | None


def log_lines(root: Path, months: int) -> Iterator[str]:
    """The churn window's log at HEAD, streamed as it is stored: each header is
    %an, %at and %ct. The lines `git log` prints under LOG_FORMAT, off disk
    whenever the key still holds."""
    return _stored_window(root, months, None).lines


def stored_window(root: Path, months: int, head: str | None) -> Window:
    """The same log as of `head`, with the cutoff it was cut at: a caller that
    keys what it builds on a HEAD it read passes that HEAD, so a commit landing
    meanwhile stays out of both. None reads HEAD."""
    return _stored_window(root, months, head)


def walked_window(root: Path, months: int, head: str | None) -> Window:
    """The window as of `head`, straight from git in the stored shape, laying
    nothing down: for a reader that needs commit dates where no log is on disk
    yet. None walks whatever HEAD is when git starts."""
    cutoff = window_cutoff(root, months, head)
    return Window(_window_log(root, months, head, cutoff), cutoff)


# The per-process answers below are shared by the two copies a HEAD move
# brings forward: the commit table on a map miss, then the laid-down log when a
# coupling reader follows (brief, worklist --batches). Both ask the same three
# questions about the same two commits, and asking git twice doubled the spawns.

def commits_since(root: Path, base: str, head: str) -> Iterator[str]:
    """What `head` added on top of `base`, in the stored shape; nothing when
    they match. Walked once per pair in a process: two commits name one range."""
    if base == head:
        return iter(())
    return iter(_range_lines(root, base, head))


def grew_from(root: Path, base: str, head: str) -> bool:
    """Whether `head` is `base` or descends from it. Asked of git once per pair
    in a process: whether one commit descends from another never changes."""
    return base == head or _ancestry(root, base, head)


def window_cutoff(root: Path, months: int, head: str | None) -> int | None:
    """git's --since cutoff for the window, the commit date a commit must reach
    to stay in it; None when git will not name one.

    Read once per HEAD and UTC day in a process, so a carry and the refresh
    after it cut at the same cutoff. The cutoff moves with the clock, so a
    long-running process reads it again at the next HEAD or the next day, the
    two things every copy here is keyed on."""
    return _cutoff_at(root, months, head, _utc_date())


@lru_cache(maxsize=16)
def _cutoff_at(root: Path, months: int, head: str | None, date: str) -> int | None:
    return _window_cutoff(root, months)


@lru_cache(maxsize=16)
def _ancestry(root: Path, base: str, head: str) -> bool:
    return is_ancestor(root, base, head)


@lru_cache(maxsize=4)
def _range_lines(root: Path, base: str, head: str) -> tuple[str, ...]:
    return tuple(_range_log(root, base, head))


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


def _stored_window(root: Path, months: int, head: str | None) -> Window:
    """The laid-down log when its key answers; else the log carried forward or
    walked, and laid down as it streams past. Its stamp records the cutoff the
    lines were cut at, which a later refresh must not go below."""
    sweep_legacy(root)
    path = root / ".crapkit" / LOG_NAME
    key = _cache_key(root, months, head)
    stored = _read_key(path)
    served = _served(path, stored, key)
    if served is not None:
        return served
    if key is None:
        return walked_window(root, months, None)  # nothing to key a copy on
    cutoff = window_cutoff(root, months, key["head"])
    source = _refreshed(root, path, stored, key, cutoff)
    if source is None:
        source = _window_log(root, months, key["head"], cutoff)
    return Window(_tee(source, path, {**key, "cutoff": cutoff}), cutoff)


def _commit_time(line: str) -> int:
    """The committer timestamp off a header line; 0 when there is none to read."""
    raw = line.rstrip("\n").rpartition("\x02")[2]
    return int(raw) if raw.isdigit() else 0


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _cache_key(root: Path, months: int, head: str | None = None) -> dict | None:
    """Keyed on `head` when the caller read one, else on HEAD now. None when
    HEAD is unreadable — then there is nothing safe to key on."""
    if head is None:
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


def _answers(stored: dict | None, key: dict | None) -> bool:
    return key is not None and stored is not None and _key_fields(stored) == key


def _served(path: Path, stored: dict | None, key: dict | None) -> Window | None:
    """The stored log when it answers exactly this key, else None."""
    if not _answers(stored, key):
        return None
    lines = _read_log(path, stored)
    return None if lines is None else Window(lines, _stored_cutoff(stored))


def _stored_cutoff(stored: dict) -> int | None:
    """The cutoff a laid-down log was cut at; None for a log that recorded none."""
    cutoff = stored.get("cutoff")
    return cutoff if isinstance(cutoff, int) else None


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


def _refreshed(root: Path, path: Path, stored: dict | None, key: dict,
               cutoff: int | None) -> Iterator[str] | None:
    """The cached log carried forward to this HEAD and cutoff, or None when only
    a walk will do."""
    if not _refreshable(root, stored, key, cutoff):
        return None
    cached = _read_log(path, stored)
    if cached is None:
        return None
    return _within(chain(commits_since(root, stored["head"], key["head"]), cached), cutoff)


def _refreshable(root: Path, stored: dict | None, key: dict, cutoff: int | None) -> bool:
    """True only for a cached log this HEAD grew from: same window, same path
    format, cut at a cutoff no later than this one, and behind us."""
    if stored is None or not _same_window(stored, key) or not _cutoff_holds(stored, cutoff):
        return False
    return grew_from(root, str(stored.get("head")), key["head"])


def _same_window(stored: dict, key: dict) -> bool:
    """Same months, and root-relative paths: prepending to a top-relative log
    would stack fresh commits on wrong paths."""
    return stored.get("months") == key["months"] and stored.get("paths") == key["paths"]


def _cutoff_holds(stored: dict, cutoff: int | None) -> bool:
    """Whether the log holds every commit this cutoff keeps: it was cut at a
    cutoff no later. git's month arithmetic moves the cutoff back at a month
    end (6 months before Aug 31 is Mar 3, before Sep 1 is Mar 1), and a log cut
    at the later cutoff lacks the commits in between; only a walk has them."""
    laid = _stored_cutoff(stored)
    return cutoff is not None and laid is not None and laid <= cutoff


def _within(lines: Iterator[str], cutoff: int) -> Iterator[str]:
    """Commit blocks below the window cutoff, dropped whole with their paths."""
    keep = False
    for line in lines:
        if line.startswith("\x01"):
            keep = _commit_time(line) >= cutoff
        if keep:
            yield line


def _tee(source: Iterator[str], path: Path, stamp: dict) -> Iterator[str]:
    """Stream the log out and lay the compressed copy down as the lines go past.

    Written under an operation's own .part and renamed at the end, so a reader that
    stops early, a crash, or a second crapkit running beside this one never
    leaves a truncated log looking valid.
    """
    part = _open_part(path)
    if part is None:
        yield from source
        return
    comp = zlib.compressobj(1)
    try:
        for batch in _batches(source, TEE_BATCH):
            part.write(comp.compress(_encoded(batch)))
            yield from batch
        part.write(comp.flush())
    except BaseException:
        _discard(part)
        raise
    _keep(part, path, stamp)


def _batches(lines: Iterable[str], size: int) -> Iterator[list[str]]:
    """Runs of lines holding at least `size` characters; the last run holds the rest."""
    batch, held = [], 0
    for line in lines:
        batch.append(line)
        held += len(line)
        if held >= size:
            yield batch
            batch, held = [], 0
    if batch:
        yield batch


def _encoded(batch: list[str]) -> bytes:
    """Normalized: the seam promises lines, not their endings, and a stub that
    strips them would be written back as one long line."""
    return ("\n".join(map(_TRIM, batch)) + "\n").encode("utf-8")


def _open_part(path: Path) -> BinaryIO | None:
    """None when there is nowhere to write: a read-only .crapkit costs the
    speedup, never the command."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return NamedTemporaryFile(dir=path.parent, prefix=path.stem + ".",
                                  suffix=".part", delete=False)
    except OSError:
        return None


def _discard(part: BinaryIO) -> None:
    """Never raises: a cleanup error must not mask the failure that caused it."""
    try:
        part.close()
    except OSError:
        pass
    _drop(Path(part.name))


def _keep(part: BinaryIO, path: Path, stamp: dict) -> None:
    """Checksum our own bytes before publication. A competing rename can leave
    a mismatched pair, which reads as cold, but cannot attach our key to its log."""
    scratch = Path(part.name)
    try:
        part.close()
        blob = scratch.read_bytes()
        record = {**stamp, "size": len(blob), "crc": zlib.crc32(blob)}
        scratch.replace(path)
        _key_path(path).write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    except OSError:
        _drop(scratch)


def _window_log(root: Path, months: int, head: str | None, cutoff: int | None) -> Iterator[str]:
    """The whole window, from git. The expensive one: on a big repo 21 MB of
    text, so it is streamed and never held whole. --relative, because every
    consumer joins these paths against root-relative ls-files rows: log
    --name-only answers relative to the repo top, so a root one directory down
    (a monorepo member, a project nested in a worktree) read every scored file
    as zero-churn. At the top the flag changes nothing.

    Walked from `head`, the commit the caller keys its copy on, and not from
    whatever HEAD is by the time git starts: a commit landing in between would
    sit in a copy keyed on its parent, and the next range walk would add it
    again. Only a caller with no HEAD to key on walks HEAD itself.

    Cut at `cutoff`, the one the caller records, rather than at a second
    reading of "N months ago": the clock moves between the two, and at a month
    end it moves the cutoff back. Only when git named no cutoff does the walk
    read the clock itself."""
    since = f"--since={months} months ago" if cutoff is None else f"--max-age={cutoff}"
    return _git_lines(root, "log", "--relative", since, LOG_FORMAT, "--name-only",
                      *([head] if head else []))


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
