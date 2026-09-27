"""What a crapkit repo keeps on disk, taken before and after an upgrade.

A cell asserts through the commands a user runs (`crapkit runs --json`,
`crapkit claims --json`, `crapkit overrides --json`, the verify line). The
manifest is the diagnosis beside them: every file crapkit keeps and its hash,
the store's schema and row counts, the runs, open claims and overrides it
holds, and the marks file's stamp lines. It goes into the transcript, and it
fails a cell for one thing only, data loss: a durable table with fewer rows,
or a run, an open claim or an override that was there before and is gone.

    before = state_manifest.take(repo)
    ... upgrade ...
    state_manifest.check(box, before, state_manifest.take(repo))

The store is read from a copy, so taking a manifest writes nothing into the
repo (no -wal or -shm file, no schema touch).
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

STORE = ".crapkit/crap.sqlite"
ADOPTION = ("crapkit.toml", "crapkit-ratchet.tsv", ".gitignore")
# Tables whose rows are history a user keeps: an upgrade may add rows, never drop them.
DURABLE = ("runs", "attempts", "overrides", "functions", "identities")
RUNS = "select id from runs order by id"
OPEN_CLAIMS = "select path, long_name from attempts where closed_at is null order by id"
OVERRIDES = "select path, long_name, reason from overrides order by created_at, path, long_name"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _kept_files(repo: Path) -> list[Path]:
    adoption = [repo / name for name in ADOPTION]
    return [path for path in [*adoption, *sorted((repo / ".crapkit").rglob("*"))] if path.is_file()]


def files(repo: Path) -> dict[str, str]:
    """relative path -> sha256 for the adoption files and everything under .crapkit/."""
    return {path.relative_to(repo).as_posix(): _sha256(path) for path in _kept_files(repo)}


def stamp(repo: Path) -> list[str]:
    """The `# ` lines that open crapkit-ratchet.tsv: the metric and key stamps."""
    marks = repo / "crapkit-ratchet.tsv"
    if not marks.is_file():
        return []
    lines = marks.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines[:3] if line.startswith("#")]


def _rows(db: sqlite3.Connection, sql: str) -> list[list]:
    """The query's rows, or none when an older schema lacks the table."""
    try:
        return [list(row) for row in db.execute(sql)]
    except sqlite3.OperationalError:
        return []


def _schema(db: sqlite3.Connection) -> dict[str, list[str]]:
    names = [row[0] for row in db.execute("select name from sqlite_master where type = 'table' order by name")]
    return {name: [column[1] for column in db.execute(f'pragma table_info("{name}")')] for name in names}


def _read(db: sqlite3.Connection) -> dict:
    schema = _schema(db)
    counts = {name: db.execute(f'select count(*) from "{name}"').fetchone()[0] for name in schema}
    return {"schema": schema, "counts": counts, "runs": [row[0] for row in _rows(db, RUNS)],
            "claims": _rows(db, OPEN_CLAIMS), "overrides": _rows(db, OVERRIDES)}


def store(repo: Path) -> dict:
    """The store's schema, row counts, run ids, open claims and overrides,
    read from a copy of the database and its -wal file."""
    source = repo / STORE
    if not source.is_file():
        return {}
    with tempfile.TemporaryDirectory() as scratch:
        for suffix in ("", "-wal"):
            if Path(f"{source}{suffix}").is_file():
                shutil.copyfile(f"{source}{suffix}", Path(scratch) / f"crap.sqlite{suffix}")
        with closing(sqlite3.connect(Path(scratch) / "crap.sqlite")) as db:
            return _read(db)


def take(repo: Path) -> dict:
    repo = Path(repo)
    return {"files": files(repo), "stamp": stamp(repo), "store": store(repo)}


# --- data loss --------------------------------------------------------------------

def _fewer_rows(before: dict, after: dict) -> list[str]:
    was, now = before.get("counts", {}), after.get("counts", {})
    return [f"table {name}: {was[name]} row(s) before, {now.get(name, 0)} after"
            for name in DURABLE if name in was and now.get(name, 0) < was[name]]


def _hashable(item):
    return tuple(item) if isinstance(item, list) else item


def _gone(before: dict, after: dict, key: str) -> list[str]:
    present = {_hashable(item) for item in after.get(key, [])}
    return [f"{key}: {item} is gone" for item in before.get(key, []) if _hashable(item) not in present]


def losses(before: dict, after: dict) -> list[str]:
    """Every sign that rows a user keeps were lost between two manifests."""
    was, now = before.get("store", {}), after.get("store", {})
    found = _fewer_rows(was, now)
    for key in ("runs", "claims", "overrides"):
        found += _gone(was, now, key)
    return found


def check(box, before: dict, after: dict, label: str = "upgrade") -> None:
    """Attach both manifests to the transcript; fail on data loss only."""
    box.transcript.attach(f"state-manifest-before-{label}", before)
    box.transcript.attach(f"state-manifest-after-{label}", after)
    lost = losses(before, after)
    if lost:
        raise AssertionError(f"{label} lost data:\n  " + "\n  ".join(lost) + f"\n{box.transcript.text()}")
