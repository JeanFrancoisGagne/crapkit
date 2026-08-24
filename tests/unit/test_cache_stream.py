"""save_cache writes the cache file one entry at a time.

Building the whole document first cost three full copies of the corpus alive at
once: the list-of-lists rebuild, the 18.6 MB string json.dumps returns, and the
encode on the way out. Measured on the consumer repo: 54.6 MB of the parent's peak, on
every cold or partial run.

The file is a determinism contract, so the bytes are pinned against the
json.dumps of the whole document that used to produce them.
"""
import json

import crapkit.analyze as analyze
from crapkit.analyze import load_cache, save_cache
from crapkit.merge import FunctionRecord


def rec(i: int, path: str = "src/a.ts") -> FunctionRecord:
    return FunctionRecord(path, f"f{i}( x )", i, i + 8, i + 1, i, i, 8, 1, 2, i)


def _legacy_bytes(cache: dict) -> bytes:
    """analyze.save_cache as it stood before the streaming rewrite."""
    document = {"fp": cache["fp"],
                "entries": {h: [list(r) for r in rows] for h, rows in cache["entries"].items()}}
    return json.dumps(document, sort_keys=True).encode("utf-8")


def _cache(entries: dict, fp: str = "crapkit=0.1.0;analysis=3;lizard=1.24.0") -> dict:
    return {"fp": fp, "entries": entries}


def test_the_written_bytes_match_the_whole_document_dump(tmp_path):
    cache = _cache({f"h{i:02d}": [rec(i), rec(i + 1)] for i in range(20)})
    path = tmp_path / "cache.json"

    save_cache(path, cache)

    assert path.read_bytes() == _legacy_bytes(cache)


def test_unsorted_keys_still_land_sorted(tmp_path):
    """Entry order is a hash-map accident; the file must not inherit it."""
    cache = _cache({"h_z": [rec(1)], "h_a": [rec(2)], "h_m": []})
    path = tmp_path / "cache.json"

    save_cache(path, cache)

    assert path.read_bytes() == _legacy_bytes(cache)


def test_a_name_that_needs_escaping_is_escaped_the_same_way(tmp_path):
    """Non-ASCII identifiers and Windows path separators both go through the
    same escaping the whole-document dump applied."""
    cache = _cache({"h1": [FunctionRecord("src\\wärme.ts", 'g( "q" )', 1, 2, 1, 1, 1, 2, 0, 0, 0)]})
    path = tmp_path / "cache.json"

    save_cache(path, cache)

    assert path.read_bytes() == _legacy_bytes(cache)
    assert load_cache(path) == cache


def test_an_empty_cache_writes_an_empty_document(tmp_path):
    path = tmp_path / "cache.json"

    save_cache(path, _cache({}))

    assert path.read_bytes() == _legacy_bytes(_cache({}))


def test_no_call_serializes_the_whole_document(tmp_path, monkeypatch):
    """The mechanism: entries reach json one at a time, so the 18.6 MB string is
    never built. Anything handed the document itself defeats the change."""
    handed = []
    real = json.dumps

    def spy(obj, **kwargs):
        handed.append(obj)
        return real(obj, **kwargs)

    monkeypatch.setattr(analyze.json, "dumps", spy)
    save_cache(tmp_path / "cache.json", _cache({"h1": [rec(1)], "h2": [rec(2)]}))

    whole = [o for o in handed if isinstance(o, dict) and "entries" in o]
    assert not whole, "the whole document was serialized in one call"


def test_the_file_still_round_trips_through_load_cache(tmp_path):
    cache = _cache({"h1": [rec(1), rec(2)], "h2": [], "h3": [rec(3, "pylib/mod.py")]})
    path = tmp_path / "cache.json"

    save_cache(path, cache)

    assert load_cache(path) == cache


def test_writing_the_same_cache_twice_produces_the_same_bytes(tmp_path):
    cache = _cache({f"h{i}": [rec(i)] for i in range(8)})
    first, second = tmp_path / "one.json", tmp_path / "two.json"

    save_cache(first, cache)
    save_cache(second, load_cache(first))

    assert first.read_bytes() == second.read_bytes()
