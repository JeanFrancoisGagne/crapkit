"""Content-hash analysis cache. Pure partition/update; the shell owns file I/O.

Keys are file content hashes, never mtimes (checkout resets mtimes). The
fingerprint bundles everything that changes analysis output for identical
content (lizard pin, crapkit analysis version); a fingerprint change drops
the whole cache rather than serving stale records.
"""
from __future__ import annotations

from .merge import FunctionRecord


def partition_by_cache(
    hashes: dict[str, str],
    cache: dict,
    *,
    fingerprint: str,
) -> tuple[dict[str, list[FunctionRecord]], list[str]]:
    entries = cache.get("entries", {}) if cache.get("fp") == fingerprint else {}
    hits: dict[str, list[FunctionRecord]] = {}
    misses: list[str] = []
    for path in sorted(hashes):
        records = entries.get(hashes[path])
        if records is None:
            misses.append(path)
        else:
            hits[path] = records
    return hits, misses


def updated_cache(
    hashes: dict[str, str],
    records_by_path: dict[str, list[FunctionRecord]],
    *,
    fingerprint: str,
    stale_hashes: list[str] | None = None,
) -> dict:
    return {
        "fp": fingerprint,
        "entries": {hashes[path]: records for path, records in sorted(records_by_path.items())},
    }


def merged_cache(prior: dict, fresh: dict) -> dict:
    """Fold a partial run's entries into the prior map instead of replacing it.

    A run that analyzed a handful of files (rescore, watch, the pre-commit hook)
    knows nothing about the rest of the corpus, so its `entries` map is not a new
    cache but an addition to one. Merging leaves entries for content that has
    left the corpus behind; the whole-corpus runs (inventory, coverage) save their
    rebuilt map without merging, and that stays the one eviction point.

    A prior written under another fingerprint is not stale, it is wrong; drop it
    exactly as partition_by_cache does.
    """
    fp = fresh["fp"]
    prior_entries = prior.get("entries", {}) if prior.get("fp") == fp else {}
    return {"fp": fp, "entries": {**prior_entries, **fresh["entries"]}}
