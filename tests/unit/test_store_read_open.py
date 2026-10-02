"""Opening a store while another crapkit writes: a current store reads its state
without competing with the writer, and one that needs setup waits for it."""
import sqlite3
import subprocess
import sys
import textwrap
import threading

import hang_guard

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


# Another crapkit process mid-write: it holds the write lock until the test
# writes a line to its stdin.
WRITER = textwrap.dedent("""
    import sqlite3, sys
    conn = sqlite3.connect(sys.argv[1], isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO runs (commit_sha, tool_versions) VALUES ('other', '{}')")
    print("holding", flush=True)
    sys.stdin.readline()
    conn.execute("COMMIT")
""")


def _released(writer) -> None:
    writer.stdin.write("\n")
    writer.stdin.flush()


def test_a_store_that_needs_setup_waits_for_a_writer_instead_of_failing(tmp_path):
    """An older crapkit (a pinned hook, an MCP server still running) stores a
    run's lanes as text, so the next open owes setup writes. Another crapkit
    writing at that moment is waited for, as every other crapkit write waits,
    and the open does not fail at once with "database is locked". That is why
    the setup takes the write lock before it reads: SQLite answers a reader
    that then asks to write without calling the busy handler."""
    path = tmp_path / "crap.sqlite"
    SnapshotStore(path).close()
    older = sqlite3.connect(path)
    older.execute("INSERT INTO runs (commit_sha, tool_versions, lanes) VALUES ('old', '{}', '{}')")
    older.commit()
    older.close()
    writer = subprocess.Popen([sys.executable, "-S", "-c", WRITER, str(path)], text=True,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    # long after the open below asks for the lock, and well inside the 5 s a
    # crapkit connection waits for one
    release = threading.Timer(.5, _released, (writer,))
    try:
        assert hang_guard.next_line(writer) == "holding\n"
        release.start()
        store = SnapshotStore(path)
        assert hang_guard.exited(writer) == 0
        assert [run["commit"] for run in store.list_runs()] == ["old", "other"]
        store.close()
    finally:
        release.cancel()
        writer.kill()
        writer.communicate()
