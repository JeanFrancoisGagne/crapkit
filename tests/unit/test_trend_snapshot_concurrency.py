"""A concurrent run cannot split trend's totals from its scope totals."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from cli_inproc_repo import repo, template_repo  # noqa: F401
from crapkit.cli import main
from crapkit.store import SnapshotStore
from state_concurrency_worker import row, wait_for


def test_public_trend_reads_one_snapshot_while_another_process_adds_a_run(repo, monkeypatch, capsys):
    (repo / ".crapkit").mkdir()
    db = repo / ".crapkit/crap.sqlite"
    store = SnapshotStore(db)
    store._conn.execute("PRAGMA journal_mode = WAL")
    store.write_run(commit="old", tool_versions={}, rows=[row()])
    store._conn.close()
    worker = subprocess.Popen([sys.executable, str(Path(__file__).with_name("state_concurrency_worker.py")),
                               "trend", str(repo)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    wait_for(repo / "writer-ready")
    original = sqlite3.connect

    def interleave(sql):
        if "SELECT run_id, scope, functions" in sql and not (repo / "writer-go").exists():
            (repo / "writer-go").touch()
            wait_for(repo / "writer-done")

    def connected(*args, **kwargs):
        connection = original(*args, **kwargs)
        connection.set_trace_callback(interleave)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connected)
    try:
        assert main(["trend", "--repo", str(repo), "--json"]) == 0
        output = json.loads(capsys.readouterr().out)
        assert (repo / "writer-done").exists(), "the writer must overlap the report read"
        assert [(r["commit"], r["functions"], r["crap_load"]) for r in output["runs"]] in (
            [("old", 1, 6.0)], [("old", 1, 6.0), ("new", 1, 6.0)])
        assert all(r["functions"] == sum(s["functions"] for s in r["by_scope"].values())
                   for r in output["runs"])
    finally:
        (repo / "writer-go").touch()
        stdout, stderr = worker.communicate(timeout=15)
        assert worker.returncode == 0, (stdout, stderr)


def test_prune_after_the_read_does_not_publish_a_rollup_for_a_deleted_run(tmp_path):
    db = tmp_path / "store.sqlite"
    store, writer = SnapshotStore(db), SnapshotStore(db)
    store._conn.execute("PRAGMA journal_mode = WAL")
    run_id = store.write_run(commit="old", tool_versions={}, rows=[row()])
    pruned = []

    def interleave(sql):
        if sql.startswith("INSERT OR REPLACE INTO run_rollup") and not pruned:
            pruned.append(writer.prune_runs(set()))

    store._conn.set_trace_callback(interleave)
    history = store.history_totals(target=6)

    assert pruned == [1]
    assert [(run["id"], totals) for run, totals, _ in history] == [(run_id, (1, 0, 6.0))]
    assert store.run_totals(target=6) == {}
    store._conn.close()
    writer._conn.close()
