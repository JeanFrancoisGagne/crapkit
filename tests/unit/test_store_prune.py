"""Retention seam: a prune frees disk without making another command lie.

The danger is not losing history, it is the surfaces that read a half-deleted
run as a real measurement. A digest whose older half lost its rows reports the
whole codebase as newly over target; a trend row that outlives its function
rows reports a week of zero debt that never happened. Every test here pins
digest and trend output across a prune.
"""
import sqlite3

import pytest
from crapkit.digest import build_digest, latest_comparable_pair, totals
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore, prune_keep_set, trusted_runs


def scored(n: int, crap: float = 40.0) -> list:
    return [ScoredRow("src", f"src/m{i}.py", f"f{i}( )", 1 + i, 9 + i, 7, 7, 7, 5, 1, 1,
                      0.25, "measured", crap + i, "add-tests")
            for i in range(n)]


def store_with_history(dirpath, rows_per_run: int = 3) -> SnapshotStore:
    """Three trusted runs where only the first and third share a lane set, so
    the digest pair is NOT the two newest and keep-N-newest alone would drop it."""
    store = SnapshotStore(dirpath / "crap.sqlite")
    store.write_run(commit="c1", tool_versions={}, rows=scored(rows_per_run, 40.0),
                    lanes={"unit": {}}, kind="coverage")
    store.write_run(commit="c2", tool_versions={}, rows=scored(rows_per_run, 41.0),
                    lanes={"unit": {}, "py": {}}, kind="coverage")
    store.write_run(commit="c3", tool_versions={}, rows=scored(rows_per_run, 55.0),
                    lanes={"unit": {}}, kind="coverage")
    return store


def digest_lines(store: SnapshotStore) -> list[str]:
    prev, cur = latest_comparable_pair(trusted_runs(store))
    return build_digest(store.read_scored(prev["id"]), store.read_scored(cur["id"]),
                        target=6).lines


def trend_totals(store: SnapshotStore) -> dict:
    agg = store.run_totals(target=6)
    return {r["id"]: agg.get(r["id"], (0, 0, 0.0)) for r in trusted_runs(store)}


def prune(store: SnapshotStore, keep: int) -> int:
    keep_ids = prune_keep_set(store.list_runs(), store.override_run_ids(), keep=keep)
    deleted = store.prune_runs(keep_ids)
    store.vacuum()
    return deleted


def test_prune_keeps_the_digest_pair_so_the_digest_is_unchanged(tmp_path):
    store = store_with_history(tmp_path)
    before = digest_lines(store)
    assert before, "the fixture must produce a loud digest, or this test proves nothing"

    assert prune(store, keep=1) == 1, "only the middle run has no claim on the keep-set"

    assert [r["commit"] for r in store.list_runs()] == ["c1", "c3"]
    assert digest_lines(store) == before


def test_a_run_row_that_outlives_its_rows_is_the_false_alarm_prune_avoids(tmp_path):
    """Why prune deletes whole runs. Deleting the function rows alone leaves a
    run that reads as a real measurement of nothing, and the digest then reports
    every over-target function in the repo as new."""
    store = store_with_history(tmp_path)
    quiet = digest_lines(store)
    older = store.list_runs()[0]["id"]

    conn = sqlite3.connect(tmp_path / "crap.sqlite")  # the naive prune, on purpose
    conn.execute("DELETE FROM functions WHERE run_id = ?", (older,))
    conn.commit()
    conn.close()

    loud = digest_lines(SnapshotStore(tmp_path / "crap.sqlite"))
    assert "functions 0 -> 3" in loud[0], loud
    assert sum(1 for line in loud if line.startswith("new over ceiling")) == 3
    assert not any(line.startswith("new over ceiling") for line in quiet), \
        "before the damage those three functions were known, not new"


def test_a_trend_row_never_survives_its_own_function_rows(tmp_path):
    store = store_with_history(tmp_path)
    before = trend_totals(store)

    prune(store, keep=1)

    after = trend_totals(store)
    assert set(after) < set(before), "a prune drops runs from the trend, it does not blank them"
    for run_id, agg in after.items():
        assert agg == before[run_id], f"run {run_id} reports different totals after a prune"
        assert agg[0] > 0, f"run {run_id} kept a trend row with no functions behind it"


def test_prune_keeps_every_run_an_override_record_names(tmp_path):
    store = store_with_history(tmp_path)
    hook = store.write_run(commit="c4", tool_versions={}, rows=[],
                           lanes={"_hook_override": {}}, kind="hook")
    store.write_overrides(hook, [("src/a.py", "f( )", 90.0, "prod down")])

    prune(store, keep=1)

    assert hook in {r["id"] for r in store.list_runs()}
    assert len(store.read_overrides_all()) == 1, "the audit trail joins through the run row"


def test_prune_keeps_passing_verify_baselines_and_the_newest_non_hook_run(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    passing = store.write_run(commit="v1", tool_versions={}, rows=scored(3),
                              lanes={"unit": {}}, kind="verify")
    store.set_verdict_ok(passing, True)
    for i in range(3):
        store.write_run(commit=f"cov{i}", tool_versions={}, rows=scored(3),
                        lanes={"unit": {}}, kind="coverage")
    failed = store.write_run(commit="v2", tool_versions={}, rows=scored(3),
                             lanes={"unit": {}}, kind="verify")
    store.set_verdict_ok(failed, False)

    prune(store, keep=1)

    kept = {r["id"] for r in store.list_runs()}
    assert passing in kept, "`verify --baseline ID` can still name a passing verify"
    assert failed in kept, "the newest non-hook run is what worklist and duplication read"


def test_prune_frees_disk_not_just_pages(tmp_path):
    store = store_with_history(tmp_path, rows_per_run=4000)
    before = store.size_bytes()

    prune(store, keep=1)

    assert store.size_bytes() < before, f"DELETE without VACUUM frees no disk (was {before})"


def test_keep_below_one_is_refused_before_anything_is_deleted(tmp_path):
    store = store_with_history(tmp_path)
    with pytest.raises(ValueError, match="keep must be >= 1"):
        prune_keep_set(store.list_runs(), store.override_run_ids(), keep=0)
    assert len(store.list_runs()) == 3


def test_prune_leaves_the_rows_of_a_kept_run_byte_identical(tmp_path):
    store = store_with_history(tmp_path)
    newest = store.list_runs()[-1]["id"]
    before = store.read_scored(newest)

    prune(store, keep=1)

    assert store.read_scored(newest) == before
    assert totals(store.read_scored(newest), target=6) == totals(before, target=6)


def test_a_prune_that_changes_nothing_deletes_nothing(tmp_path):
    store = store_with_history(tmp_path)
    assert prune(store, keep=5) == 0
    assert prune(store, keep=5) == 0, "prune converges: running it twice is one prune"
    assert len(store.list_runs()) == 3
