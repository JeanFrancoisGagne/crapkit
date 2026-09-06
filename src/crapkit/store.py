"""Append-only SQLite snapshot store.

Run metadata (commit, tool versions, lane provenance) lives on the run row;
scored rows are pure data keyed by run. Nothing here is ever updated in
place — a rebuild is a new run. Coverage columns are NULL on inventory-only
runs and populated on scored runs.

A function's identity — scope, path, long_name — lives once, in `identities`,
and every run's rows point at it by id. Storing the three strings on the row
rewrote a hundred thousand identities on every run; the flagship consumer's
store reached 246 MB that way. The reads join them back, so nothing above this
module can tell: read_rows and read_scored return the same values in the same
order, and identity ids never reach a sort key.

`runs prune` is the one exception to append-only, and it deletes whole runs
rather than editing any row: see prune_keep_set for what it may never take.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import zlib
from pathlib import Path
from typing import NamedTuple

from .keys import (claim_holds, claim_key, expression_group, expression_reader_current,
                   key_name, key_names, position,
                   refuse_ambiguous, split_ordinal)
from .packet import bare_name, handle_ordinal, matching_names
from .snapshot import InventoryRow
from .worklist import Marks
from .errors import ToolError

# {table} so the migration can build the same shape under a temp name and swap
# it in last: the live table is never dropped until its replacement is filled.
#
# flag and remedy are INTEGER codes into `flags` and `remedies`. The two columns
# held 14.7 MB of repeated short strings on the flagship consumer's store; the
# codes never leave this module, so every read still hands back "measured" and
# "add-tests".
_FUNCTIONS_DDL = """CREATE TABLE IF NOT EXISTS {table} (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    identity_id INTEGER NOT NULL REFERENCES identities(id),
    start INTEGER NOT NULL, end INTEGER NOT NULL,
    ccn_std INTEGER NOT NULL, ccn_mod INTEGER NOT NULL, ccn INTEGER NOT NULL,
    nloc INTEGER NOT NULL, params INTEGER NOT NULL, nesting INTEGER NOT NULL,
    cov REAL, flag INTEGER, crap REAL, remedy INTEGER,
    cognitive INTEGER NOT NULL DEFAULT 0,
    occurrence INTEGER NOT NULL DEFAULT 0
)"""

# The UNIQUE leads with the path, and that ordering IS the index the path-scoped
# reads seek: brief, explain and function_span all ask (path, long_name). A key
# led by scope answers none of them, which is why it needed a second index on
# (path, long_name) carried beside it.
_IDENTITY_KEY = ("path", "long_name", "scope")
_IDENTITIES_DDL = """CREATE TABLE IF NOT EXISTS {table} (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL, path TEXT NOT NULL, long_name TEXT NOT NULL,
    UNIQUE(path, long_name, scope)
)"""

_CODE_DDL = """CREATE TABLE IF NOT EXISTS {table} (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
)"""

# crapkit's own verdict vocabulary, at FIXED codes: insertion order would let two
# stores that met the same names in a different order hold different integers,
# and a store is a file people copy between machines. A name from outside this
# list is still stored, at a code minted after these.
_CODE_SEEDS = {"flags": ("measured", "untested", "no-lane", "cc-only"),
               "remedies": ("ok", "add-tests", "decompose")}

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commit_sha TEXT NOT NULL,
    tool_versions TEXT NOT NULL,
    lanes BLOB NOT NULL DEFAULT '{{}}',
    kind TEXT NOT NULL DEFAULT 'coverage',
    verdict_ok INTEGER,
    findings INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
{_IDENTITIES_DDL.format(table="identities")};
{_CODE_DDL.format(table="flags")};
{_CODE_DDL.format(table="remedies")};
{_FUNCTIONS_DDL.format(table="functions")};
CREATE TABLE IF NOT EXISTS overrides (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    path TEXT NOT NULL, long_name TEXT NOT NULL,
    crap REAL NOT NULL, reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL, long_name TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    closed_at TEXT,
    handle TEXT,
    key_name TEXT,
    key_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS run_rollup (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    ceiling_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    functions INTEGER NOT NULL,
    over_target INTEGER NOT NULL,
    crap_load REAL NOT NULL,
    PRIMARY KEY (run_id, ceiling_key, scope)
) WITHOUT ROWID;
"""

# Indexes run after the migration, never with it: on a store still in the old
# shape none of these columns exists yet.
#
# Two per-row indexes on functions, not three. A run-keyed seek and an
# identity-keyed seek are the only two shapes any read asks for; a third index
# keyed (run_id, identity_id) answered neither of them better and cost 10.8 MB
# and a fifth of the insert time on the flagship consumer's store.
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_functions_run ON functions(run_id);
CREATE INDEX IF NOT EXISTS idx_functions_identity ON functions(identity_id, run_id);
CREATE INDEX IF NOT EXISTS idx_attempts_open ON attempts(closed_at);
CREATE INDEX IF NOT EXISTS idx_attempts_identity ON attempts(path, key_name);
"""

# Indexes an earlier shape carried that nothing reads now. idx_identities_path
# is what the reordered UNIQUE replaced; both are dropped on open.
_DEAD_INDEXES = ("idx_functions_run_path", "idx_identities_path")

_CURRENT_OBJECTS = frozenset(("runs", "identities", "flags", "remedies", "functions",
                              "overrides", "attempts", "run_rollup", "idx_functions_run",
                              "idx_functions_identity", "idx_attempts_open", "idx_attempts_identity"))
_ADDED_COLUMNS = {"functions": {"cov", "flag", "crap", "remedy", "cognitive", "identity_id", "occurrence"},
                  "runs": {"lanes", "kind", "verdict_ok", "findings"},
                  "attempts": {"handle", "key_name", "key_version"}}

_JOINED = "FROM functions f JOIN identities i ON i.id = f.identity_id"
# Path-scoped reads, identities first. CROSS JOIN is SQLite's documented way to
# pin the outer table, and pinning it is the whole difference: a path names a
# handful of identities, a run names a hundred thousand rows, and with no
# table statistics the planner picks the run and scans it.
_BY_PATH = "FROM identities i CROSS JOIN functions f ON f.identity_id = i.id"
_ID_COLS = "i.scope, i.path, i.long_name"
_METRIC_COLS = "f.start, f.end, f.ccn_std, f.ccn_mod, f.ccn, f.nloc, f.params, f.nesting"
_INV_COLS = f"{_ID_COLS}, {_METRIC_COLS}, f.cognitive, f.occurrence"
_ALL_COLS = f"{_ID_COLS}, {_METRIC_COLS}, f.cov, f.flag, f.crap, f.remedy, f.cognitive, f.occurrence"
_CRAP_COLS = f"{_ID_COLS}, f.crap, f.start, f.occurrence"
# what write_run binds per row: everything but the three identity strings
_WRITE_COLS = ("start, end, ccn_std, ccn_mod, ccn, nloc, params, nesting, "
               "cov, flag, crap, remedy, cognitive, occurrence")
_N_COLS = _WRITE_COLS.count(",") + 3  # every _WRITE_COLS column plus run_id and identity_id
# the identity strings, never the ids: a row's place in an export may not depend
# on when its identity was first seen
_ROW_ORDER = "ORDER BY i.scope, i.path, f.start, f.occurrence, f.end, i.long_name"

def _selected(**substitutions: str) -> str:
    """_WRITE_COLS as a SELECT list off alias f, with named columns replaced.

    The migrations read the live table column for column; the two verdict
    columns arrive from their lookup tables instead.
    """
    return ", ".join(substitutions.get(col, f"f.{col}")
                     for col in _WRITE_COLS.replace(" ", "").split(","))


_CODED_COLS = _selected(flag="fl.id", remedy="rm.id")
_CODE_JOIN = "LEFT JOIN flags fl ON fl.name = f.flag LEFT JOIN remedies rm ON rm.name = f.remedy"

# Names this crapkit does not know still get a code rather than a NULL: the
# LEFT JOIN below would silently drop a verdict a newer scorer invented.
_HARVEST = tuple(
    f"INSERT OR IGNORE INTO {table} (name) SELECT DISTINCT {column} FROM functions "
    f"WHERE {column} IS NOT NULL AND typeof({column}) = 'text'"
    for table, column in (("flags", "flag"), ("remedies", "remedy")))

# The old-shape rewrite, in order, run as one transaction. The live table is
# read until the second-to-last statement and dropped only once its replacement
# is full, so an interrupt anywhere rolls back to a database the old code reads.
# It lands rows in the CURRENT shape, codes and all, so a pre-identity store
# needs one rewrite rather than two.
_IDENTITY_MIGRATION = (
    # a killed process leaves no temp table behind — SQLite rolls the whole
    # transaction back — but a retry must not trip over one either way
    "DROP TABLE IF EXISTS functions_mig",
    "DROP TABLE IF EXISTS identities",  # the empty one _SCHEMA just created
    _IDENTITIES_DDL.format(table="identities"),
    "INSERT INTO identities (scope, path, long_name) "
    "SELECT DISTINCT scope, path, long_name FROM functions ORDER BY scope, path, long_name",
    *_HARVEST,
    _FUNCTIONS_DDL.format(table="functions_mig"),
    f"INSERT INTO functions_mig (run_id, identity_id, {_WRITE_COLS}) "
    f"SELECT f.run_id, i.id, {_CODED_COLS} FROM functions f JOIN identities i "
    "ON i.scope = f.scope AND i.path = f.path AND i.long_name = f.long_name "
    f"{_CODE_JOIN}",
    "DROP TABLE functions",
    "ALTER TABLE functions_mig RENAME TO functions",
)

# Re-key the identity table. Nothing moves but the UNIQUE, so the ids on every
# functions row keep pointing at the same identity.
_REKEY_IDENTITIES = (
    "DROP TABLE IF EXISTS identities_mig",
    _IDENTITIES_DDL.format(table="identities_mig"),
    "INSERT INTO identities_mig (id, scope, path, long_name) "
    "SELECT id, scope, path, long_name FROM identities",
    "DROP TABLE identities",
    "ALTER TABLE identities_mig RENAME TO identities",
)

# Verdict strings to codes, same build-and-swap discipline.
_CODE_MIGRATION = (
    "DROP TABLE IF EXISTS functions_mig",
    *_HARVEST,
    _FUNCTIONS_DDL.format(table="functions_mig"),
    f"INSERT INTO functions_mig (run_id, identity_id, {_WRITE_COLS}) "
    f"SELECT f.run_id, f.identity_id, {_CODED_COLS} FROM functions f {_CODE_JOIN}",
    "DROP TABLE functions",
    "ALTER TABLE functions_mig RENAME TO functions",
)


class _Span(NamedTuple):
    """What `keys.key_names` reads off a row: where a function is and what it is called."""
    scope: str
    path: str
    long_name: str
    start: int
    occurrence: int


class CrapRow(NamedTuple):
    """A scored function reduced to what a comparison between two runs needs.

    Start counts same-named twins before comparing runs. The remaining columns
    supply their names, scores and scope ceilings without whole scored rows.
    """
    scope: str
    path: str
    long_name: str
    crap: float
    start: int
    occurrence: int = 0


class _Codes(NamedTuple):
    """One lookup table, both ways: names on the way in, back out on the way out."""
    ids: dict
    names: dict


def _read_codes(conn, table: str) -> _Codes:
    rows = conn.execute(f"SELECT id, name FROM {table}").fetchall()
    return _Codes({name: code for code, name in rows}, dict(rows))


def _code(ids: dict, name):
    """The stored code for a verdict string. None stays None: an inventory row
    has no verdict, and a store written before coverage existed holds NULLs."""
    return None if name is None else ids[name]


def _name(names: dict, code):
    """The verdict string a stored code stands for."""
    return None if code is None else names[code]


def _deflate(text: str) -> bytes:
    """A run's lane record, compressed. It is by far the largest thing on the run
    row — a failing lane records every failure by name — and JSON of that shape
    goes to a fifth of its bytes."""
    return zlib.compress(text.encode("utf-8"), 6)


def _inflate(stored) -> str:
    """The lane record back as JSON text. A row written before the column was
    deflated holds the text itself, so both storage classes read the same."""
    return zlib.decompress(stored).decode("utf-8") if isinstance(stored, bytes) else stored


def _verdict_names(rows: list) -> tuple[set, set]:
    """The flag and remedy strings this batch stores. Inventory rows carry
    neither, so they name nothing."""
    scored = [row for row in rows if len(row) in (16, 17)]
    return ({row[12] for row in scored} - {None}, {row[14] for row in scored} - {None})


def _writable(row, flags: dict, remedies: dict):
    """A row's metric columns in _WRITE_COLS order, identity dropped, verdict coded.

    Scored rows already carry the four coverage columns; inventory rows carry
    cognitive last, so the four unscored columns slot in before it.
    """
    occurrence = position(row)[1]
    if len(row) in (16, 17):
        return (*row[3:12], _code(flags, row[12]), row[13], _code(remedies, row[14]), row[15], occurrence)
    return (*row[3:11], None, None, None, None, row[11], occurrence)


def _own_ceilings(target: int, scope_targets: dict[str, int] | None) -> list[tuple[str, int]]:
    """The scopes whose ceiling is not the repo's, sorted.

    A scope that declares the repo target declares nothing: its branch and the
    ELSE say the same number. Dropping it is what lets a config with per-scope
    blocks but no per-scope targets compare against one bound parameter over a
    million rows of history.
    """
    return sorted((scope, ceiling) for scope, ceiling in (scope_targets or {}).items()
                  if ceiling != target)


class _Ceiling(NamedTuple):
    """The CRAP ceiling a row is compared against, as SQL."""
    expr: str
    params: list


def _ceiling_expr(target: int, scope_targets: dict[str, int] | None) -> _Ceiling:
    """The per-scope CRAP ceiling as a parameterized CASE, so an over-target count
    decided in SQL is decided exactly the way digest._over_count decides it."""
    own = _own_ceilings(target, scope_targets)
    params: list = []
    for scope, ceiling in own:
        params.extend((scope, ceiling))
    params.append(target)
    if not own:
        return _Ceiling("?", params)  # a CASE with no WHEN is not SQL
    return _Ceiling(f"CASE i.scope {' '.join('WHEN ? THEN ?' for _ in own)} ELSE ? END",
                    params)


def _ceiling_key(target: int, scope_targets: dict[str, int] | None) -> str:
    """The rollup cache key: the ceiling every stored number was decided against.

    _own_ceilings, not the raw dict. A scope whose target IS the repo target
    changes no answer, and keying on it would split the cache and pay for a
    second full scan of history for nothing. Nothing else belongs in the key:
    a run's rows never change after it is written, so within one ceiling a
    stored number cannot go stale.
    """
    return json.dumps([target, _own_ceilings(target, scope_targets)],
                      separators=(",", ":"), sort_keys=True)


# The rollup, cut per (run, scope): run_scope_totals answers it as-is and
# run_totals adds each run's scopes up, so one scan feeds both. `scope = ''` is
# the marker a filled run leaves whether or not it scored anything, and every
# read excludes it.
_ROLLUP_COLS = "run_id, ceiling_key, scope, functions, over_target, crap_load"
_ROLLUP_READ = ("SELECT run_id, scope, functions, over_target, crap_load FROM run_rollup "
                "WHERE ceiling_key = ? AND scope <> '' ORDER BY run_id, scope")


def _summed(by_scope: dict[str, tuple]) -> tuple:
    """One run's scopes added back up into the whole-run triple."""
    parts = list(by_scope.values())
    return (sum(n for n, _o, _l in parts), sum(o for _n, o, _l in parts),
            sum(load for *_x, load in parts))


def _by_run(rows) -> dict[int, dict[str, tuple]]:
    out: dict[int, dict[str, tuple]] = {}
    for run_id, scope, n, over, load in rows:
        out.setdefault(run_id, {})[scope] = (n, over, load)
    return out


def _scored_rows(cur, flags: dict, remedies: dict) -> list:
    """A cursor over _ALL_COLS, streamed into ScoredRows with identities interned.

    Rows stream off the cursor rather than through a fetchall list, and the
    identity strings are interned process-wide, so a second read reuses the
    first one's strings. The verdict columns need no interning: every row of a
    run shares the one string its code names.
    """
    from .score import ScoredRow
    si = sys.intern
    return [ScoredRow(si(scope), si(path), si(name), a, b, c, d, e, f, g, h, cov,
                      _name(flags, flag), crap, _name(remedies, remedy), cog, occurrence)
            for scope, path, name, a, b, c, d, e, f, g, h, cov, flag, crap, remedy, cog, occurrence in cur]


def _scope_clause(scopes, *, keyword: str = "IN") -> tuple[str, list]:
    """A parameterized `AND i.scope IN (...)`, empty when no scope was named.

    Deduplicated and sorted so the same request always builds the same SQL text,
    which is what lets SQLite reuse the prepared statement across calls. NOT IN
    is the same cut the other way: what a scope-blind read must leave behind.
    """
    if not scopes:
        return "", []
    names = sorted(set(scopes))
    return f"AND i.scope {keyword} ({','.join('?' * len(names))})", names


def _identity_where(run_id, path, name) -> tuple[str, list]:
    fields = [(column, value) for column, value in
              (("f.run_id", run_id), ("i.path", path), ("i.long_name", name))
              if value is not None]
    return " AND ".join(f"{column} = ?" for column, _ in fields) or "1", [v for _, v in fields]


class SnapshotStore:
    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._identities: dict[tuple[str, str, str], int] = {}
        self._conn = sqlite3.connect(str(path))
        if not self._current():
            self._prepare()
        self._codes = {table: _read_codes(self._conn, table) for table in _CODE_SEEDS}

    def _current(self) -> bool:
        """Detect actual schema and data, including writes by older crapkit.

        A current store owes no setup writes. Shape checks avoid a version stamp
        that an older writer could leave current while adding uncompressed rows.
        """
        objects = {r[0] for r in self._conn.execute("SELECT name FROM sqlite_master")}
        if not _CURRENT_OBJECTS <= objects or objects.intersection(_DEAD_INDEXES):
            return False
        return self._current_columns() and self._current_codes() and not self._text_lanes()

    def _current_columns(self) -> bool:
        have = all(columns <= self._existing_columns(table)
                   for table, columns in _ADDED_COLUMNS.items())
        return (have and self._identity_key() == _IDENTITY_KEY
                and self._declared_types("functions").get("flag") == "INTEGER")

    def _current_codes(self) -> bool:
        return all(set(enumerate(names, 1)) <= set(self._conn.execute(f"SELECT id, name FROM {table}"))
                   for table, names in _CODE_SEEDS.items())

    def _text_lanes(self) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM runs WHERE typeof(lanes) = 'text' LIMIT 1").fetchone() is not None

    def _prepare(self) -> None:
        """Create or upgrade a store only when its measured shape requires it."""
        self._conn.executescript(_SCHEMA)
        self._seed_codes()
        self._migrate()
        # after the migration, never with it: on a store still in the old shape
        # these index columns do not exist yet
        self._conn.executescript(_INDEXES)
        self._conn.commit()

    def _seed_codes(self) -> None:
        """The known verdict names, at their fixed codes. Before any migration:
        the rewrites resolve every stored string through these tables."""
        for table, names in _CODE_SEEDS.items():
            self._conn.executemany(
                f"INSERT OR IGNORE INTO {table} (id, name) VALUES (?, ?)",
                list(enumerate(names, 1)))

    def _migrate(self) -> None:
        # _SCHEMA and _INDEXES are migration paths in themselves: every statement
        # in them is IF NOT EXISTS and runs on every open, so a database written
        # before a table or an index existed grows it the next time it is opened.
        # What is left here is what CREATE cannot express — a column added to a
        # table that already exists, and the two rewrites.
        self._add_coverage_columns()
        self._add_cognitive_column()
        self._add_occurrence_column()
        self._add_run_provenance_columns()
        self._add_claim_handle_column()
        self._conn.commit()
        self._normalize_identities()
        self._restack()

    def size_bytes(self) -> int:
        """The file as the OS sees it: what a prune has to move to mean anything."""
        return self._path.stat().st_size if self._path.is_file() else 0

    def _declared_types(self, table: str) -> dict[str, str]:
        return {row[1]: row[2].upper() for row in self._conn.execute(f"PRAGMA table_info({table})")}

    def _existing_columns(self, table: str) -> set[str]:
        return set(self._declared_types(table))

    def _add_coverage_columns(self) -> None:
        have = self._existing_columns("functions")
        for col, decl in (("cov", "REAL"), ("flag", "INTEGER"),
                          ("crap", "REAL"), ("remedy", "INTEGER")):
            if col not in have:
                self._conn.execute(f"ALTER TABLE functions ADD COLUMN {col} {decl}")

    def _add_cognitive_column(self) -> None:
        if "cognitive" not in self._existing_columns("functions"):
            self._conn.execute(
                "ALTER TABLE functions ADD COLUMN cognitive INTEGER NOT NULL DEFAULT 0")

    def _add_occurrence_column(self) -> None:
        if "occurrence" not in self._existing_columns("functions"):
            self._conn.execute(
                "ALTER TABLE functions ADD COLUMN occurrence INTEGER NOT NULL DEFAULT 0")

    def _add_run_provenance_columns(self) -> None:
        run_cols = self._existing_columns("runs")
        if "lanes" not in run_cols:
            self._conn.execute("ALTER TABLE runs ADD COLUMN lanes TEXT NOT NULL DEFAULT '{}'")
        if "kind" not in run_cols:
            self._conn.execute("ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'coverage'")
            # rows that predate the column have unknown provenance; the DEFAULT
            # must not promote them to baseline-grade 'coverage'
            self._conn.execute("UPDATE runs SET kind = 'legacy'")
        if "verdict_ok" not in run_cols:
            self._conn.execute("ALTER TABLE runs ADD COLUMN verdict_ok INTEGER")
        if "findings" not in run_cols:
            self._conn.execute(
                "ALTER TABLE runs ADD COLUMN findings INTEGER NOT NULL DEFAULT 0")

    def _add_claim_handle_column(self) -> None:
        """The name a claim was taken under, beside the long_name it was taken on.

        An anonymous function's long_name is `(anonymous)` for every anonymous
        function in its file, so a release by long_name closes whichever claim
        sorts first. The handle is the ordinal that tells them apart, and it is
        stored rather than recomputed: the whole point is that it survives the
        line shifts the session's own edit makes.
        """
        if "handle" not in self._existing_columns("attempts"):
            self._conn.execute("ALTER TABLE attempts ADD COLUMN handle TEXT")
        if "key_name" not in self._existing_columns("attempts"):
            self._conn.execute("ALTER TABLE attempts ADD COLUMN key_name TEXT")
        if "key_version" not in self._existing_columns("attempts"):
            self._conn.execute("ALTER TABLE attempts ADD COLUMN key_version INTEGER NOT NULL DEFAULT 0")

    def _normalize_identities(self) -> None:
        """Move scope, path and long_name out of every functions row.

        The old shape is the one that still has a scope column. The rewrite runs
        as one transaction and swaps the table in last, so an interrupt leaves
        the old table whole, rows and all, and the next open retries from there.
        Old databases migrate silently; `runs prune` reclaims the freed pages.
        """
        if "scope" not in self._existing_columns("functions"):
            return
        self._conn.execute("BEGIN")  # DDL does not open one, and this must be atomic
        try:
            for statement in _IDENTITY_MIGRATION:
                self._conn.execute(statement)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _identity_key(self) -> tuple:
        """The columns of the identity UNIQUE, in key order."""
        unique = [row[1] for row in self._conn.execute("PRAGMA index_list(identities)") if row[2]]
        if not unique:
            return ()
        return tuple(row[2] for row in self._conn.execute(f'PRAGMA index_info("{unique[0]}")'))

    def _restack_steps(self) -> tuple:
        """The rewrites this store still owes, in the order they must run.

        Both are version-detected off the schema itself rather than a stamp: a
        store carries its own shape, and a stamp is one more thing to get wrong.
        """
        steps = tuple(f"DROP INDEX IF EXISTS {name}" for name in _DEAD_INDEXES)
        if self._identity_key() != _IDENTITY_KEY:
            steps += _REKEY_IDENTITIES
        if self._declared_types("functions").get("flag") == "TEXT":
            steps += _CODE_MIGRATION
        return steps

    def _deflate_lanes(self) -> None:
        """Compress the lane records still held as text. Never conditional on the
        rewrites: a store this code restacked can still be written by an older
        crapkit, and the next open should take those rows too."""
        rows = self._conn.execute(
            "SELECT id, lanes FROM runs WHERE typeof(lanes) = 'text'").fetchall()
        self._conn.executemany("UPDATE runs SET lanes = ? WHERE id = ?",
                               [(_deflate(text), rid) for rid, text in rows])

    def _restack(self) -> None:
        """Re-key the identities, code the verdicts, deflate the lane records.

        Together these took the flagship consumer's store from 131.4 to 92.3 MB
        with every timed read faster. One transaction, tables built under temp
        names and swapped in last, so a process killed anywhere in here leaves a
        database the previous code still reads and the next open retries.
        """
        self._conn.execute("BEGIN")  # DDL does not open one, and this must be atomic
        try:
            for statement in self._restack_steps():
                self._conn.execute(statement)
            self._deflate_lanes()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _cache_identities(self) -> None:
        """Every identity in the store, keyed by triple.

        One scan, and the strings are interned, so the cache shares them with
        the rows a read of the same store already built.
        """
        si = sys.intern
        self._identities = {
            (si(scope), si(path), si(name)): rid
            for rid, scope, path, name in self._conn.execute(
                "SELECT id, scope, path, long_name FROM identities")}

    def _identity_ids(self, rows: list) -> dict[tuple[str, str, str], int]:
        """(scope, path, long_name) -> identities.id, covering every row.

        One pass over the rows and at most two over the identity table, never a
        lookup per row: a rebuild rewrites the same hundred thousand identities
        every run. The reload after the insert is what makes INSERT OR IGNORE
        safe — a triple a concurrent writer got in first is read back rather
        than left unresolved.
        """
        if not self._identities:
            self._cache_identities()
        fresh = sorted({row[:3] for row in rows} - self._identities.keys())
        if fresh:
            self._conn.executemany(
                "INSERT OR IGNORE INTO identities (scope, path, long_name) VALUES (?, ?, ?)",
                fresh)
            self._cache_identities()
        return self._identities

    def _code_ids(self, table: str, names: set) -> dict:
        """name -> code, covering every name this batch will store.

        The seeded vocabulary answers every name a crapkit run produces, so this
        inserts nothing in practice. A name from somewhere else is minted a code
        rather than dropped, and the reload after the insert is what makes
        INSERT OR IGNORE safe against a concurrent writer.
        """
        fresh = sorted(names - self._codes[table].ids.keys())
        if fresh:
            self._conn.executemany(f"INSERT OR IGNORE INTO {table} (name) VALUES (?)",
                                   [(name,) for name in fresh])
            self._codes[table] = _read_codes(self._conn, table)
        return self._codes[table].ids

    def write_run(self, *, commit: str, tool_versions: dict[str, str], rows: list,
                  lanes: dict | None = None, kind: str = "coverage") -> int:
        flag_names, remedy_names = _verdict_names(rows)
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO runs (commit_sha, tool_versions, lanes, kind) VALUES (?, ?, ?, ?)",
                (commit, json.dumps(tool_versions, sort_keys=True),
                 _deflate(json.dumps(lanes or {}, sort_keys=True)), kind),
            )
            run_id = cur.lastrowid
            ids = self._identity_ids(rows)
            flags = self._code_ids("flags", flag_names)
            remedies = self._code_ids("remedies", remedy_names)
            # a generator, not two lists: executemany consumes any iterator, and
            # materializing the padded copy doubled the rows in memory alongside
            # the caller's own list for the length of the insert
            self._conn.executemany(
                f"INSERT INTO functions (run_id, identity_id, {_WRITE_COLS}) "
                f"VALUES ({','.join('?' * _N_COLS)})",
                ((run_id, ids[row[:3]], *_writable(row, flags, remedies)) for row in rows),
            )
        return run_id

    def read_rows(self, run_id: int, *, min_ccn: int = 0,
                  scopes: list[str] | None = None) -> list[InventoryRow]:
        """Inventory rows for one run, in export order.

        min_ccn is the caller's admission floor pushed into the scan: a row a
        worklist could never admit costs nothing to leave in SQLite. scopes is
        the same idea for a scope-shaped cut, decided on the joined identity.

        Rows stream off the cursor rather than through a fetchall list, and the
        identity strings are interned. A run holds thousands of distinct paths
        repeated across a hundred thousand rows, and interning is process-wide,
        so a second run read in the same process reuses the first one's strings.
        """
        clause, names = _scope_clause(scopes)
        cur = self._conn.execute(
            f"SELECT {_INV_COLS} {_JOINED} WHERE f.run_id = ? AND f.ccn >= ? "
            f"{clause} {_ROW_ORDER}",
            (run_id, min_ccn, *names),
        )
        si = sys.intern
        return [InventoryRow(si(scope), si(path), si(name), a, b, c, d, e, f, g, h, cog, occurrence)
                for scope, path, name, a, b, c, d, e, f, g, h, cog, occurrence in cur]

    def read_scored(self, run_id: int, *, min_ccn: int = 0,
                    scopes: list[str] | None = None) -> list:
        """Scored rows for one run, same streaming and interning as read_rows.

        This is where interning pays: a digest holds two runs at once, and the
        second one costs almost nothing for paths the first already interned.
        """
        clause, names = _scope_clause(scopes)
        return self._scored(self._conn.execute(
            f"SELECT {_ALL_COLS} {_JOINED} WHERE f.run_id = ? AND f.crap IS NOT NULL "
            f"AND f.ccn >= ? {clause} {_ROW_ORDER}",
            (run_id, min_ccn, *names),
        ))

    def _scored(self, cur) -> list:
        return _scored_rows(cur, self._codes["flags"].names, self._codes["remedies"].names)

    def read_crap(self, run_id: int) -> list[CrapRow]:
        """One run's names, scores, scopes and starts for ordinal matching.

        Digest holds two runs at once. These five fields retain exact identity
        without building the other eleven fields of every scored row.
        """
        cur = self._conn.execute(
            f"SELECT {_CRAP_COLS} {_JOINED} WHERE f.run_id = ? AND f.crap IS NOT NULL "
            f"{_ROW_ORDER}", (run_id,))
        si = sys.intern
        return [CrapRow(si(scope), si(path), si(name), crap, start, occurrence)
                for scope, path, name, crap, start, occurrence in cur]

    def read_scored_file(self, run_id: int, path: str) -> list:
        """One file's scored rows: the path seeks identities, the ids seek the run.

        A brief asks about a single function; reading the whole run to find it
        materializes every other row of a hundred-thousand-function repo.
        """
        return self._scored(self._conn.execute(
            f"SELECT {_ALL_COLS} {_BY_PATH} WHERE i.path = ? AND f.run_id = ? "
            f"AND f.crap IS NOT NULL {_ROW_ORDER}",
            (path, run_id),
        ))

    def read_positions(self, run_id: int, path: str) -> list[_Span]:
        """One file's complete positions, including rows ranking filters omit."""
        cur = self._conn.execute(
            f"SELECT i.scope, i.path, i.long_name, f.start, f.occurrence {_BY_PATH} "
            "WHERE i.path = ? AND f.run_id = ? ORDER BY f.start, f.occurrence, i.long_name",
            (path, run_id))
        return [_Span(*row) for row in cur]

    def read_marks(self, run_id: int, *, min_ccn: int = 0,
                   scopes: list[str] | None = None) -> Marks:
        """Verdict and score by full function location, from one narrow read.

        Duplicate scopes use the highest score for their shared span. Twins
        keep separate verdicts so a finished sibling stays finished.
        """
        self._require_identity(run_id=run_id)
        clause, names = _scope_clause(scopes)
        cur = self._conn.execute(
            f"SELECT i.path, i.long_name, f.start, f.occurrence, f.flag, f.remedy, f.crap, f.cov {_JOINED} "
            f"WHERE f.run_id = ? AND f.remedy IS NOT NULL AND f.ccn >= ? {clause} "
            "ORDER BY f.crap",
            (run_id, min_ccn, *names))
        flags, remedies = self._codes["flags"].names, self._codes["remedies"].names
        verdicts, scores = {}, {}
        for path, name, start, occurrence, flag, remedy, crap, cov in cur:
            verdicts[(path, name, start, occurrence)] = (_name(flags, flag), _name(remedies, remedy))
            scores[(path, name, start, occurrence)] = (crap, cov)
        return Marks(verdicts, scores)

    def twin_key_names(self, run_id: int) -> dict[tuple[str, str, int, int], str]:
        """The ratchet key name of every function that shares its long_name
        with another in its file, by (path, long_name, start, occurrence).

        Every other function's key IS its long_name, so the map holds only the
        rows whose key carries an ordinal: a handful, on a run of a hundred
        thousand rows. The worklist reads rows cut at a floor and a scope, and
        an ordinal counted over that cut would hand twin #2 the bare key, and
        with it twin #1's mark; the count runs over the whole run here, the
        way `brief` counts it over the whole file.
        """
        self._require_identity(run_id=run_id)
        cur = self._conn.execute(
            f"SELECT i.scope, i.path, i.long_name, f.start, f.occurrence {_JOINED} WHERE f.run_id = ? "
            "AND (i.path, i.long_name) IN ("
            "SELECT t.path, t.long_name FROM functions g JOIN identities t ON t.id = g.identity_id "
            "WHERE g.run_id = ? GROUP BY t.path, t.long_name "
            "HAVING COUNT(DISTINCT g.start) > 1 OR COUNT(DISTINCT g.occurrence) > 1)",
            (run_id, run_id))
        return key_names([_Span(*row) for row in cur])

    def historical_collision_groups(self) -> set[tuple[str, str]]:
        """Raw-name groups that contained same-line twins in any stored run."""
        return self._collision_groups()

    def identity_witness_run_ids(self) -> set[int]:
        """One recent run per collision group keeps legacy key checks unchanged."""
        newest = {}
        for path, name, run_id in self._collision_rows():
            key = path, name
            newest[key] = max(run_id, newest.get(key, 0))
        return set(newest.values())

    def _collision_groups(self, *, run_id=None, path=None, name=None,
                          legacy_only: bool = False) -> set[tuple[str, str]]:
        return {(path, name) for path, name, _ in self._collision_rows(
            run_id=run_id, path=path, name=name, legacy_only=legacy_only)}

    def _collision_rows(self, *, run_id=None, path=None, name=None, legacy_only=False):
        where, params = _identity_where(run_id, path, name)
        mixed = "MIN(f.occurrence) = 0 AND MAX(f.occurrence) > 0"
        multiple = mixed if legacy_only else "COUNT(DISTINCT f.occurrence) > 1"
        joined = _BY_PATH if path is not None else _JOINED
        return self._conn.execute(
            f"SELECT i.path, i.long_name, f.run_id {joined} WHERE {where} "
            "GROUP BY f.run_id, i.scope, i.path, i.long_name, f.start "
            f"HAVING SUM(f.occurrence = 0) > 1 OR ({multiple})", params)

    def _require_identity(self, *, run_id=None, path=None, name=None) -> None:
        refuse_ambiguous(self._collision_groups(run_id=run_id, path=path, name=name,
                                               legacy_only=True))

    def count_by_path(self, run_id: int, *, flag: str,
                      skip_scopes=frozenset()) -> list[tuple[str, int, int]]:
        """Per path in one run: (path, functions, others), path order.

        `others` counts the rows whose flag is NOT the named one, which is all
        doctor asks of a run: a directory is a measurement gap only when nothing
        in it carries any other verdict. A run holds a hundred thousand rows and
        a few thousand paths, so the grouping belongs where the rows are — and
        the scopes to leave out belong in the WHERE, not in a Python filter that
        reads them first.
        """
        clause, names = _scope_clause(skip_scopes, keyword="NOT IN")
        cur = self._conn.execute(
            f"SELECT i.path, COUNT(*), SUM(f.flag IS NOT ?) {_JOINED} "
            f"WHERE f.run_id = ? AND f.crap IS NOT NULL {clause} GROUP BY i.path "
            "ORDER BY i.path",
            (_code(self._codes["flags"].ids, flag), run_id, *names))
        return cur.fetchall()

    def count_scored_below(self, run_id: int, min_ccn: int,
                           scopes: list[str] | None = None) -> int:
        """The rows read_scored(min_ccn=...) skipped. An empty queue still has to
        report them, and one COUNT is cheaper than reading them to count them.

        Same scopes the read took, or a scoped queue reports rows it was never
        going to offer.
        """
        clause, names = _scope_clause(scopes)
        cur = self._conn.execute(
            f"SELECT COUNT(*) {_JOINED} WHERE f.run_id = ? AND f.crap IS NOT NULL "
            f"AND f.ccn < ? {clause}",
            (run_id, min_ccn, *names))
        return cur.fetchone()[0]

    def _unrolled(self, key: str) -> list[int]:
        """The runs this ceiling has never been summed for, oldest first."""
        cur = self._conn.execute(
            "SELECT id FROM runs WHERE id NOT IN "
            "(SELECT run_id FROM run_rollup WHERE ceiling_key = ?) ORDER BY id", (key,))
        return [rid for (rid,) in cur]

    def _fill_rollup(self, key: str, run_ids: list[int], target: int,
                     scope_targets: dict[str, int] | None) -> dict[int, dict[str, tuple]]:
        """Sum the named runs per scope, store the result, and hand it back.

        The GROUP BY is the one trend used to run over the whole table on every
        invocation, narrowed to the runs nothing has summed yet. Handing the
        numbers back rather than re-reading them is what lets the write fail
        without failing the command.
        """
        ceiling = _ceiling_expr(target, scope_targets)
        holes = ",".join("?" * len(run_ids))
        cur = self._conn.execute(
            f"SELECT f.run_id, i.scope, COUNT(*), SUM(f.crap > {ceiling.expr}), "
            f"SUM(f.crap) {_JOINED} WHERE f.crap IS NOT NULL AND f.run_id IN ({holes}) "
            "GROUP BY f.run_id, i.scope ORDER BY f.run_id, i.scope",
            (*ceiling.params, *run_ids))
        scored = cur.fetchall()
        # the marker first, so a run that scored nothing still reads as filled
        self._store_rollup([(rid, key, "", 0, 0, 0.0) for rid in run_ids]
                           + [(rid, key, scope, n, over, load)
                              for rid, scope, n, over, load in scored])
        return _by_run(scored)

    def _store_rollup(self, rows: list[tuple]) -> None:
        """Best effort. trend and report WRITE now, and two crapkit processes
        reading one store can collide on the fill; losing the cache is a cost,
        losing the command is a bug."""
        try:
            with self._conn:
                self._conn.executemany(
                    f"INSERT OR REPLACE INTO run_rollup ({_ROLLUP_COLS}) VALUES (?, ?, ?, ?, ?, ?)",
                    rows)
        except sqlite3.OperationalError:
            pass  # another process holds the write lock

    def _rollup(self, target: int,
                scope_targets: dict[str, int] | None) -> dict[int, dict[str, tuple]]:
        """Every run's (functions, over_target, crap_load) per scope, off the cache.

        trend and report used to re-derive this from every scored row of every
        run, twice, on every invocation. A run is immutable once written, so the
        only thing that can change its numbers is the ceiling they were decided
        against, and that is the key.
        """
        key = _ceiling_key(target, scope_targets)
        missing = self._unrolled(key)
        filled = self._fill_rollup(key, missing, target, scope_targets) if missing else {}
        out = _by_run(self._conn.execute(_ROLLUP_READ, (key,)))
        out.update(filled)  # the fill wins: its write may have lost the lock
        return out

    def run_totals(self, *, target: int,
                   scope_targets: dict[str, int] | None = None) -> dict[int, tuple]:
        """Per-run (functions, over_target, crap_load), added up from the rollup.

        Summing the per-scope rows is what keeps the whole history one scan
        instead of two: the per-scope cut is the finer one, and the whole-run
        numbers fall out of it.
        """
        return {run_id: _summed(by_scope)
                for run_id, by_scope in self._rollup(target, scope_targets).items()}

    def run_scope_totals(self, *, target: int,
                         scope_targets: dict[str, int] | None = None) -> dict[int, dict[str, tuple]]:
        """run_totals cut one level finer: (functions, over_target, crap_load) per
        (run, scope). The grain the rollup is stored at, so this is the raw read."""
        return self._rollup(target, scope_targets)

    def function_span(self, run_id: int, path: str, long_name: str) -> tuple | None:
        """One keyed function's span; duplicate scopes count as one twin."""
        name, ordinal = split_ordinal(long_name)
        self._require_identity(run_id=run_id, path=path, name=name)
        cur = self._conn.execute(
            f"SELECT f.start, f.end {_BY_PATH} WHERE i.path = ? AND i.long_name = ? "
            "AND f.run_id = ? GROUP BY f.start, f.occurrence "
            "ORDER BY f.start, f.occurrence LIMIT 1 OFFSET ?",
            (path, name, run_id, ordinal - 1))
        return cur.fetchone()

    def set_verdict_ok(self, run_id: int, ok: bool, *, findings: int = 0) -> None:
        """Stamp a verdict on a run, with how many findings it carried.

        The count is what a later refusal quotes back: a baseline that skips
        this run has to say what it is protecting, and re-deriving it would mean
        rerunning the lanes on a tree that has moved on.
        """
        with self._conn:
            self._conn.execute("UPDATE runs SET verdict_ok = ?, findings = ? WHERE id = ?",
                               (1 if ok else 0, findings, run_id))

    def list_runs(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT id, commit_sha, tool_versions, lanes, kind, verdict_ok, findings, "
            "created_at FROM runs ORDER BY id")
        return [
            {"id": rid, "commit": sha, "tool_versions": json.loads(tv),
             "lanes": json.loads(_inflate(lanes)), "kind": kind,
             "verdict_ok": None if ok is None else bool(ok), "findings": findings,
             "created_at": ts}
            for rid, sha, tv, lanes, kind, ok, findings, ts in cur.fetchall()
        ]

    def write_overrides(self, run_id: int, rows: list[tuple[str, str, float, str]]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT INTO overrides (run_id, path, long_name, crap, reason) VALUES (?,?,?,?,?)",
                [(run_id, *r) for r in rows],
            )

    def read_overrides(self, run_id: int) -> list[tuple[str, str, float, str]]:
        cur = self._conn.execute(
            "SELECT path, long_name, crap, reason FROM overrides WHERE run_id = ? ORDER BY path, long_name",
            (run_id,))
        return list(cur.fetchall())

    def record_claim(self, *, path: str, long_name: str, commit: str,
                     handle: str | None = None, key_name: str | None = None,
                     source_run_id: int | None = None) -> int | None:
        """Take a claim on one function. Opt-in: nothing writes here unless a
        session asked for it, so a store with no claims answers every query the
        way it did before claims existed.

        `handle` is the name the claim was handed out under. None is the honest
        answer for a caller that never had one, and reads back as null.
        A pre-10 expression snapshot cannot prove an anonymous ordinal; its
        claim holds the raw-name group until released, pruned, or all healthy.
        """
        version = self._claim_key_version(path, long_name, source_run_id)
        key = claim_key({"path": path, "long_name": long_name, "handle": handle,
                         "key_name": key_name, "key_version": version})
        precise = key[1] if key else None
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            if self._claim_conflict(path, long_name, key):
                return None
            cur = self._conn.execute(
                "INSERT INTO attempts (path, long_name, commit_sha, handle, key_name, key_version) "
                "VALUES (?, ?, ?, ?, ?, ?)", (path, long_name, commit, handle, precise, version))
        return cur.lastrowid

    def _claim_key_version(self, path: str, name: str, source_run_id: int | None) -> int:
        if not expression_group(path, name):
            return 1
        row = self._conn.execute("SELECT tool_versions FROM runs WHERE id = ?",
                                 (source_run_id,)).fetchone()
        versions = json.loads(row[0]) if row else {}
        return int(expression_reader_current(versions.get("analysis_version")))

    def _claim_conflict(self, path: str, name: str, key: tuple | None) -> bool:
        cur = self._conn.execute(
            "SELECT handle, key_name, key_version FROM attempts WHERE path = ? AND long_name = ? "
            "AND closed_at IS NULL", (path, name))
        return any(key is None or claim_holds(
            {"path": path, "long_name": name, "handle": handle, "key_name": precise,
             "key_version": version}, key) for handle, precise, version in cur)

    def open_claims(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT id, path, long_name, commit_sha, created_at, handle, key_name, key_version FROM attempts "
            "WHERE closed_at IS NULL ORDER BY id")
        return [{"id": cid, "path": p, "long_name": n, "commit": sha,
                 "created_at": ts, "handle": handle, "key_name": name, "key_version": version}
                for cid, p, n, sha, ts, handle, name, version in cur]

    def attempts_for(self, keys) -> dict[tuple[str, str], list[dict]]:
        """Every claim ever taken on each named function, oldest first.

        One query for the whole batch, filtered on the indexed path and paired
        up here: a packet run asks about N functions, and a query apiece is N
        round trips for what one path filter already returns. Every requested
        key is in the answer, so a function nobody ever claimed reads as [].
        """
        wanted = list(dict.fromkeys(keys))
        found: dict[tuple[str, str], list[dict]] = {key: [] for key in wanted}
        if not wanted:
            return found
        paths = sorted({path for path, _ in wanted})
        cur = self._conn.execute(
            "SELECT path, long_name, handle, key_name, key_version, created_at, closed_at FROM attempts "
            f"WHERE path IN ({','.join('?' * len(paths))}) ORDER BY id", paths)
        self._pair_attempts(cur, found)
        return found

    @staticmethod
    def _pair_attempts(rows, found: dict) -> None:
        grouped: dict[tuple, list] = {}
        for key in found:
            grouped.setdefault((key[0], split_ordinal(key[1])[0]), []).append(key)
        for path, name, handle, precise, version, opened, closed in rows:
            claim = {"path": path, "long_name": name, "handle": handle, "key_name": precise,
                     "key_version": version}
            for key in grouped.get((path, name), ()):
                if claim_holds(claim, key):
                    found[key].append({"opened": opened, "closed": closed})

    def close_claims(self, claim_ids) -> int:
        """Stamp the named claims closed; already-closed ones are left alone, so
        a verify that runs twice closes the same claim once."""
        ids = sorted(claim_ids)
        if not ids:
            return 0
        with self._conn:
            cur = self._conn.execute(
                "UPDATE attempts SET closed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                f"WHERE closed_at IS NULL AND id IN ({','.join('?' * len(ids))})", ids)
        return cur.rowcount

    def _oldest_kept_at(self, keep_ids: set[int]) -> str | None:
        ids = sorted(keep_ids)
        if not ids:
            return None
        cur = self._conn.execute(
            f"SELECT MIN(created_at) FROM runs WHERE id IN ({','.join('?' * len(ids))})", ids)
        return cur.fetchone()[0]

    def prune_claims(self, keep_ids: set[int]) -> int:
        """Drop claims older than the oldest run a prune keeps.

        Explicit pruning expires ownership before the retained history, even
        when the function still exists. Ordinary queue reads never expire it.
        """
        floor = self._oldest_kept_at(keep_ids)
        if floor is None:
            return 0
        with self._conn:
            cur = self._conn.execute("DELETE FROM attempts WHERE created_at < ?", (floor,))
        return cur.rowcount

    def prior_scored_run(self, *, commit: str, before: int) -> int | None:
        """The newest earlier TRUSTED run of one commit, for the tighten damping.

        `trusted_runs` and nothing else, which is the list `ratchet seed` picks
        its baseline out of: one question about run history deserves one answer.
        It drops hook runs (no rows) and inventory runs (no CRAP) the way the
        "scored something" row test used to, and it also drops the two the row
        test admitted — a FAILED verify, whose scores can come off a red tree,
        and a `partial` run, whose coverage is a fraction of the suite's and
        whose CRAP is inflated to match. Damping a mark against either freezes
        it at a number no other reader will accept.

        An empty answer reads as "nothing moved", which is exactly the licence a
        bouncing measurement needs to tighten a mark.
        """
        earlier = [r for r in trusted_runs(self)
                   if r["commit"] == commit and r["id"] < before]
        return earlier[-1]["id"] if earlier else None

    def latest_run(self, *, commit: str) -> int | None:
        cur = self._conn.execute("SELECT MAX(id) FROM runs WHERE commit_sha = ?", (commit,))
        (rid,) = cur.fetchone()
        return rid


    def read_overrides_all(self) -> list[tuple]:
        """The full audit trail: (run_id, path, long_name, crap, reason, created_at, commit)."""
        cur = self._conn.execute(
            """SELECT o.run_id, o.path, o.long_name, o.crap, o.reason, r.created_at, r.commit_sha
               FROM overrides o JOIN runs r ON r.id = o.run_id ORDER BY o.run_id, o.path""")
        return list(cur.fetchall())

    def find_functions(self, path: str, name_fragment: str) -> list[str]:
        """The long_names in a path that one NAME resolves to, across all runs.

        `packet.matching_names` is the rule, shared with `brief`: exact first,
        the fragment only when nothing matches exactly. This used to be a SQL
        `LIKE '%name%'` and nothing else, so `explain src/lib.rs route` printed
        the trajectories of `route`, `route_chain` and `route_num` for a
        question about one function. Matching in Python also makes `_` and `%`
        the characters they are rather than LIKE's wildcards.

        A bare start line selects only when one function opens there. Same-line
        callbacks require a handle, as they do in `brief`.

        `(anonymous)#N` is resolved by position instead: an anonymous function
        carries no text to match, and the fragment would otherwise hunt for a
        `#` no long_name has.

        A named `f#2` is the twin selector, so the `#2` comes off before the
        match: no long_name carries it, and the caller re-attaches it to reach
        that twin's ratchet key.
        """
        if name_fragment.isdigit():
            return self._at_start_line(path, int(name_fragment))
        ordinal = handle_ordinal(name_fragment)
        if ordinal is not None:
            return self._nth_anonymous(path, ordinal)
        return matching_names(self._long_names(path), split_ordinal(name_fragment)[0])

    def _long_names(self, path: str) -> list[str]:
        """Every distinct long_name a surviving run scored in this path.

        Joined to functions rather than read off identities alone: a prune drops
        the rows of a run and leaves its identities behind, and a name no
        surviving run scored is a name `brief` cannot resolve.
        """
        cur = self._conn.execute(
            f"SELECT DISTINCT i.long_name {_BY_PATH} WHERE i.path = ? "
            "ORDER BY i.long_name", (path,))
        return [n for (n,) in cur]

    def _at_start_line(self, path: str, start: int) -> list[str]:
        """The function opening on this line, or nothing when none does.

        Read off the newest run that scored the path, like the anonymous
        ordinal: a line number names a position in the file as it stands now,
        and an older run held other positions. `brief` answers the same line off
        the same file, so a session that read a line out of one packet can hand
        it to either command.
        """
        run_id = self._newest_run_for(path)
        if run_id is None:
            return []
        self._require_line(path, run_id, start)
        cur = self._conn.execute(
            f"SELECT DISTINCT i.long_name {_BY_PATH} WHERE i.path = ? AND f.run_id = ? AND f.start = ?",
            (path, run_id, start))
        return [n for (n,) in cur]

    def _nth_anonymous(self, path: str, ordinal: int) -> list[str]:
        """The path's Nth anonymous function, or nothing when there is no Nth.

        Read off the newest run that scored the path, because the ordinal names
        a position in the file as it stands now; an older run held other
        positions. Nothing found is a list, not an error: the caller already
        reports a name that matched no function.
        """
        names = self._anonymous_names(path)
        return names[ordinal - 1:ordinal] if ordinal >= 1 else []

    def _anonymous_names(self, path: str) -> list[str]:
        """The long_names of the path's anonymous functions, in file order."""
        run_id = self._newest_run_for(path)
        if run_id is None:
            return []
        self._require_identity(run_id=run_id, path=path)
        cur = self._conn.execute(
            f"SELECT DISTINCT f.start, f.occurrence, i.long_name {_BY_PATH} "
            "WHERE i.path = ? AND f.run_id = ? ORDER BY f.start, f.occurrence, i.long_name", (path, run_id))
        return [n for _, _, n in cur if not bare_name(n)]

    def function_key(self, path: str, long_name: str, selector: str) -> str:
        """The key of an already-resolved selector, including inventory rows."""
        self._require_identity(run_id=self._newest_run_for(path), path=path, name=long_name)
        if selector.isdigit():
            return key_name(long_name, self._ordinal_at_start(path, long_name, int(selector)))
        anonymous = handle_ordinal(selector)
        ordinal = (self._anonymous_names(path)[:anonymous].count(long_name)
                   if anonymous is not None else split_ordinal(selector)[1])
        return key_name(long_name, ordinal)

    def _ordinal_at_start(self, path: str, name: str, start: int) -> int:
        run_id = self._newest_run_for(path)
        self._require_line(path, run_id, start)
        cur = self._conn.execute(
            f"SELECT COUNT(*) FROM (SELECT DISTINCT f.start, f.occurrence {_BY_PATH} "
            "WHERE i.path = ? AND i.long_name = ? AND f.run_id = ? AND f.start <= ?)",
            (path, name, run_id, start))
        return cur.fetchone()[0]

    def _require_line(self, path: str, run_id: int | None, start: int) -> None:
        self._require_identity(run_id=run_id, path=path)
        cur = self._conn.execute(
            f"SELECT DISTINCT i.long_name, f.occurrence {_BY_PATH} "
            "WHERE i.path = ? AND f.run_id = ? AND f.start = ?", (path, run_id, start))
        if len(cur.fetchmany(2)) > 1:
            raise ToolError(f"line {start} in {path} identifies multiple functions; use a function handle")

    def _newest_run_for(self, path: str) -> int | None:
        """The last run that holds a row for this path. Hook runs carry no rows,
        so they never win it."""
        cur = self._conn.execute(f"SELECT MAX(f.run_id) {_BY_PATH} WHERE i.path = ?",
                                 (path,))
        return cur.fetchone()[0]

    def function_history(self, path: str, long_name: str) -> list[dict]:
        """One row per run this function appears in: the trajectory behind a verdict.

        The path seeks identities once; (identity_id, run_id) then hands back
        every run that scored it, already in run order.
        """
        name, ordinal = split_ordinal(long_name)
        self._require_identity(path=path, name=name)
        cur = self._conn.execute(
            f"""WITH history AS (
                SELECT f.run_id, r.commit_sha, r.kind, r.created_at,
                       f.ccn, f.cov, f.flag, f.crap,
                       DENSE_RANK() OVER (PARTITION BY f.run_id ORDER BY f.start, f.occurrence) AS twin,
                       ROW_NUMBER() OVER (PARTITION BY f.run_id, f.start, f.occurrence
                                          ORDER BY f.crap DESC, i.scope) AS copy
                {_BY_PATH} JOIN runs r ON r.id = f.run_id
                WHERE i.path = ? AND i.long_name = ?)
                SELECT run_id, commit_sha, kind, created_at, ccn, cov, flag, crap
                FROM history WHERE twin = ? AND copy = 1 ORDER BY run_id""",
            (path, name, ordinal))
        flags = self._codes["flags"].names
        return [{"run_id": rid, "commit": sha, "kind": kind, "created_at": ts,
                 "ccn": ccn, "cov": cov, "flag": _name(flags, flag), "crap": crap}
                for rid, sha, kind, ts, ccn, cov, flag, crap in cur]

    def override_run_ids(self) -> set[int]:
        """Runs an override record names. Deleting one deletes an audit row."""
        return {rid for (rid,) in self._conn.execute("SELECT DISTINCT run_id FROM overrides")}

    def _doomed_ids(self, keep_ids: set[int], observed_ids: set[int] | None) -> list[tuple]:
        cur = self._conn.execute("SELECT id FROM runs ORDER BY id")
        return [(rid,) for (rid,) in cur if rid not in keep_ids
                and (observed_ids is None or rid in observed_ids)]

    def prune_runs(self, keep_ids: set[int], *, observed_ids: set[int] | None = None) -> int:
        """Delete observed runs outside keep_ids, rows and metadata together.

        Whole runs, never rows within a run: a run row that outlives its
        functions reads as a real run that scored zero, which is how a prune
        turns a silent digest into a false alarm and a trend into fiction.

        The cached rollup goes in the same transaction, and for the same
        reason: a rollup row that outlives its run keeps answering for it, so
        run_totals would still hand out totals for a run the store no longer
        holds. Ids come from AUTOINCREMENT and are never handed out twice, so
        nothing else would ever overwrite the row.
        """
        # A concurrent writer may add a run after the caller selected retention.
        # Only runs that selection observed can be candidates for deletion.
        doomed = self._doomed_ids(keep_ids, observed_ids)
        with self._conn:
            self._conn.executemany("DELETE FROM functions WHERE run_id = ?", doomed)
            self._conn.executemany("DELETE FROM run_rollup WHERE run_id = ?", doomed)
            self._conn.executemany("DELETE FROM runs WHERE id = ?", doomed)
        return len(doomed)

    def vacuum(self) -> None:
        """Hand the freed pages back to the OS. A DELETE alone frees none of
        them: it moves the pages to the freelist and the file never shrinks."""
        self._conn.commit()  # VACUUM cannot run inside a transaction
        self._conn.execute("VACUUM")


def default_baseline(store: SnapshotStore) -> dict | None:
    """The newest TRUSTED scored run: the state every reader describes.

    Trusted = a coverage run, or a verify run whose verdict passed. A failed
    verify must never become the next baseline (rerunning verify on a broken
    tree would launder its own failures), a partial run measures a fraction of
    the suite and reports a CRAP inflated to match, and hook-override anchor
    runs carry no scored rows at all.

    Named for verify, but `worklist` and `next-item` read it too, and that is
    the point: a view that ranks off a run verify refuses hands out an order no
    other command agrees with. `pick_baseline` adds the taint rule on top, which
    is verify's alone — a view compares nothing, so it has nothing to launder.
    """
    eligible = trusted_runs(store)
    return eligible[-1] if eligible else None


class BaselinePick(NamedTuple):
    """What `verify` measures against by default, and what the taint rule refused.

    `skipped` and `blocker` are both None on the ordinary path. When they are
    not, they are the message: the newest trusted run the rule passed over, and
    the failed verify it passed it over for.
    """
    run: dict | None
    skipped: dict | None
    blocker: dict | None


def _verify_blocker(run: dict, previous: dict | None) -> dict | None:
    """Only a completed verify changes the outstanding failure."""
    if run["kind"] != "verify" or run["verdict_ok"] is None:
        return previous
    return None if run["verdict_ok"] else run


def pick_baseline(runs: list[dict]) -> BaselinePick:
    """The newest trusted run no unanswered failed verify stands in front of.

    One chronological walk remembers the newest clean candidate and the newest
    refused one. A crashed verify changes neither trust nor the outstanding
    failure. An explicit `verify --baseline ID` still skips this decision.
    """
    picked = BaselinePick(None, None, None)
    blocker = None
    for run in runs:
        blocker = _verify_blocker(run, blocker)
        if is_trusted(run):
            picked = (BaselinePick(picked.run, run, blocker) if blocker
                      else BaselinePick(run, None, None))
    return picked


def is_trusted(r: dict) -> bool:
    """Trusted = a full coverage run, or a verify run whose verdict passed.

    `kind` decides it, not lane provenance. A repo whose every scope declares
    `coverage_optional` scores with no lanes at all, and reading the empty
    provenance as "nothing was measured" left it with a coverage run no
    baseline reader would accept — worklist, next-item, rescore, ratchet seed
    and verify all reported there was no scored run right after one.

    `legacy` is the exception that keeps the old test: those rows were migrated
    from before the column existed, so one label covers their inventory runs and
    their coverage runs alike and only provenance tells the two apart.
    """
    if r["kind"] == "hook":
        return False
    if r["kind"] == "coverage":
        return True
    if r["kind"] in ("legacy", None):
        return bool(r["lanes"])
    return r["kind"] == "verify" and r["verdict_ok"] is True


def trusted_runs(store: SnapshotStore) -> list[dict]:
    return [r for r in store.list_runs() if is_trusted(r)]


def is_rowful(r: dict) -> bool:
    """Does this run carry scored rows? Hook-override runs carry none.

    Rowfulness is not trust and never stands in for it. It answers "is there
    anything here to read", which `duplication` and the `explain` context ask
    because they describe whatever the store last measured, and which `worklist`
    asks only as the fallback for a repo with no trusted run yet.
    """
    return r["kind"] != "hook"


def rowful_runs(store: SnapshotStore) -> list[dict]:
    """Every run with rows, oldest first, trusted or not."""
    return [r for r in store.list_runs() if is_rowful(r)]


def _digest_pair_ids(trusted: list[dict]) -> set[int]:
    """Both halves of the pair `crapkit digest` compares.

    Losing either half is the loudest way a prune can go wrong: the digest
    would read the surviving run as a codebase that appeared from nothing and
    alert every over-target function in the repo as new.
    """
    from .digest import latest_comparable_pair

    pair = latest_comparable_pair(trusted)
    return {r["id"] for r in pair} if pair else set()


def _passing_verify_ids(runs: list[dict]) -> set[int]:
    """Every run `verify --baseline ID` can still legitimately name."""
    return {r["id"] for r in runs if r["kind"] == "verify" and r["verdict_ok"] is True}


def _newest_non_hook_id(runs: list[dict]) -> set[int]:
    """`duplication` and the `explain` context read the newest rowful run,
    trusted or not, and so does worklist in a repo with no trusted run at all.
    Prune it and those three describe a state older than the store holds."""
    ids = [r["id"] for r in runs if is_rowful(r)]
    return {ids[-1]} if ids else set()


def _baseline_keep_ids(runs: list[dict]) -> set[int]:
    """Keep the chosen comparison point and every failure it still answers.

    Failures after the newest coverage matter to the next run too. Retaining
    their records preserves both verify's blocker and ratchet's refusal list.
    """
    pick = pick_baseline(runs)
    selected = {r["id"] for r in filter(None, pick)}
    cutoff = pick.run["id"] if pick.run else 0
    return selected | {r["id"] for r in runs if r["id"] > cutoff
                       and _verify_blocker(r, None) is not None}


def prune_keep_set(runs: list[dict], override_run_ids, *, keep: int,
                   identity_run_ids=()) -> set[int]:
    """The runs a prune may never delete.

    Retention counts trusted runs, but recency alone is not the contract: a
    prune that keeps N and nothing else re-arms the digest, drops a baseline
    someone can still name, and orphans an override record whose audit trail
    joins through the run row. Legacy key migration also needs a surviving
    witness for each historical same-line collision group.
    """
    if keep < 1:
        raise ValueError(f"keep must be >= 1, got {keep}")
    trusted = [r for r in runs if is_trusted(r)]
    return ({r["id"] for r in trusted[-keep:]}
            | _digest_pair_ids(trusted) | _passing_verify_ids(runs)
            | _newest_non_hook_id(runs) | _baseline_keep_ids(runs)
            | set(override_run_ids) | set(identity_run_ids))
