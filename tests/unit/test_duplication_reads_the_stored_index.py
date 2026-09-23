"""`duplication` reads its owner lists from the run's stored shingle index.

The standalone report shingled every function in the repo on every call, the
same work brief did per packet. `run_index` is the one door both take to the
store: at the default threshold it hands back the run's stored index, building
and storing it on first ask. At any other threshold it stays out of the store,
because an index answers only at the min_lines it was built at and the store
keeps the one brief reads.
"""
import sqlite3

from crapkit.dup import find_duplicates, run_index
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore


def row(path, name, start, end):
    return InventoryRow(scope="src", path=path, long_name=name, start=start, end=end,
                        ccn_std=3, ccn_mod=3, ccn=3, nloc=end - start + 1, params=1, nesting=1)


BODY = "\n".join(f"    step_{i} = compute({i}) + offset" for i in range(10))
SOURCES = {"src/a.py": "def alpha():\n" + BODY + "\n",
           "src/b.py": "def beta():\n" + BODY + "\n",
           "src/c.py": "def gamma():\n" + BODY.replace("step", "other") + "\n"}
ROWS = [row("src/a.py", "alpha", 1, 11), row("src/b.py", "beta", 1, 11),
        row("src/c.py", "gamma", 1, 11)]


def refuse_sources():
    raise AssertionError("a stored index read the repo's files")


def seeded(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    return store, store.write_run(commit="c", tool_versions={}, rows=ROWS, kind="inventory")


def stored_runs(tmp_path) -> list:
    conn = sqlite3.connect(tmp_path / "crap.sqlite")
    try:
        return conn.execute("SELECT run_id, min_lines FROM twin_runs").fetchall()
    finally:
        conn.close()


def test_the_first_report_on_a_run_stores_the_index_it_counted_pairs_from(tmp_path):
    store, run_id = seeded(tmp_path)

    pairs = find_duplicates(ROWS, lambda: SOURCES,
                            indexed=run_index(store, run_id, ROWS, lambda: SOURCES))

    assert [[f["path"] for f in p["functions"]] for p in pairs] == [["src/a.py", "src/b.py"]]
    assert stored_runs(tmp_path) == [(run_id, 8)]


def test_a_later_report_reads_the_stored_index_and_no_file(tmp_path):
    store, run_id = seeded(tmp_path)
    run_index(store, run_id, ROWS, lambda: SOURCES)

    again = SnapshotStore(tmp_path / "crap.sqlite")
    pairs = find_duplicates(ROWS, refuse_sources,
                            indexed=run_index(again, run_id, ROWS, refuse_sources))

    assert pairs == find_duplicates(ROWS, lambda: SOURCES)
    # 8 windows each; only the first holds the differing `def` line
    assert pairs[0]["similarity"] == 0.875


def test_another_min_lines_leaves_the_store_alone(tmp_path):
    store, run_id = seeded(tmp_path)

    indexed = run_index(store, run_id, ROWS, refuse_sources, min_lines=4)

    assert indexed is None, "find_duplicates builds its own at that threshold"
    assert find_duplicates(ROWS, lambda: SOURCES, min_lines=4, indexed=indexed) == \
        find_duplicates(ROWS, lambda: SOURCES, min_lines=4)
    assert stored_runs(tmp_path) == []
