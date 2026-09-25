"""Each scored run keeps its content record: the git blob id of every scored file.

`scored_changes` (the agent payloads) counts the scored files whose blob id
differs from the latest run's record, so the record has to ride with the run,
survive a reopen, go with its run on a prune, and answer None for every run a
crapkit older than 0.8.1 wrote, which recorded none.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

import stale_tree
from stale_tree import REL

from crapkit.store import SnapshotStore

RECORD = {"src/app.ts": "a" * 40, "src/b.ts": "b" * 40}


def _run(store: SnapshotStore, sources=None) -> int:
    return store.write_run(commit="c" * 40, tool_versions={}, rows=[], sources=sources)


def test_a_run_keeps_the_record_it_was_written_with(tmp_path):
    db = tmp_path / "crap.sqlite"
    with closing(SnapshotStore(db)) as store:
        recorded, bare = _run(store, RECORD), _run(store)

    with closing(SnapshotStore(db)) as store:
        assert store.run_sources(recorded) == RECORD
        assert store.run_sources(bare) is None, "a run that recorded none says so"
        assert store.run_sources(999) is None


def test_an_empty_record_is_a_record(tmp_path):
    with closing(SnapshotStore(tmp_path / "crap.sqlite")) as store:
        assert store.run_sources(_run(store, {})) == {}


def test_a_store_0_8_0_wrote_opens_and_its_runs_recorded_nothing(tmp_path):
    """0.8.0's store has neither table; opening it adds both and keeps its runs."""
    db = tmp_path / "crap.sqlite"
    with closing(SnapshotStore(db)) as store:
        old = _run(store)
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript("DROP TABLE run_sources; DROP TABLE lane_refusals;")

    with closing(SnapshotStore(db)) as store:
        assert store.run_sources(old) is None
        assert store.run_sources(_run(store, RECORD)) == RECORD
        assert store.lane_refusals() == {}


def test_a_pruned_run_takes_its_record_with_it(tmp_path):
    db = tmp_path / "crap.sqlite"
    with closing(SnapshotStore(db)) as store:
        doomed, kept = _run(store, RECORD), _run(store, RECORD)
        store.prune_runs({kept})
        rows = store._conn.execute("SELECT run_id FROM run_sources").fetchall()

    assert rows == [(kept,)], doomed


def test_coverage_records_the_blob_id_of_every_scored_file(tmp_path, capsys):
    from crapkit.cli import main

    root = stale_tree.build(tmp_path / "repo")
    assert main(["coverage", "--repo", str(root)]) == 0, capsys.readouterr().err

    with closing(SnapshotStore(root / ".crapkit" / "crap.sqlite")) as store:
        (run,) = store.list_runs()
        record = store.run_sources(run["id"])

    assert record == {REL: stale_tree.git(root, "rev-parse", f"HEAD:{REL}").strip()}


def test_outside_git_the_run_records_nothing(tmp_path, monkeypatch):
    from crapkit.cli.scoring import _content_record

    monkeypatch.setenv("PATH", str(tmp_path))

    assert _content_record(tmp_path, []) == {}
    assert _content_record(tmp_path, [type("Row", (), {"path": "a.py"})()]) is None
