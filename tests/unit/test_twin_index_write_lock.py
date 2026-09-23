"""Storing a run's shingle index does not lock the rest of crapkit out.

The first brief on a run writes about 38 MB of index into the store on a large
consumer repo. Two things kept every other crapkit process waiting on that write:

- At SQLite's default 2 MB page cache the insert spills to the file before it
  commits, and a spill takes the exclusive lock. A reader polling during a
  9.4 s write was locked out for 8.8 s of it, longer than the 5 s any crapkit
  command waits before failing with "database is locked".
- Sorting and encoding 1.55 M postings under the write lock kept every other
  writer waiting for all of it, where copying them in from a temp table built
  beforehand holds the lock for about a third of the time.

Both are mechanism tests: a second connection that never waits tries to read,
or to write, at the moment that matters.
"""
import sqlite3

import pytest

from crapkit.dup import function_index
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore


def big_repo(functions: int, lines: int):
    """Enough distinct shingles that the index outgrows SQLite's default cache."""
    rows, sources = [], {}
    for f in range(functions):
        path = f"src/m{f}.py"
        body = "\n".join(f"    v{f}_{i} = step({f}, {i})" for i in range(lines))
        sources[path] = f"def f{f}():\n{body}\n"
        rows.append(InventoryRow("src", path, f"f{f}", 1, lines + 1, 3, 3, 3, lines + 1, 0, 0))
    return rows, sources


@pytest.fixture(scope="module")
def built():
    rows, sources = big_repo(120, 1000)
    return rows, function_index(rows, sources)


def seeded(tmp_path, rows):
    db = tmp_path / "crap.sqlite"
    store = SnapshotStore(db)
    return db, store, store.write_run(commit="c", tool_versions={}, rows=rows, kind="inventory")


def attempt(db, statement: str) -> bool:
    """Whether a connection that never waits gets through `statement` right now."""
    other = sqlite3.connect(db, timeout=0)
    try:
        other.execute(statement).fetchall()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        other.rollback()
        other.close()


def test_a_reader_is_never_locked_out_while_the_index_is_written(tmp_path, built):
    rows, index = built
    db, store, run_id = seeded(tmp_path, rows)
    reads: list[bool] = []

    def mid_write() -> int:
        if store._conn.in_transaction:
            reads.append(attempt(db, "SELECT count(*) FROM runs"))
        return 0

    store._conn.set_progress_handler(mid_write, 20_000)
    try:
        store.twin_index(run_id, lambda: index)
    finally:
        store._conn.set_progress_handler(None, 0)

    assert len(reads) > 10, "the write ran long enough to be probed"
    assert all(reads), f"{reads.count(False)} of {len(reads)} reads were locked out"
    assert store._conn.execute("PRAGMA cache_size").fetchone()[0] == -2000, \
        "the wider cache is the write's alone"


def test_the_postings_are_staged_before_the_write_lock_is_taken(tmp_path, built):
    rows, index = built
    db, store, run_id = seeded(tmp_path, rows)
    writes: dict[str, bool] = {}

    def at(statement: str) -> None:
        for marker in ("INSERT INTO temp.twin_stage", "INSERT INTO twin_postings"):
            if statement.startswith(marker) and marker not in writes:
                writes[marker] = attempt(db, "BEGIN IMMEDIATE")

    store._conn.set_trace_callback(at)
    try:
        store.twin_index(run_id, lambda: index)
    finally:
        store._conn.set_trace_callback(None)

    assert writes == {"INSERT INTO temp.twin_stage": True, "INSERT INTO twin_postings": False}, \
        "another writer gets in while the postings are staged, and waits only for the copy"
    assert attempt(db, "SELECT name FROM temp.sqlite_master"), "the stage is gone"
    assert SnapshotStore(db).twin_index(run_id, lambda: pytest.fail("not stored")).min_lines == 8
