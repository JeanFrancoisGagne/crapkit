"""Retention preserves the evidence that decides a verify's comparison point."""
import pytest

from crapkit.store import SnapshotStore, pick_baseline, prune_keep_set


def run(store, kind, verdict=None):
    rid = store.write_run(commit=f"tree-{len(store.list_runs())}", tool_versions={},
                         rows=[], lanes={"unit": {}}, kind=kind)
    if verdict is not None:
        store.set_verdict_ok(rid, verdict, findings=int(not verdict))
    return rid


@pytest.mark.parametrize("history,expected", [
    ([("coverage", None), ("verify", False), ("coverage", None), ("coverage", None)],
     (1, 4, 2)),
    ([("verify", False), ("coverage", None), ("coverage", None)], (None, 3, 1)),
    ([("coverage", None), ("verify", False), ("coverage", None), ("verify", False),
      ("coverage", None)], (1, 5, 4)),
    ([("coverage", None), ("verify", False), ("verify", None), ("coverage", None)],
     (1, 4, 2)),
    ([("coverage", None), ("verify", False), ("verify", True), ("coverage", None)],
     (4, None, None)),
])
def test_prune_preserves_the_selected_baseline_and_refusal(tmp_path, history, expected):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    for kind, verdict in history:
        run(store, kind, verdict)
    before = pick_baseline(store.list_runs())
    assert tuple(r["id"] if r else None for r in before) == expected

    for _ in range(2):
        kept = prune_keep_set(store.list_runs(), store.override_run_ids(), keep=1)
        store.prune_runs(kept)
        assert pick_baseline(store.list_runs()) == before


def test_prune_keeps_a_failure_after_the_latest_coverage_for_future_runs(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    baseline = run(store, "coverage")
    failed = run(store, "verify", False)
    run(store, "hook")
    kept = prune_keep_set(store.list_runs(), set(), keep=1)
    store.prune_runs(kept)
    run(store, "coverage")
    picked = pick_baseline(store.list_runs())
    assert picked.run["id"] == baseline
    assert picked.blocker["id"] == failed
