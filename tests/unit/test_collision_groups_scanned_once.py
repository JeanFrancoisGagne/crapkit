"""Each run's same-line collision groups are scanned once and dropped with the run.

The historical identity proof used to regroup every function row of every run
on each call: 4.17M rows in a 29-run store, about 8 s, behind worklist, brief,
check_gate, explain and the commit gate. A run's rows never change once
written, so its groups are scanned the first time something asks, kept in a
per-run table the way run_rollup keeps totals, and deleted in the prune that
deletes the run.
"""
import re
import sqlite3
from contextlib import closing

import pytest

from crapkit.errors import ToolError
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore, prune_keep_set


def _row(path, start, occurrence, name="(anonymous)", scope="web"):
    return ScoredRow(scope, path, name, start, start, 3, 3, 3, 1, 0, 0,
                     0.0, "untested", 12.0, "add-tests", 0, occurrence)


def _write(store, rows):
    return store.write_run(commit="c0ffee", tool_versions={"analysis_version": "10"}, rows=rows)


def _history(store):
    """Four runs: legacy twins in a.ts, positioned twins in b.ts, scope copies, nothing."""
    legacy = _write(store, [_row("a.ts", 1, 0), _row("a.ts", 1, 0), _row("c.ts", 4, 0, "g")])
    positioned = _write(store, [_row("b.ts", 2, 1), _row("b.ts", 2, 2), _row("c.ts", 4, 1, "g")])
    copies = _write(store, [_row("c.ts", 4, 0, "g"), _row("c.ts", 4, 0, "g", scope="copy")])
    plain = _write(store, [_row("a.ts", 1, 1), _row("a.ts", 5, 1), _row("c.ts", 4, 1, "g")])
    return legacy, positioned, copies, plain


def _scans(statements):
    """The runs each collision scan in `statements` grouped: None for a scan of every run."""
    found = []
    for statement in statements:
        if "SUM(f.occurrence = 0) > 1" in statement:
            run = re.search(r"f\.run_id = (\d+)", statement)
            found.append(int(run.group(1)) if run else None)
    return found


def test_the_groups_are_the_runs_twins_and_a_second_read_scans_no_run(tmp_path):
    store = SnapshotStore(tmp_path / "s.sqlite")
    with closing(store._conn):
        legacy, positioned, _copies, _plain = _history(store)
        first = store.historical_collision_groups()
        statements = []
        store._conn.set_trace_callback(statements.append)
        assert store.historical_collision_groups() == first
        assert store.identity_witness_run_ids() == {legacy, positioned}
    assert first == {("a.ts", "(anonymous)"), ("b.ts", "(anonymous)")}
    assert _scans(statements) == []


def test_a_run_written_after_the_scan_is_the_only_run_scanned_next(tmp_path):
    store = SnapshotStore(tmp_path / "s.sqlite")
    with closing(store._conn):
        _history(store)
        store.historical_collision_groups()
        later = _write(store, [_row("d.ts", 3, 0), _row("d.ts", 3, 0)])
        statements = []
        store._conn.set_trace_callback(statements.append)
        assert ("d.ts", "(anonymous)") in store.historical_collision_groups()
    assert _scans(statements) == [later]


def test_a_prune_takes_the_collision_groups_with_the_run(tmp_path):
    store = SnapshotStore(tmp_path / "s.sqlite")
    with closing(store._conn):
        legacy, positioned, _copies, plain = _history(store)
        store.historical_collision_groups()
        store.prune_runs({plain})
        left = {run for (run,) in store._conn.execute("SELECT DISTINCT run_id FROM run_collisions")}
        assert left == {plain}
        assert store.historical_collision_groups() == set()


def test_rows_an_older_crapkit_left_behind_answer_for_no_run(tmp_path):
    """An older crapkit prunes runs without knowing this table exists."""
    store = SnapshotStore(tmp_path / "s.sqlite")
    with closing(store._conn):
        legacy, _positioned, _copies, _plain = _history(store)
        store.historical_collision_groups()
        store._conn.execute("DELETE FROM functions WHERE run_id = ?", (legacy,))
        store._conn.execute("DELETE FROM runs WHERE id = ?", (legacy,))
        store._conn.commit()
        assert store.historical_collision_groups() == {("b.ts", "(anonymous)")}


def test_a_locked_store_still_answers_with_the_groups_it_scanned(tmp_path):
    path = tmp_path / "s.sqlite"
    store = SnapshotStore(path)
    with closing(store._conn):
        _history(store)
        store._conn.execute("PRAGMA busy_timeout = 0")
        blocker = sqlite3.connect(str(path), timeout=0)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            assert store.historical_collision_groups() == {("a.ts", "(anonymous)"),
                                                          ("b.ts", "(anonymous)")}
        finally:
            blocker.rollback()
            blocker.close()
        assert store._conn.execute("SELECT COUNT(*) FROM run_collisions").fetchone() == (0,)


def test_a_store_written_before_the_table_grows_it_on_open(tmp_path):
    path = tmp_path / "s.sqlite"
    store = SnapshotStore(path)
    with closing(store._conn):
        _history(store)
        store._conn.execute("DROP TABLE run_collisions")
        store._conn.commit()
    reopened = SnapshotStore(path)
    with closing(reopened._conn):
        assert reopened.historical_collision_groups() == {("a.ts", "(anonymous)"),
                                                         ("b.ts", "(anonymous)")}


def test_a_store_that_regains_occurrence_is_scanned_again(tmp_path):
    """Rows reopened without positions are legacy twins, whatever the scan said before."""
    path = tmp_path / "s.sqlite"
    store = SnapshotStore(path)
    with closing(store._conn):
        run = _write(store, [_row("b.ts", 2, 1), _row("b.ts", 2, 2)])
        store.read_marks(run)
        store._conn.execute("ALTER TABLE functions DROP COLUMN occurrence")
        store._conn.commit()
    reopened = SnapshotStore(path)
    with closing(reopened._conn), pytest.raises(ToolError, match=f"b.ts: \\(anonymous\\) in run {run}"):
        reopened.read_marks(run)


def test_one_runs_identity_check_scans_that_run_alone(tmp_path):
    store = SnapshotStore(tmp_path / "s.sqlite")
    with closing(store._conn):
        legacy, _positioned, _copies, plain = _history(store)
        statements = []
        store._conn.set_trace_callback(statements.append)
        store.read_marks(plain)
        store.twin_key_names(plain)
        with pytest.raises(ToolError, match=f"a.ts: \\(anonymous\\) in run {legacy}"):
            store.read_marks(legacy)
    assert _scans(statements) == [plain, legacy]


def test_a_few_files_are_proved_off_the_path_index_without_scanning_a_run(tmp_path):
    store = SnapshotStore(tmp_path / "s.sqlite")
    with closing(store._conn):
        _history(store)
        assert store.historical_collision_groups({"a.ts", "c.ts"}) == {("a.ts", "(anonymous)")}
        assert store._conn.execute("SELECT COUNT(*) FROM run_collisions").fetchone() == (0,)


def test_many_files_read_the_table_and_keep_only_their_own_groups(tmp_path):
    store = SnapshotStore(tmp_path / "s.sqlite")
    with closing(store._conn):
        _history(store)
        many = {f"x{n}.ts" for n in range(500)} | {"b.ts"}
        assert store.historical_collision_groups(many) == {("b.ts", "(anonymous)")}
        assert store._conn.execute(
            "SELECT COUNT(*) FROM run_collisions WHERE identity_id = 0").fetchone() == (4,)


def test_the_witness_runs_survive_a_prune_that_reads_the_table(tmp_path):
    store = SnapshotStore(tmp_path / "s.sqlite")
    with closing(store._conn):
        legacy, positioned, _copies, plain = _history(store)
        runs = store.list_runs()
        keep = prune_keep_set(runs, store.override_run_ids(), keep=1,
                              identity_run_ids=store.identity_witness_run_ids())
        assert keep >= {legacy, positioned, plain}
