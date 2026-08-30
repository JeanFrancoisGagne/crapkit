"""The one door to churn data, with git's tree walk cached behind it.

`git log --name-only` over a year of a large repo costs 6.5s, 5.8s of which is
git diffing every commit's tree, and worklist, next-item and coupling each paid
it in full on every invocation, at an unmoved HEAD. Every one of them reaches
git through this module, so `.crapkit/churn-cache-v2.json` has one writer.

The key is (HEAD sha, window months, UTC date, path format). The sha pins the
history; the window pins the command; the date is there because `--since=N
months ago` is evaluated against the wall clock, so yesterday's cache describes
a window one day wider than today's; the format marker retires maps whose
paths predate `git log --relative`. Anything else is a miss, and a miss
rebuilds.

A cache is disposable: unreadable, corrupt or unkeyable content reads as cold,
never as a crash. Uncommitted work is invisible to churn either way. The one
thing a sha does not pin is depth — deepening a shallow clone adds history
under an unmoved HEAD — and that resolves itself at the next date rollover.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from .churn import FileChurn, parse_git_log_lines
from .churn_log import RELATIVE_PATHS, has_cache, log_lines
from .errors import GitError
from .gitio import churn_log_lines, head_commit

# The format lives in the file name. Two crapkit versions on one working tree
# key different fields, each read the other's cache as cold, and each rewrote
# it — so every run of both rebuilt the map. Different formats, different files:
# neither invalidates the other and both stay warm. The key's own marker stays,
# for a format change that keeps the name.
CACHE_NAME = "churn-cache-v2.json"


def _window_lines(root: Path, months: int) -> Iterator[str]:
    """The raw window log a map rebuild parses.

    Read through the deflated log cache when one is on disk (free at an exact
    key, a cached..HEAD range walk otherwise); straight from git when none is.
    A map-only command never lays the log down — the commands that need its
    per-commit structure (brief, batches, coupling) already do."""
    if has_cache(root):
        return log_lines(root, months)
    return churn_log_lines(root, months)


def load_churn(root: Path, months: int) -> dict[str, FileChurn]:
    """Per-file churn for the window — from disk when the key still matches, else rebuilt."""
    path = root / ".crapkit" / CACHE_NAME
    key = _cache_key(root, months)
    cached = _read_cache(path, key)
    if cached is not None:
        return cached
    churn = parse_git_log_lines(_window_lines(root, months))
    _write_cache(path, key, churn)
    return churn


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _cache_key(root: Path, months: int) -> dict | None:
    """None when HEAD is unreadable — then there is nothing safe to key on."""
    try:
        head = head_commit(root)
    except GitError:
        return None
    # `paths` marks the format, not a question: maps built before --relative
    # hold top-relative paths in a subdirectory root, and must read as cold.
    return {"head": head, "months": months, "date": _utc_date(),
            "paths": RELATIVE_PATHS}


def _read_cache(path: Path, key: dict | None) -> dict[str, FileChurn] | None:
    if key is None:
        return None
    doc = _read_doc(path)
    if doc is None or doc.get("key") != key:
        return None
    return _decode(doc.get("files"))


def _read_doc(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _decode(files) -> dict[str, FileChurn] | None:
    """Rebuild the map from whatever JSON held under "files" — hence the wide except.

    Weights are round(_, 4) floats, which JSON round-trips exactly, so a warm
    read is byte-identical to the cold one it replaces.
    """
    try:
        return {p: FileChurn(int(c), int(a), float(w)) for p, (c, a, w) in files.items()}
    except (AttributeError, TypeError, ValueError):
        return None


def _write_cache(path: Path, key: dict | None, churn: dict[str, FileChurn]) -> None:
    """Best effort: a read-only .crapkit costs the speedup, never the command."""
    if key is None:
        return
    doc = {"key": key, "files": {p: [c.commits, c.authors, c.weight] for p, c in churn.items()}}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    except OSError:
        return
