"""Pruning and override publication cannot leave a grant without visible audit."""
import json
from pathlib import Path
import subprocess
import sys

from cli_inproc_repo import repo, template_repo  # noqa: F401
from crapkit.cli import main
from crapkit.store import SnapshotStore, prune_keep_set
from state_concurrency_worker import wait_for


def seed(root):
    (root / ".crapkit").mkdir()
    store = SnapshotStore(root / ".crapkit/crap.sqlite")
    for commit in ("first", "second"):
        store.write_run(commit=commit, tool_versions={}, rows=[])
    return store


def test_prune_winning_during_alert_refuses_the_grant_before_writing_marks(repo, capsys):
    store = seed(repo)
    store._conn.close()
    worker = subprocess.Popen([sys.executable, str(Path(__file__).with_name("state_concurrency_worker.py")),
                               "override", str(repo)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_for(repo / "alert-ready")
        assert main(["runs", "prune", "--keep", "1", "--repo", str(repo), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["pruned_runs"] == 1
        (repo / "alert-go").touch()
        stdout, stderr = worker.communicate(timeout=15)
        assert worker.returncode != 0, stdout
        assert b"no longer exists" in stderr
        assert not (repo / "crapkit-ratchet.tsv").exists()
        assert main(["overrides", "--repo", str(repo), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["overrides"] == []
    finally:
        (repo / "alert-go").touch()
        if worker.poll() is None:
            worker.communicate(timeout=15)


def test_audit_winning_after_retention_selection_keeps_its_run(repo):
    store = seed(repo)
    hook = store.write_run(commit="hook", tool_versions={}, rows=[], kind="hook")
    history = store.list_runs()
    keep = prune_keep_set(history, store.override_run_ids(), keep=1)
    writer = SnapshotStore(repo / ".crapkit/crap.sqlite")
    audit = ("src/app.ts", "f( )", 90.0, "accepted with audit")
    writer.write_overrides(hook, [audit])

    store.prune_runs(keep, observed_ids={r["id"] for r in history})

    assert hook in {run["id"] for run in store.list_runs()}
    assert store.read_overrides_all()[0][:5] == (hook, *audit)
    writer._conn.close()
    store._conn.close()
