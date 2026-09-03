"""The stored-bytes stack: two index changes, coded flags, a deflated lane blob.

Measured on a copy of the flagship consumer's store, the four together take it
from 131,436,544 to 92,303,360 bytes and make every timed read faster. None of
it may be visible above this module: the codes are internal, the strings still
come back out of every read, and a store written by the previous shape migrates
on open inside one transaction.
"""
import json
import sqlite3
import zlib
from unittest import mock

import pytest
from crapkit.score import ScoredRow
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore

# The shape this stack replaces: identities already normalized, flag and remedy
# still TEXT on every row, lanes still a JSON string, both redundant indexes
# still on disk. Written out rather than imported so the fixture cannot drift
# when the live DDL moves.
PREV_SHAPE = """
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
CREATE TABLE identities (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL, path TEXT NOT NULL, long_name TEXT NOT NULL,
    UNIQUE(scope, path, long_name)
);
CREATE TABLE functions (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    identity_id INTEGER NOT NULL REFERENCES identities(id),
    start INTEGER NOT NULL, end INTEGER NOT NULL,
    ccn_std INTEGER NOT NULL, ccn_mod INTEGER NOT NULL, ccn INTEGER NOT NULL,
    nloc INTEGER NOT NULL, params INTEGER NOT NULL, nesting INTEGER NOT NULL,
    cov REAL, flag TEXT, crap REAL, remedy TEXT,
    cognitive INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE overrides (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    path TEXT NOT NULL, long_name TEXT NOT NULL,
    crap REAL NOT NULL, reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL, long_name TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    closed_at TEXT
);
CREATE INDEX idx_functions_run ON functions(run_id);
CREATE INDEX idx_functions_run_path ON functions(run_id, identity_id);
CREATE INDEX idx_functions_identity ON functions(identity_id, run_id);
CREATE INDEX idx_identities_path ON identities(path, long_name);
CREATE INDEX idx_attempts_open ON attempts(closed_at);
"""

SCOPES = ("api", "ui")
FLAGS = ("measured", "untested", "no-lane", "cc-only")
REMEDIES = ("ok", "add-tests", "decompose")
LANES = {"unit": {"exit_code": 0, "scopes": ["api", "ui"]}}


def scored(n: int, bump: float = 0.0) -> list:
    """n scored rows over three paths, two scopes and the whole flag domain."""
    return [ScoredRow(SCOPES[i % 2], f"src/m{i % 3}.py", f"f{i % 5}( a )",
                      1 + i, 9 + i, 3 + i % 7, 3 + i % 7, 3 + i % 7, 5, 1, 1,
                      0.25, FLAGS[i % 4], 4.0 + i + bump, REMEDIES[i % 3], i % 3)
            for i in range(n)]


def inventory_of(rows: list) -> list:
    return [InventoryRow(*r[:11], r.cognitive) for r in rows]


def export_order(rows: list) -> list:
    return sorted(rows, key=lambda r: (r.scope, r.path, r.start, r.end, r.long_name))


def prev_store(db, runs: list[list]) -> None:
    """A database in the shape this stack replaces, one run per row list."""
    conn = sqlite3.connect(db)
    conn.executescript(PREV_SHAPE)
    for i, rows in enumerate(runs):
        cur = conn.execute(
            "INSERT INTO runs (commit_sha, tool_versions, lanes) VALUES (?, '{}', ?)",
            (f"c{i}", json.dumps(LANES, sort_keys=True)))
        _insert_prev_rows(conn, cur.lastrowid, rows)
    conn.commit()
    conn.close()


def _insert_prev_rows(conn, run_id: int, rows: list) -> None:
    ids = {}
    for r in rows:
        key = (r.scope, r.path, r.long_name)
        if key not in ids:
            conn.execute(
                "INSERT OR IGNORE INTO identities (scope, path, long_name) VALUES (?,?,?)", key)
            ids[key] = conn.execute(
                "SELECT id FROM identities WHERE scope=? AND path=? AND long_name=?",
                key).fetchone()[0]
        conn.execute(
            "INSERT INTO functions (run_id, identity_id, start, end, ccn_std, ccn_mod, ccn, "
            "nloc, params, nesting, cov, flag, crap, remedy, cognitive) "
            "VALUES (?" + ",?" * 14 + ")", (run_id, ids[key], *r[3:]))


def conn_of(db):
    return sqlite3.connect(db)


def index_names(db, table: str) -> set:
    conn = conn_of(db)
    names = {row[1] for row in conn.execute(f"PRAGMA index_list({table})")}
    conn.close()
    return names


def unique_columns(db, table: str) -> tuple:
    """The columns of the table's UNIQUE constraint, in key order."""
    conn = conn_of(db)
    unique = [row[1] for row in conn.execute(f"PRAGMA index_list({table})") if row[2]]
    cols = tuple(row[2] for row in conn.execute(f"PRAGMA index_info({unique[0]})"))
    conn.close()
    return cols


def storage_classes(db, table: str, column: str) -> set:
    conn = conn_of(db)
    classes = {row[0] for row in conn.execute(
        f"SELECT DISTINCT typeof({column}) FROM {table}")}
    conn.close()
    return classes


def seeded(db, rows: list, **kw) -> tuple[SnapshotStore, int]:
    store = SnapshotStore(db)
    return store, store.write_run(commit="c1", tool_versions={}, rows=rows,
                                  lanes=LANES, **kw)


# --- (a) the identities UNIQUE carries the path first --------------------------

def test_the_identities_unique_leads_with_the_path(tmp_path):
    db = tmp_path / "crap.sqlite"
    seeded(db, scored(30))

    assert unique_columns(db, "identities") == ("path", "long_name", "scope"), \
        "the path-scoped reads seek this index; leading with scope makes it useless to them"
    assert "idx_identities_path" not in index_names(db, "identities"), \
        "the reordered UNIQUE covers (path, long_name); a second index on it is dead weight"


# --- (b) one run-keyed index on functions, not two ----------------------------

def test_only_one_run_keyed_index_survives_on_functions(tmp_path):
    db = tmp_path / "crap.sqlite"
    seeded(db, scored(30))

    names = index_names(db, "functions")
    assert "idx_functions_run" in names
    assert "idx_functions_identity" in names
    assert "idx_functions_run_path" not in names, \
        "idx_functions_run answers the same seek and costs 10.8 MB less on the flagship store"


# --- (c) flag and remedy as codes, strings on every surface -------------------

def test_flag_and_remedy_are_stored_as_integer_codes(tmp_path):
    db = tmp_path / "crap.sqlite"
    seeded(db, scored(40))

    assert storage_classes(db, "functions", "flag") == {"integer"}
    assert storage_classes(db, "functions", "remedy") == {"integer"}


def test_the_lookup_tables_name_every_code(tmp_path):
    db = tmp_path / "crap.sqlite"
    seeded(db, scored(40))

    conn = conn_of(db)
    flags = dict(conn.execute("SELECT id, name FROM flags"))
    remedies = dict(conn.execute("SELECT id, name FROM remedies"))
    conn.close()
    assert set(flags.values()) >= set(FLAGS)
    assert set(remedies.values()) >= set(REMEDIES)


def test_the_reads_still_hand_back_the_strings(tmp_path):
    db = tmp_path / "crap.sqlite"
    rows = scored(40)
    store, run_id = seeded(db, rows)

    assert store.read_scored(run_id) == export_order(rows)
    assert store.read_rows(run_id) == export_order(inventory_of(rows))
    marks = store.read_marks(run_id)
    assert {verdict[:2] for verdict in marks.values()} <= {(f, r) for f in FLAGS for r in REMEDIES}
    assert {h["flag"] for h in store.function_history("src/m1.py", "f1( a )")} <= set(FLAGS)


def test_a_flag_outside_the_seeded_domain_round_trips(tmp_path):
    """The codes are crapkit's own vocabulary, but the store may not silently
    drop a name it has not met: a row written by a newer scorer reads back whole."""
    db = tmp_path / "crap.sqlite"
    odd = ScoredRow("api", "src/m9.py", "g( )", 1, 4, 2, 2, 2, 3, 0, 0,
                    0.5, "brand-new-flag", 3.5, "brand-new-remedy", 0)
    store, run_id = seeded(db, [odd])

    assert store.read_scored(run_id) == [odd]
    assert store.read_marks(run_id) == {("src/m9.py", "g( )"): ("brand-new-flag",
                                                               "brand-new-remedy", 3.5, 0.5)}


def test_an_inventory_run_still_stores_no_verdict(tmp_path):
    db = tmp_path / "crap.sqlite"
    rows = inventory_of(scored(20))
    store, run_id = seeded(db, rows, kind="inventory")

    assert store.read_rows(run_id) == export_order(rows)
    assert store.read_scored(run_id) == [], "an inventory row has no crap and no flag"
    assert storage_classes(db, "functions", "flag") == {"null"}


# --- (d) lanes deflated ------------------------------------------------------

def test_lanes_are_stored_deflated(tmp_path):
    db = tmp_path / "crap.sqlite"
    store, _ = seeded(db, scored(10))

    assert storage_classes(db, "runs", "lanes") == {"blob"}
    conn = conn_of(db)
    (blob,) = conn.execute("SELECT lanes FROM runs").fetchone()
    conn.close()
    assert json.loads(zlib.decompress(blob).decode("utf-8")) == LANES
    assert store.list_runs()[0]["lanes"] == LANES


# --- the migration -----------------------------------------------------------

def test_the_previous_shape_migrates_on_open_and_reads_back_unchanged(tmp_path):
    db = tmp_path / "crap.sqlite"
    runs = [scored(40), scored(40, bump=2.0)]
    prev_store(db, runs)

    store = SnapshotStore(db)

    assert unique_columns(db, "identities") == ("path", "long_name", "scope")
    assert "idx_functions_run_path" not in index_names(db, "functions")
    assert storage_classes(db, "functions", "flag") == {"integer"}
    assert storage_classes(db, "runs", "lanes") == {"blob"}
    for run_id, rows in ((1, runs[0]), (2, runs[1])):
        assert store.read_scored(run_id) == export_order(rows), run_id
        assert store.read_rows(run_id) == export_order(inventory_of(rows)), run_id
    assert [r["lanes"] for r in store.list_runs()] == [LANES, LANES]


def test_the_migrated_store_answers_every_surface_the_same(tmp_path):
    db = tmp_path / "crap.sqlite"
    rows = scored(40)
    prev_store(db, [rows])
    migrated = SnapshotStore(db)

    fresh, _ = seeded(tmp_path / "fresh.sqlite", rows)

    assert migrated.read_marks(1) == fresh.read_marks(1)
    assert migrated.read_scored_file(1, "src/m1.py") == fresh.read_scored_file(1, "src/m1.py")
    assert migrated.run_totals(target=6) == fresh.run_totals(target=6)
    assert migrated.run_scope_totals(target=6) == fresh.run_scope_totals(target=6)
    # sorted: one (path, long_name) lives in both scopes, so the two stores can
    # order the pair by whichever identity id they minted first
    assert _history_marks(migrated) == _history_marks(fresh)


def _history_marks(store) -> list:
    return sorted((h["ccn"], h["cov"], h["flag"], h["crap"])
                  for h in store.function_history("src/m1.py", "f1( a )"))


def test_the_migration_runs_once(tmp_path):
    db = tmp_path / "crap.sqlite"
    rows = scored(40)
    prev_store(db, [rows])

    SnapshotStore(db)
    before = db.stat().st_size
    reopened = SnapshotStore(db)

    assert reopened.read_scored(1) == export_order(rows)
    assert db.stat().st_size == before, "a second open rewrote a table it had already rewritten"
    assert _table_names(db) & {"functions_mig", "identities_mig"} == set()


def _table_names(db) -> set:
    conn = conn_of(db)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    return names


def test_a_killed_migration_rolls_back_and_the_retry_completes(tmp_path):
    """The rewrites build under temp names and swap inside one transaction. A
    process killed before the commit must leave a store the previous code still
    reads, and the next open must finish the job."""
    db = tmp_path / "crap.sqlite"
    rows = scored(40)
    prev_store(db, [rows])

    with mock.patch("crapkit.store._deflate", side_effect=RuntimeError("killed")):
        with pytest.raises(RuntimeError):
            SnapshotStore(db)

    conn = conn_of(db)
    survived = conn.execute("SELECT COUNT(*) FROM functions WHERE flag IS NOT NULL").fetchone()[0]
    conn.close()
    assert survived == 40, "no row was lost"
    assert storage_classes(db, "functions", "flag") == {"text"}, "the old table survived"

    assert SnapshotStore(db).read_scored(1) == export_order(rows), "the retry migrates cleanly"


def test_a_leftover_temp_table_does_not_block_the_retry(tmp_path):
    db = tmp_path / "crap.sqlite"
    rows = scored(40)
    prev_store(db, [rows])
    conn = conn_of(db)
    conn.executescript("CREATE TABLE functions_mig (junk INTEGER);"
                       "CREATE TABLE identities_mig (junk INTEGER);")
    conn.commit()
    conn.close()

    assert SnapshotStore(db).read_scored(1) == export_order(rows)


def test_the_oldest_shape_lands_coded_in_one_rewrite(tmp_path):
    """A store from before identities were normalized carries the strings on
    every row. It must arrive at the current shape in ONE table rewrite, not one
    to move the identities out and a second to code the verdicts."""
    db = tmp_path / "crap.sqlite"
    rows = scored(40)
    _oldest_store(db, rows)

    store = SnapshotStore(db)

    assert storage_classes(db, "functions", "flag") == {"integer"}
    assert unique_columns(db, "identities") == ("path", "long_name", "scope")
    assert store.read_scored(1) == export_order(rows)


def _oldest_store(db, rows: list) -> None:
    """The pre-identity shape: scope, path and long_name on the functions row."""
    ddl = PREV_SHAPE.replace(
        "    identity_id INTEGER NOT NULL REFERENCES identities(id),",
        "    scope TEXT NOT NULL, path TEXT NOT NULL, long_name TEXT NOT NULL,")
    ddl = "\n".join(line for line in ddl.splitlines() if "identity" not in line)
    conn = sqlite3.connect(db)
    conn.executescript(ddl)
    cur = conn.execute("INSERT INTO runs (commit_sha, tool_versions, lanes) "
                       "VALUES ('c0', '{}', ?)", (json.dumps(LANES, sort_keys=True),))
    conn.executemany(
        "INSERT INTO functions (run_id, scope, path, long_name, start, end, ccn_std, ccn_mod, "
        "ccn, nloc, params, nesting, cov, flag, crap, remedy, cognitive) VALUES (?" + ",?" * 16 + ")",
        [(cur.lastrowid, *r) for r in rows])
    conn.commit()
    conn.close()


def test_an_empty_previous_store_migrates(tmp_path):
    db = tmp_path / "crap.sqlite"
    prev_store(db, [])

    store = SnapshotStore(db)

    run_id = store.write_run(commit="c1", tool_versions={}, rows=scored(10), lanes=LANES)
    assert store.read_scored(run_id) == export_order(scored(10))


def test_an_identities_table_with_no_unique_index_is_rekeyed_on_open(tmp_path):
    """_identity_key answers () when the identities table carries no UNIQUE at
    all - a hand-built or half-migrated store. That answer must read as
    'wrong key', so the open restacks the table instead of trusting it."""
    db = tmp_path / "crap.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE identities (id INTEGER PRIMARY KEY, scope TEXT, path TEXT,"
        " long_name TEXT);"
        "CREATE TABLE functions (run_id INTEGER, identity_id INTEGER);"
        "CREATE TABLE runs (id INTEGER PRIMARY KEY, kind TEXT, commit_sha TEXT,"
        " created_at TEXT, lanes BLOB);")
    conn.commit()
    conn.close()

    store = SnapshotStore(db)

    unique = [r[1] for r in store._conn.execute("PRAGMA index_list(identities)") if r[2]]
    assert unique, "the open must give the bare table the UNIQUE the key demands"
    cols = tuple(r[2] for r in store._conn.execute(f'PRAGMA index_info("{unique[0]}")'))
    assert cols == ("path", "long_name", "scope")
