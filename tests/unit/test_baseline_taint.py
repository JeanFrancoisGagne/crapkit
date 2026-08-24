"""The taint rule: a failed verify's findings cannot be laundered by a later run.

`coverage` writes a trusted run wherever HEAD is, and anyone may run it. Without
this rule, one `coverage` between a failed `verify` and its fix moves the
comparison point past the findings: the offending function stops being touched
relative to the new baseline and no later verify ever looks at it again.
"""
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore, pick_baseline


def rows():
    return [InventoryRow("src", "src/a.ts", "f( x )", 1, 9, 7, 5, 5, 8, 1, 2)]


def coverage_run(store: SnapshotStore, commit: str = "a") -> int:
    return store.write_run(commit=commit, tool_versions={}, rows=rows(),
                           kind="coverage", lanes={"unit": {}})


def verify_run(store: SnapshotStore, ok: bool, *, findings: int = 0,
               commit: str = "a") -> int:
    run_id = store.write_run(commit=commit, tool_versions={}, rows=rows(),
                             kind="verify", lanes={"unit": {}})
    store.set_verdict_ok(run_id, ok, findings=findings)
    return run_id


def test_a_repo_that_never_ran_verify_picks_the_newest_trusted_run(tmp_path):
    """Adoption is untouched: with no verdict in the history there is nothing to
    launder, so the rule cannot slow a repo that only runs `coverage`."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    coverage_run(store)
    newest = coverage_run(store, commit="b")

    pick = pick_baseline(store.list_runs())

    assert pick.run["id"] == newest
    assert pick.blocker is None and pick.skipped is None


def test_a_coverage_run_taken_after_a_failed_verify_is_not_the_baseline(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    before = coverage_run(store)
    verify_run(store, False, findings=1)
    coverage_run(store, commit="b")

    pick = pick_baseline(store.list_runs())

    assert pick.run["id"] == before, "the baseline stays behind the failure"


def test_the_pick_names_the_failed_verify_and_the_run_it_refused(tmp_path):
    """The refusal message is built from these three: the failed run, its finding
    count, and the run id `--baseline` would have to name to override."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    coverage_run(store)
    failed = verify_run(store, False, findings=3)
    laundered = coverage_run(store, commit="b")

    pick = pick_baseline(store.list_runs())

    assert pick.blocker["id"] == failed
    assert pick.blocker["findings"] == 3
    assert pick.skipped["id"] == laundered


def test_a_passing_verify_clears_the_taint(tmp_path):
    """Passing is the proof the findings were answered. Nothing else clears it."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    coverage_run(store)
    verify_run(store, False, findings=1)
    passed = verify_run(store, True, commit="b")
    after = coverage_run(store, commit="c")

    assert pick_baseline(store.list_runs()).run["id"] == after
    assert pick_baseline(store.list_runs()[:3]).run["id"] == passed


def test_the_newest_of_several_laundering_runs_is_the_one_named(tmp_path):
    """`--baseline ID` has to name a run that accepts every laundering run at
    once, which is the newest of them."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    before = coverage_run(store)
    verify_run(store, False, findings=2)
    coverage_run(store, commit="b")
    newest = coverage_run(store, commit="c")

    pick = pick_baseline(store.list_runs())

    assert pick.run["id"] == before
    assert pick.skipped["id"] == newest


def test_a_verify_that_never_recorded_a_verdict_neither_taints_nor_clears(tmp_path):
    """A crashed run leaves verdict_ok NULL. It found nothing, so it may not
    block a baseline; it proved nothing, so it may not clear one either."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    store.write_run(commit="a", tool_versions={}, rows=rows(), kind="verify",
                    lanes={"unit": {}})
    after_crash = coverage_run(store, commit="b")
    assert pick_baseline(store.list_runs()).run["id"] == after_crash

    verify_run(store, False, findings=1)
    store.write_run(commit="c", tool_versions={}, rows=rows(), kind="verify",
                    lanes={"unit": {}})
    coverage_run(store, commit="d")

    assert pick_baseline(store.list_runs()).run["id"] == after_crash


def test_a_taint_with_nothing_older_to_fall_back_to_reports_no_run(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    verify_run(store, False, findings=1)
    coverage_run(store, commit="b")

    pick = pick_baseline(store.list_runs())

    assert pick.run is None
    assert pick.blocker["findings"] == 1
