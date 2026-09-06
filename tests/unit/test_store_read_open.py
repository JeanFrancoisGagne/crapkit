"""Opening a current store reads its state without competing with writers."""
import sqlite3

from crapkit.store import SnapshotStore


def test_a_reader_opens_while_another_connection_owns_the_write_transaction(tmp_path):
    path = tmp_path / "crap.sqlite"
    original = SnapshotStore(path)
    rid = original.write_run(commit="known-tree", tool_versions={}, rows=[])
    writer = sqlite3.connect(path)
    writer.execute("BEGIN IMMEDIATE")
    try:
        reader = SnapshotStore(path)
        assert reader.latest_run(commit="known-tree") == rid
        assert reader.list_runs() == original.list_runs()
    finally:
        writer.rollback()
        writer.close()


def test_reopening_a_current_store_does_not_change_its_bytes(tmp_path):
    path = tmp_path / "crap.sqlite"
    original = SnapshotStore(path)
    original.write_run(commit="known-tree", tool_versions={}, rows=[])
    before = path.read_bytes()
    reader = SnapshotStore(path)
    assert reader.list_runs() == original.list_runs()
    assert path.read_bytes() == before
