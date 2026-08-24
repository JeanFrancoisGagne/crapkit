"""Identity normalization: scope, path and long_name live once, in `identities`.

Every `functions` row used to carry all three strings, rewritten in full on
every run. The flagship consumer's store reached 246 MB storing a hundred
thousand identities eight times over. These tests pin the new shape, the
migration onto it, and the fact that neither is visible through any public read.
"""
import sqlite3

import pytest
from crapkit.score import ScoredRow
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore

OLD_SHAPE = """
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commit_sha TEXT NOT NULL,
    tool_versions TEXT NOT NULL,
    lanes TEXT NOT NULL DEFAULT '{}',
    kind TEXT NOT NULL DEFAULT 'coverage',
    verdict_ok INTEGER,
    findings INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE functions (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    --IDENTITY--
    start INTEGER NOT NULL, end INTEGER NOT NULL,
    ccn_std INTEGER NOT NULL, ccn_mod INTEGER NOT NULL, ccn INTEGER NOT NULL,
    nloc INTEGER NOT NULL, params INTEGER NOT NULL, nesting INTEGER NOT NULL,
    cov REAL, flag TEXT, crap REAL, remedy TEXT,
    cognitive INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_functions_run ON functions(run_id);
CREATE INDEX idx_functions_run_path ON functions(run_id, path);
CREATE INDEX idx_functions_run_scope ON functions(run_id, scope);
"""

SCOPES = ("api", "ui")


def scored(n: int, bump: float = 0.0) -> list:
    """n scored rows over three paths and two scopes, so identities repeat."""
    return [ScoredRow(SCOPES[i % 2], f"src/m{i % 3}.py", f"f{i % 5}( a )",
                      1 + i, 9 + i, 3 + i % 7, 3 + i % 7, 3 + i % 7, 5, 1, 1,
                      0.25, "measured", 4.0 + i + bump, "add-tests")
            for i in range(n)]


def export_order(rows: list) -> list:
    """_ROW_ORDER: scope, path, start, end, long_name."""
    return sorted(rows, key=lambda r: (r.scope, r.path, r.start, r.end, r.long_name))


def old_store(db, runs: list[list], *, nullable: bool = False) -> None:
    """A database in the pre-identity shape, one run per row list.

    `nullable` is the older shape still: the first schema declared the three
    identity columns without NOT NULL, so a real store can hold a NULL scope.
    """
    decl = "scope TEXT, path TEXT, long_name TEXT" if nullable else \
        "scope TEXT NOT NULL, path TEXT NOT NULL, long_name TEXT NOT NULL"
    conn = sqlite3.connect(db)
    conn.executescript(OLD_SHAPE.replace("--IDENTITY--", decl + ","))
    for i, rows in enumerate(runs):
        cur = conn.execute(
            "INSERT INTO runs (commit_sha, tool_versions, lanes) VALUES (?, '{}', ?)",
            (f"c{i}", '{"unit": {}}'))
        conn.executemany(
            "INSERT INTO functions (run_id, scope, path, long_name, start, end, ccn_std, "
            "ccn_mod, ccn, nloc, params, nesting, cov, flag, crap, remedy, cognitive) "
            "VALUES (?" + ",?" * 16 + ")",
            [(cur.lastrowid, *r) for r in rows])
    conn.commit()
    conn.close()


def columns(db, table: str) -> list[str]:
    conn = sqlite3.connect(db)
    names = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    conn.close()
    return names


def count(db, table: str) -> int:
    conn = sqlite3.connect(db)
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


def traced(store, call) -> list[str]:
    """Every statement the store ran during `call`, parameters expanded."""
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    try:
        call()
    finally:
        store._conn.set_trace_callback(None)
    return seen


def plans(store, call) -> list[str]:
    """EXPLAIN QUERY PLAN for every SELECT the store ran during `call`."""
    reads = [s for s in traced(store, call) if s.lstrip().upper().startswith("SELECT")]
    assert reads, "the call ran no SELECT, so there is no plan to judge"
    return [row[-1] for sql in reads
            for row in store._conn.execute("EXPLAIN QUERY PLAN " + sql)]


# --- the new shape ------------------------------------------------------------

def test_the_new_shape_stores_each_identity_once(tmp_path):
    db = tmp_path / "crap.sqlite"
    store = SnapshotStore(db)
    rows = scored(60)

    store.write_run(commit="c1", tool_versions={}, rows=rows)
    store.write_run(commit="c2", tool_versions={}, rows=rows)

    distinct = {(r.scope, r.path, r.long_name) for r in rows}
    assert count(db, "identities") == len(distinct), \
        "two runs of the same functions store the identity strings once"
    assert count(db, "functions") == 120
    assert "scope" not in columns(db, "functions"), "the strings moved out of the row"
    assert set(columns(db, "functions")) & {"path", "long_name"} == set()
    assert "identity_id" in columns(db, "functions")


def test_a_second_run_reuses_the_identity_rows_of_the_first(tmp_path):
    db = tmp_path / "crap.sqlite"
    store = SnapshotStore(db)
    first = store.write_run(commit="c1", tool_versions={}, rows=scored(30))
    second = store.write_run(commit="c2", tool_versions={}, rows=scored(30, bump=1.0))

    conn = sqlite3.connect(db)
    ids = {run: {i for (i,) in conn.execute(
        "SELECT DISTINCT identity_id FROM functions WHERE run_id = ?", (run,))}
        for run in (first, second)}
    conn.close()
    assert ids[first] == ids[second], "the same function keeps the same identity id"


def test_a_repeat_run_writes_no_new_identity_rows(tmp_path):
    """The in-process cache is what keeps a 140k-row run to one pass: the second
    write must not go back to SQLite for identities it already resolved."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    rows = scored(60)
    store.write_run(commit="c1", tool_versions={}, rows=rows)

    statements = traced(store, lambda: store.write_run(commit="c2", tool_versions={}, rows=rows))

    assert not [s for s in statements if "INTO identities" in s], \
        "every identity was already cached, so nothing was inserted"


def test_a_later_run_that_adds_a_function_adds_one_identity(tmp_path):
    db = tmp_path / "crap.sqlite"
    store = SnapshotStore(db)
    store.write_run(commit="c1", tool_versions={}, rows=scored(30))
    before = count(db, "identities")

    newcomer = ScoredRow("api", "src/new.py", "fresh( )", 1, 9, 4, 4, 4, 5, 1, 1,
                         0.0, "untested", 20.0, "add-tests")
    run_id = store.write_run(commit="c2", tool_versions={}, rows=[*scored(30), newcomer])

    assert count(db, "identities") == before + 1
    assert newcomer in store.read_scored(run_id)


def test_identity_ids_never_reach_the_row_order(tmp_path):
    """Rows written in the reverse of export order get identity ids that run
    the wrong way. The read must still come back in _ROW_ORDER."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    rows = scored(24)
    run_id = store.write_run(commit="c1", tool_versions={}, rows=list(reversed(rows)))

    assert store.read_scored(run_id) == export_order(rows)
    assert store.read_rows(run_id) == export_order(
        [InventoryRow(*r[:11], r.cognitive) for r in rows])


def test_two_stores_on_one_file_agree_on_identity_ids(tmp_path):
    """Two processes, one store file. The second store's cache starts empty and
    must find the identities the first one wrote rather than duplicate them."""
    db = tmp_path / "crap.sqlite"
    rows = scored(30)
    SnapshotStore(db).write_run(commit="c1", tool_versions={}, rows=rows)
    before = count(db, "identities")

    second = SnapshotStore(db)
    run_id = second.write_run(commit="c2", tool_versions={}, rows=rows)

    assert count(db, "identities") == before, "a fresh store reuses the identities on disk"
    assert second.read_scored(run_id) == export_order(rows)


# --- the migration ------------------------------------------------------------

def test_an_old_store_migrates_on_open_and_reads_back_unchanged(tmp_path):
    db = tmp_path / "crap.sqlite"
    runs = [scored(40), scored(40, bump=2.0)]
    old_store(db, runs)

    store = SnapshotStore(db)

    assert "identity_id" in columns(db, "functions")
    assert count(db, "identities") == len({(r.scope, r.path, r.long_name) for r in runs[0]})
    for run_id, rows in ((1, runs[0]), (2, runs[1])):
        assert store.read_scored(run_id) == export_order(rows), run_id
        assert store.read_rows(run_id) == export_order(
            [InventoryRow(*r[:11], r.cognitive) for r in rows]), run_id


def test_the_migrated_store_answers_every_surface_the_same(tmp_path):
    db = tmp_path / "crap.sqlite"
    rows = scored(40)
    old_store(db, [rows])
    migrated = SnapshotStore(db)

    fresh = SnapshotStore(tmp_path / "fresh.sqlite")
    fresh.write_run(commit="c0", tool_versions={}, rows=rows, lanes={"unit": {}})

    assert migrated.read_marks(1) == fresh.read_marks(1)
    assert migrated.count_scored_below(1, 5) == fresh.count_scored_below(1, 5)
    assert migrated.read_rows(1, scopes=["ui"]) == fresh.read_rows(1, scopes=["ui"])
    assert migrated.run_totals(target=6, scope_targets={"ui": 4}) == \
        fresh.run_totals(target=6, scope_targets={"ui": 4})
    assert migrated.run_scope_totals(target=6) == fresh.run_scope_totals(target=6)
    assert migrated.read_scored_file(1, "src/m1.py") == fresh.read_scored_file(1, "src/m1.py")
    assert migrated.function_span(1, "src/m1.py", "f1( a )") == \
        fresh.function_span(1, "src/m1.py", "f1( a )")
    assert migrated.find_functions("src/m1.py", "f") == fresh.find_functions("src/m1.py", "f")
    assert [h["ccn"] for h in migrated.function_history("src/m1.py", "f1( a )")] == \
        [h["ccn"] for h in fresh.function_history("src/m1.py", "f1( a )")]


def test_the_migration_runs_once(tmp_path):
    db = tmp_path / "crap.sqlite"
    old_store(db, [scored(40)])

    SnapshotStore(db)
    identities, functions = count(db, "identities"), count(db, "functions")
    reopened = SnapshotStore(db)

    assert (count(db, "identities"), count(db, "functions")) == (identities, functions)
    assert reopened.read_scored(1) == export_order(scored(40))


def test_an_interrupted_migration_leaves_the_old_table_intact(tmp_path):
    """The rewrite builds under a temp name and swaps last, inside one
    transaction. A failure partway through must leave a database that still
    reads through the old code and still migrates on the next open."""
    db = tmp_path / "crap.sqlite"
    old_store(db, [scored(40)], nullable=True)
    conn = sqlite3.connect(db)  # a row the identity table cannot hold
    conn.execute("UPDATE functions SET scope = NULL WHERE start = 1")
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.IntegrityError):
        SnapshotStore(db)

    assert "scope" in columns(db, "functions"), "the old table survived the failure"
    assert count(db, "functions") == 40, "no row was lost"
    assert count(db, "identities") == 0, "nothing half-built was left behind"

    conn = sqlite3.connect(db)
    conn.execute("UPDATE functions SET scope = 'api' WHERE scope IS NULL")
    conn.commit()
    conn.close()
    assert len(SnapshotStore(db).read_scored(1)) == 40, "the retry migrates cleanly"


def test_an_old_store_with_no_rows_migrates(tmp_path):
    db = tmp_path / "crap.sqlite"
    old_store(db, [])

    store = SnapshotStore(db)

    assert count(db, "identities") == 0
    run_id = store.write_run(commit="c1", tool_versions={}, rows=scored(10))
    assert store.read_scored(run_id) == export_order(scored(10))


# --- the reads stay index-driven ---------------------------------------------

def seeded(tmp_path) -> tuple[SnapshotStore, int]:
    store = SnapshotStore(tmp_path / "crap.sqlite")
    return store, store.write_run(commit="c1", tool_versions={}, rows=scored(400),
                                  lanes={"unit": {}}, kind="coverage")


def test_the_run_reads_seek_the_run_index_rather_than_scanning(tmp_path):
    store, run_id = seeded(tmp_path)
    for label, call in (("read_rows", lambda: store.read_rows(run_id)),
                        ("read_scored", lambda: store.read_scored(run_id)),
                        ("read_marks", lambda: store.read_marks(run_id))):
        lines = plans(store, call)
        assert not [line for line in lines if line.startswith("SCAN functions")], \
            f"{label} scanned every row ever written: {lines}"


def test_the_path_scoped_reads_seek_the_identity_index(tmp_path):
    """A path names a handful of identities and a run names every row in it.
    Seeking the run first and filtering on path reads the whole run for one
    file, which is the cost `brief` and `explain` exist to avoid."""
    store, run_id = seeded(tmp_path)
    calls = (("read_scored_file", lambda: store.read_scored_file(run_id, "src/m1.py")),
             ("function_span", lambda: store.function_span(run_id, "src/m1.py", "f1( a )")),
             ("find_functions", lambda: store.find_functions("src/m1.py", "f")),
             ("function_history", lambda: store.function_history("src/m1.py", "f1( a )")))
    for label, call in calls:
        lines = plans(store, call)
        assert not [line for line in lines if line.startswith("SCAN")], \
            f"{label} scanned a whole table instead of seeking a path: {lines}"
        assert "identities" in lines[0] and "(path=" in lines[0], \
            f"{label} did not start from the path: {lines}"
        assert "idx_functions_identity" in lines[1], \
            f"{label} did not reach the rows through the identity: {lines}"


def test_the_scope_cut_still_reads_only_the_named_scopes(tmp_path):
    store, run_id = seeded(tmp_path)
    rows = export_order(scored(400))

    assert store.read_scored(run_id, scopes=["ui"]) == [r for r in rows if r.scope == "ui"]
    assert store.read_rows(run_id, scopes=["nope"]) == []
