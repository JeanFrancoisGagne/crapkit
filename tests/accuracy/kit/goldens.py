"""Goldens, the lock over every expected-value file, and declared changes.

A golden is crapkit's own output on a corpus with the volatile values replaced
(kit.surfaces.normalize): it is not an independent method, and a test that
compares to one carries the `golden` marker. What a golden adds is that no
printed value moves unnoticed.

The lock is a TSV of (path, sha256, change): one row per locked file, naming
the CHANGES row that last changed it. A file whose bytes differ from its lock
row changed with no declared change, and check() says so with the command that
declares it. declare() is the only writer: it appends a CHANGES row and relocks
every file that moved under that row's id. against_base() compares a lock with
the lock at a base commit, so a lock row edited by hand under an old id fails.

The CHANGES table has one row per declared change: id, date, kind (fix,
definition, feature or none), calcs, analysis_version, lizard_version, the
CHANGELOG anchor and a reason.

The change-control packet runs these functions over every packet's goldens and
expected-value files, and its declare command adds the oracle judgement. The
kit's seed goldens carry their own small lock and CHANGES table under
kit/fixtures, because they guard the kit's own self-test.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
from pathlib import Path

from . import surfaces

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "accuracy" / "kit" / "fixtures"
SEED_GOLDENS = FIXTURES / "seed-goldens"
SEED_LOCK = FIXTURES / "seed-goldens.lock"
SEED_CHANGES = FIXTURES / "seed-changes.tsv"
SEED_PATTERNS = ("tests/accuracy/kit/fixtures/seed-goldens/*",)
LOCK_COLUMNS = ("path", "sha256", "change")
CHANGE_COLUMNS = ("id", "date", "kind", "calcs", "analysis_version", "lizard_version",
                  "changelog", "reason")
CHANGE_KINDS = ("fix", "definition", "feature", "none")
FIX = ('declare it with `python tools/accuracy/change_control.py declare <id> --kind '
       '<fix|definition|feature|none> --calcs "<calc>,..."`, or undo the change')
_JSON = (".json", ".sarif")


class ChangeControlError(ValueError):
    """A CHANGES table or a declare call that breaks the change-control rules."""


# --- goldens --------------------------------------------------------------------

def _volatile(run) -> surfaces.Volatile:
    """The run's workspace and the versions its doctor output reports."""
    versions = json.loads(run.output("doctor.json"))["versions"]
    wanted = {name: versions[name] for name in ("crapkit", "python")}
    return surfaces.Volatile(roots=surfaces.spellings(run.root.parent), versions=wanted)


def _normal(name: str, text: str, volatile: surfaces.Volatile) -> str:
    if name.endswith(_JSON):
        value = surfaces.normalize(json.loads(text), volatile)
        return json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    return volatile.text(text)


def goldens_of(run) -> dict[str, str]:
    """{output name: normalized text} for every file a corpus run wrote; an
    empty stderr file is left out."""
    volatile = _volatile(run)
    names = sorted(path.name for path in run.outputs.iterdir() if path.stat().st_size)
    return {name: _normal(name, run.output(name), volatile) for name in names}


def write(directory: Path, goldens: Mapping[str, str]) -> None:
    """Make `directory` hold exactly these goldens, byte for byte."""
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.iterdir():
        if stale.name not in goldens:
            stale.unlink()
    for name, text in goldens.items():
        (directory / name).write_bytes(text.encode("utf-8"))


def _first_difference(name: str, golden: str, now: str) -> str:
    old, new = golden.split("\n"), now.split("\n")
    number = next(n for n, pair in enumerate(zip(old + [""], new + [""]), 1)
                  if pair[0] != pair[1])
    got = lambda rows: rows[number - 1] if number <= len(rows) else "<end>"  # noqa: E731
    return f"{name}: line {number} differs\n  golden: {got(old)}\n  now:    {got(new)}"


def _stored(directory: Path) -> dict[str, str]:
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes().decode("utf-8") for path in directory.iterdir()}


def _mismatch(name: str, stored: Mapping[str, str], goldens: Mapping[str, str]) -> str | None:
    if name not in goldens:
        return f"{name}: a golden with no output behind it"
    if name not in stored:
        return f"{name}: an output with no golden"
    if stored[name] != goldens[name]:
        return _first_difference(name, stored[name], goldens[name])
    return None


def compare(directory: Path, goldens: Mapping[str, str]) -> list[str]:
    """One line per golden that is missing, extra, or differs from the output."""
    stored = _stored(directory)
    found = (_mismatch(name, stored, goldens) for name in sorted({*stored, *goldens}))
    return [problem for problem in found if problem]


# --- the lock and the CHANGES table -------------------------------------------------

def _rows(path: Path, columns: tuple[str, ...]) -> list[dict]:
    if not path.is_file():
        return []
    lines = [line for line in path.read_bytes().decode("utf-8").split("\n") if line]
    return [dict(zip(columns, line.split("\t"))) for line in lines[1:]]


def _table(path: Path, columns: tuple[str, ...], rows: Iterable[Iterable[str]]) -> None:
    lines = ["\t".join(columns)] + ["\t".join(row) for row in rows]
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def read_lock(path: Path) -> dict[str, tuple[str, str]]:
    """{path: (sha256, change id)}. A missing lock holds nothing."""
    return {row["path"]: (row["sha256"], row["change"]) for row in _rows(path, LOCK_COLUMNS)}


def write_lock(path: Path, rows: Mapping[str, tuple[str, str]]) -> None:
    _table(path, LOCK_COLUMNS, ((name, *rows[name]) for name in sorted(rows)))


def _checked_change(row: dict, seen: dict) -> dict:
    if row["id"] in seen:
        raise ChangeControlError(f"CHANGES: {row['id']} appears twice")
    if row.get("kind") not in CHANGE_KINDS:
        raise ChangeControlError(f"CHANGES {row['id']}: kind {row.get('kind')!r} is not one "
                                 f"of {', '.join(CHANGE_KINDS)}")
    return row


def read_changes(path: Path) -> dict[str, dict]:
    """{id: row} from a CHANGES table, refusing a repeated id or an unknown kind."""
    seen: dict[str, dict] = {}
    for row in _rows(path, CHANGE_COLUMNS):
        seen[row["id"]] = _checked_change(row, seen)
    return seen


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan(base: Path, patterns: Iterable[str]) -> dict[str, str]:
    """{posix path under base: sha256} for every file a pattern matches."""
    found = {path for pattern in patterns for path in base.glob(pattern) if path.is_file()}
    return {path.relative_to(base).as_posix(): _sha256(path) for path in sorted(found)}


def _lock_problem(path: str, locked, now, changes: Mapping) -> str | None:
    if locked is None:
        return f"{path} is not in the lock: {FIX}"
    if now is None:
        return f"{path} is locked but gone: {FIX}"
    if locked[0] != now:
        return (f"{path} changed with no declared change "
                f"(lock {locked[0][:12]}, now {now[:12]}): {FIX}")
    if locked[1] not in changes:
        return f"{path} names change {locked[1]}, which no CHANGES row declares"
    return None


def check(lock_path: Path, base: Path, patterns: Iterable[str],
          changes: Mapping[str, dict]) -> list[str]:
    """Every locked or lockable file whose bytes, presence or change id breaks the lock."""
    lock, now = read_lock(lock_path), scan(base, patterns)
    found = (_lock_problem(path, lock.get(path), now.get(path), changes)
             for path in sorted({*lock, *now}))
    return [problem for problem in found if problem]


def _new_change(change: Mapping[str, str], changes: Mapping[str, dict]) -> dict:
    row = {column: str(change.get(column, "")) for column in CHANGE_COLUMNS}
    if row["id"] in changes:
        raise ChangeControlError(f"{row['id']} is already declared; a change takes a new id")
    return _checked_change(row, changes)


def _moved(lock: Mapping, now: Mapping) -> list[str]:
    """Paths whose bytes differ from their lock row, including new and gone ones."""
    return sorted(path for path in {*lock, *now} if lock.get(path, ("", ""))[0] != now.get(path))


def _relocked(lock: Mapping, now: Mapping, moved: list[str], change_id: str) -> dict:
    rows = {path: lock[path] for path in now if path not in moved}
    rows.update({path: (now[path], change_id) for path in moved if path in now})
    return rows


def _append_change(changes_path: Path, changes: Mapping[str, dict], row: dict) -> None:
    rows = [*changes.values(), row]
    _table(changes_path, CHANGE_COLUMNS, [[old[column] for column in CHANGE_COLUMNS]
                                          for old in rows])


def declare(lock_path: Path, base: Path, patterns: Iterable[str], change: Mapping[str, str],
            changes_path: Path) -> list[str]:
    """Record `change` in the CHANGES table and relock every file that moved
    under its id. Returns the moved paths; refuses when nothing moved."""
    changes = read_changes(changes_path)
    row = _new_change(change, changes)
    lock, now = read_lock(lock_path), scan(base, patterns)
    moved = _moved(lock, now)
    if not moved:
        raise ChangeControlError("nothing moved since the lock: there is no change to declare")
    _append_change(changes_path, changes, row)
    write_lock(lock_path, _relocked(lock, now, moved, row["id"]))
    return moved


def _base_problem(path: str, old, new, fresh: set) -> str | None:
    if old == new or new is None:
        return None
    if new[1] not in fresh:
        return f"{path} was relocked under {new[1]}, a change the base already had: {FIX}"
    return None


def _gone_problem(base_lock: Mapping, lock: Mapping, fresh: set) -> list[str]:
    gone = sorted(set(base_lock) - set(lock))
    if gone and not fresh:
        return [f"{gone[0]} left the lock with no new change: {FIX}"]
    return []


def against_base(base_lock: Mapping, lock: Mapping, base_changes: Mapping,
                 changes: Mapping) -> list[str]:
    """Each lock row that moved since the base must name a change the base lacks,
    and a row the base had that is gone needs some new change."""
    fresh = set(changes) - set(base_changes)
    found = [_base_problem(path, base_lock.get(path), lock[path], fresh) for path in sorted(lock)]
    return [problem for problem in found if problem] + _gone_problem(base_lock, lock, fresh)
