"""Cache seam: file hashes + prior cache in, (hits, misses) out; fingerprint invalidates. Pure."""
from crapkit.cache import merged_cache, partition_by_cache, updated_cache
from crapkit.merge import FunctionRecord


def rec(path):
    return FunctionRecord(path, "f( )", 1, 2, 1, 1, 1, 2, 0, 0)


def test_unchanged_files_hit_and_changed_or_new_files_miss():
    cache = {"fp": "v1", "entries": {"h_a": [rec("src/a.ts")], "h_old_b": [rec("src/b.ts")]}}
    hits, misses = partition_by_cache({"src/a.ts": "h_a", "src/b.ts": "h_new_b", "src/c.ts": "h_c"}, cache, fingerprint="v1")
    assert hits == {"src/a.ts": [rec("src/a.ts")]}
    assert sorted(misses) == ["src/b.ts", "src/c.ts"]


def test_fingerprint_change_invalidates_everything():
    cache = {"fp": "v1", "entries": {"h_a": [rec("src/a.ts")]}}
    hits, misses = partition_by_cache({"src/a.ts": "h_a"}, cache, fingerprint="v2")
    assert hits == {}
    assert misses == ["src/a.ts"]


def test_updated_cache_holds_only_current_universe_hashes():
    fresh = updated_cache(
        {"src/a.ts": "h_a", "src/b.ts": "h_b"},
        {"src/a.ts": [rec("src/a.ts")], "src/b.ts": [rec("src/b.ts")]},
        fingerprint="v1",
        stale_hashes=["h_gone"],
    )
    assert fresh["fp"] == "v1"
    assert set(fresh["entries"]) == {"h_a", "h_b"}


def test_merged_cache_keeps_prior_entries_the_partial_run_never_saw():
    prior = {"fp": "v1", "entries": {"h_a": [rec("src/a.ts")], "h_b": [rec("src/b.ts")]}}
    fresh = updated_cache({"src/b.ts": "h_b2"}, {"src/b.ts": [rec("src/b.ts")]}, fingerprint="v1")
    merged = merged_cache(prior, fresh)
    assert set(merged["entries"]) == {"h_a", "h_b", "h_b2"}
    assert merged["fp"] == "v1"


def test_merged_cache_drops_a_prior_written_under_another_fingerprint():
    prior = {"fp": "v1", "entries": {"h_a": [rec("src/a.ts")]}}
    fresh = updated_cache({"src/b.ts": "h_b"}, {"src/b.ts": [rec("src/b.ts")]}, fingerprint="v2")
    merged = merged_cache(prior, fresh)
    assert merged == {"fp": "v2", "entries": {"h_b": [rec("src/b.ts")]}}


def test_merged_cache_treats_a_corrupt_prior_as_empty():
    fresh = updated_cache({"src/a.ts": "h_a"}, {"src/a.ts": [rec("src/a.ts")]}, fingerprint="v1")
    assert merged_cache({}, fresh) == fresh


def test_cache_round_trips_records_through_disk(tmp_path):
    from crapkit.analyze import load_cache, save_cache
    path = tmp_path / "cache.json"
    cache = {"fp": "v1", "entries": {"h_a": [rec("src/a.ts"), rec("src/a.ts")]}}
    save_cache(path, cache)
    assert load_cache(path) == cache


def test_a_reloaded_cache_saves_to_the_same_bytes(tmp_path):
    """load_cache consumes the parsed rows out of order; the file must not notice."""
    from crapkit.analyze import load_cache, save_cache
    first, second = tmp_path / "one.json", tmp_path / "two.json"
    cache = {"fp": "v1", "entries": {"h_b": [rec("src/b.ts")], "h_a": [rec("src/a.ts")]}}
    save_cache(first, cache)
    save_cache(second, load_cache(first))
    assert first.read_bytes() == second.read_bytes()


def test_save_cache_skips_the_rewrite_when_the_merged_cache_matches_disk(tmp_path):
    from crapkit.analyze import load_cache, save_cache
    path = tmp_path / "cache.json"
    cache = {"fp": "v1", "entries": {"h_a": [rec("src/a.ts")]}}
    save_cache(path, cache)
    prior = load_cache(path)
    path.write_text("SENTINEL", encoding="utf-8")  # any rewrite would clobber this
    save_cache(path, cache, prior=prior)
    assert path.read_text(encoding="utf-8") == "SENTINEL"


def test_save_cache_writes_whenever_the_entry_map_moved(tmp_path):
    from crapkit.analyze import load_cache, save_cache
    path = tmp_path / "cache.json"
    save_cache(path, {"fp": "v1", "entries": {"h_a": [rec("src/a.ts")]}})
    prior = load_cache(path)
    grown = {"fp": "v1", "entries": {**prior["entries"], "h_b": [rec("src/b.ts")]}}
    save_cache(path, grown, prior=prior)
    assert load_cache(path) == grown


def test_analysis_version_is_part_of_the_fingerprint():
    from crapkit.analyze import ANALYSIS_VERSION, fingerprint
    assert f"analysis={ANALYSIS_VERSION}" in fingerprint()
