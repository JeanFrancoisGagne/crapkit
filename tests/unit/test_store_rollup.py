"""The per-run rollup: trend and report stop rescanning history to add it up.

Runs are immutable once written, so the three numbers a run contributes to a
trend never change. They were recomputed from scratch on every invocation
anyway: two GROUP BYs over every scored row of every run, 2.48 M rows on the
flagship consumer's store, for thirty numbers.

What this pins is the part that could go wrong quietly. The cache key has to
carry the CEILING, or a config change prints yesterday's over_target. A run
that scored no rows has to count as done, or it is rescanned forever. And a
pruned run's rollup has to die with it, or an id AUTOINCREMENT hands out again
serves another run's totals.
"""
import sqlite3

import pytest
from crapkit.digest import totals
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

SCOPES = ("api", "ui")


def scored(n: int, crap0: float = 2.0) -> list:
    return [ScoredRow(SCOPES[i % 2], f"src/m{i % 3}.py", f"f{i % 5}( a )",
                      1 + i, 9 + i, 3 + i % 9, 3 + i % 9, 3 + i % 9, 5, 1, 1,
                      0.25, "measured", crap0 + i * 1.5, "add-tests", 0)
            for i in range(n)]


def traced(store, call) -> list[str]:
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    try:
        call()
    finally:
        store._conn.set_trace_callback(None)
    return seen


def scans(store, call) -> list[str]:
    """The statements that read the function rows. Everything this change is
    about is whether a second invocation issues any."""
    return [s for s in traced(store, call) if "FROM functions f" in s]


def seeded(tmp_path, runs: int = 2) -> SnapshotStore:
    store = SnapshotStore(tmp_path / "crap.sqlite")
    for i in range(runs):
        store.write_run(commit=f"c{i}", tool_versions={}, rows=scored(60, 2.0 + i),
                        lanes={"unit": {}})
    return store


def test_a_second_read_never_scans_the_function_rows_again(tmp_path):
    store = seeded(tmp_path)

    first = scans(store, lambda: store.run_totals(target=6))
    second = scans(store, lambda: store.run_totals(target=6))

    assert first, "the first read has to do the work once"
    assert second == [], f"a second read rescanned history: {second}"


def test_the_per_scope_read_is_served_from_the_same_fill(tmp_path):
    """run_totals and run_scope_totals are one scan, not two: the whole-run
    numbers are the per-scope numbers added up."""
    store = seeded(tmp_path)

    store.run_totals(target=6)
    assert scans(store, lambda: store.run_scope_totals(target=6)) == []


def test_the_cached_numbers_are_the_numbers_the_scan_computed(tmp_path):
    store = seeded(tmp_path)
    cold = (store.run_totals(target=6), store.run_scope_totals(target=6))

    warm = (store.run_totals(target=6), store.run_scope_totals(target=6))

    assert warm == cold
    fresh = SnapshotStore(tmp_path / "crap.sqlite")
    assert (fresh.run_totals(target=6), fresh.run_scope_totals(target=6)) == cold


def test_the_totals_still_count_what_the_digest_counts(tmp_path):
    """The one thing a cache may never change: over-target decided in SQL and
    over-target decided over the rows in hand still agree, warm or cold."""
    store = seeded(tmp_path, runs=1)
    run_id = store.list_runs()[0]["id"]
    rows = store.read_scored(run_id)

    for scope_targets in ({"api": 6, "ui": 6}, {"ui": 4}, None):
        for _ in range(2):  # cold, then off the rollup
            (n, over, load) = store.run_totals(target=6, scope_targets=scope_targets)[run_id]
            want = totals(rows, target=6, scope_targets=scope_targets)
            assert (n, over, round(load, 2)) == \
                (want.functions, want.over_target, want.crap_load), scope_targets


def test_the_ceiling_is_part_of_the_key_so_a_new_target_is_not_served_stale(tmp_path):
    """Two configs differing only in one scope's target. Without the ceiling in
    the key the second one silently prints the first one's over_target."""
    store = seeded(tmp_path, runs=1)
    run_id = store.list_runs()[0]["id"]
    rows = store.read_scored(run_id)

    strict = store.run_totals(target=6, scope_targets={"ui": 4})[run_id]
    loose = store.run_totals(target=6, scope_targets={"ui": 40})[run_id]

    assert strict[1] != loose[1], "the fixture must straddle the two ceilings"
    assert strict[1] == totals(rows, target=6, scope_targets={"ui": 4}).over_target
    assert loose[1] == totals(rows, target=6, scope_targets={"ui": 40}).over_target


def test_a_scope_target_equal_to_the_repo_target_shares_the_key(tmp_path):
    """`{"api": 6}` against target 6 declares nothing, so it must not split the
    cache and pay for a second full scan."""
    store = seeded(tmp_path)
    store.run_totals(target=6)

    assert scans(store, lambda: store.run_totals(target=6, scope_targets={"api": 6})) == []


def test_a_run_that_scored_nothing_is_not_rescanned_forever(tmp_path):
    """A hook anchor run holds no scored rows, so it contributes no rollup row.
    Without a marker it is never 'present' and every later invocation rescans
    the whole table looking for it."""
    store = seeded(tmp_path)
    store.write_run(commit="hook", tool_versions={}, rows=[], kind="hook")
    store.run_totals(target=6)

    assert scans(store, lambda: store.run_totals(target=6)) == []


def test_a_rowless_run_stays_out_of_the_answer(tmp_path):
    store = seeded(tmp_path)
    hook = store.write_run(commit="hook", tool_versions={}, rows=[], kind="hook")

    assert hook not in store.run_totals(target=6)
    assert hook not in store.run_scope_totals(target=6)


def test_only_the_new_run_is_scanned_when_history_is_already_rolled(tmp_path):
    store = seeded(tmp_path)
    store.run_totals(target=6)
    fresh = store.write_run(commit="c9", tool_versions={}, rows=scored(60, 9.0),
                            lanes={"unit": {}})

    (fill, *rest) = scans(store, lambda: store.run_totals(target=6))

    assert rest == [], f"one fill statement, not one per run: {rest}"
    assert f"run_id IN ({fresh})" in fill, f"the fill scanned more than the new run: {fill}"


def test_a_locked_store_still_answers_with_the_numbers_it_computed(tmp_path):
    """trend and report now WRITE. A second crapkit filling the same rollup
    holds the write lock; that may cost the cache, never the command."""
    store = seeded(tmp_path)
    want = store.run_totals(target=6)
    store._conn.execute("DELETE FROM run_rollup")
    store._conn.commit()
    store._conn.execute("PRAGMA busy_timeout = 0")  # do not wait out the other process
    blocker = sqlite3.connect(str(tmp_path / "crap.sqlite"), timeout=0)
    blocker.execute("BEGIN IMMEDIATE")  # a writer, the way the other crapkit is one
    try:
        assert store.run_totals(target=6) == want
    finally:
        blocker.rollback()
        blocker.close()


def test_the_rollup_survives_opening_a_store_written_before_it_existed(tmp_path):
    """The table is in _SCHEMA, so an old store grows it on open like every
    other table there."""
    store = seeded(tmp_path)
    want = store.run_scope_totals(target=6)
    store._conn.execute("DROP TABLE run_rollup")
    store._conn.commit()
    store._conn.close()

    reopened = SnapshotStore(tmp_path / "crap.sqlite")

    assert reopened.run_scope_totals(target=6) == want


@pytest.mark.parametrize("scope_targets", [None, {"ui": 4}])
def test_the_whole_run_totals_are_the_scope_totals_added_up(tmp_path, scope_targets):
    store = seeded(tmp_path)

    whole = store.run_totals(target=6, scope_targets=scope_targets)
    by_scope = store.run_scope_totals(target=6, scope_targets=scope_targets)

    for run_id, (n, over, load) in whole.items():
        parts = by_scope[run_id].values()
        assert n == sum(p[0] for p in parts)
        assert over == sum(p[1] for p in parts)
        assert round(load, 6) == round(sum(p[2] for p in parts), 6)
