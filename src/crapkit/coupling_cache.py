"""The one door to ranked co-change pairs, with the pairing walk cached behind it.

Three commands rank the same pairs out of the same window: brief's coupling
section, `worklist --batches`, and `coupling` itself. Each one re-cut 12 months
of log into per-commit file sets and re-counted every combination on every
invocation, at an unmoved HEAD. On a 72k-commit consumer that is 0.95 s and
170 MB per run, all of it spent reproducing the list the last run already had.
The deflated churn log removed git's walk; it did not remove this one, because
the pairing reads that log's per-commit structure line by line either way.

The key is the churn map's key (HEAD sha, window months, UTC date, path format)
plus a digest of the tracked set. The tracked set is there because ranking
drops any pair naming a file `git ls-files` no longer lists, and ls-files reads
the INDEX, which moves without HEAD: `git rm --cached src/util.py` leaves the
sha alone and must still retire every pair naming util.py. The digest costs
6 ms over that consumer's 31,684 tracked paths, and every reader already holds
the list, so keying on it buys no spawn.

What is stored is the ranking at the DEFAULT thresholds, ordered, with no cut.
`--top` truncates that total order, so it reads from here. `--min-support` or
`--min-confidence` off the defaults ask a wider question than the file answers,
so those recompute: serving them a filtered subset would silently drop the
pairs the wider thresholds exist to surface.

A cache is disposable: unreadable, corrupt or unkeyable content reads as cold,
never as a crash. A cold run pays the write on top of the walk it was already
paying: 2 ms for that consumer's 2,368 pairs, 234 kB on disk.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from hashlib import blake2b
from pathlib import Path

from .churn_log import RELATIVE_PATHS, log_lines
from .coupling import change_coupling_lines
from .errors import GitError
from .gitio import head_commit

# The format lives in the file name, as it does for the churn map and log: a
# version keying another shape writes another file, so two installs on one tree
# both stay warm instead of rewriting each other's key on every run.
CACHE_NAME = "coupling-cache-v1.json"


def load_coupling(root: Path, months: int, tracked: Iterable[str]) -> list[dict]:
    """The window's ranked pairs at the default thresholds, uncut.

    `tracked` is the repo's live path set, the `git ls-files` rows the caller
    already has. It both filters the ranking and keys the file.
    """
    path = root / ".crapkit" / CACHE_NAME
    paths = sorted(tracked)
    key = _cache_key(root, months, paths)
    cached = _read_cache(path, key)
    if cached is not None:
        return cached
    pairs = change_coupling_lines(log_lines(root, months), top=None, tracked=set(paths))
    _write_cache(path, key, pairs)
    return pairs


def _tracked_digest(paths: list[str]) -> str:
    """One short name for the whole sorted tracked set.

    Length-delimited rather than joined on a separator, so no path holding the
    separator can spell another set's digest.
    """
    digest = blake2b(digest_size=16)
    for path in paths:
        digest.update(f"{len(path)}:".encode("utf-8"))
        digest.update(path.encode("utf-8", "surrogatepass"))
    return digest.hexdigest()


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _cache_key(root: Path, months: int, paths: list[str]) -> dict | None:
    """None when HEAD is unreadable: then there is nothing safe to key on."""
    try:
        head = head_commit(root)
    except GitError:
        return None
    return {"head": head, "months": months, "date": _utc_date(),
            "paths": RELATIVE_PATHS, "tracked": _tracked_digest(paths)}


def _read_cache(path: Path, key: dict | None) -> list[dict] | None:
    if key is None:
        return None
    doc = _read_doc(path)
    if doc is None or doc.get("key") != key:
        return None
    return _decode(doc.get("pairs"))


def _read_doc(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _decode(pairs) -> list[dict] | None:
    """Rebuild the ranking from whatever JSON held under "pairs", hence the wide
    except.

    List order is the ranking, kept as written. Confidences are round(_, 4)
    floats, which JSON round-trips exactly, so a warm read is byte-identical to
    the cold one it replaces.
    """
    try:
        return [{"files": [str(a), str(b)], "support": int(support),
                 "confidence": float(confidence)}
                for a, b, support, confidence in pairs]
    except (TypeError, ValueError):
        return None


def _write_cache(path: Path, key: dict | None, pairs: list[dict]) -> None:
    """Best effort: a read-only .crapkit costs the speedup, never the command."""
    if key is None:
        return
    doc = {"key": key,
           "pairs": [[*p["files"], p["support"], p["confidence"]] for p in pairs]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    except OSError:
        return
