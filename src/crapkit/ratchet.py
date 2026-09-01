"""The committed ratchet: per-function high-water CRAP marks for functions above target.

Pure text <-> entries. Marks only ever tighten: an improvement lowers or drops
an entry on update; a regression NEVER raises one (it surfaces as a verdict
failure instead); brand-new debt enters only through the audited override.

Identity is (path, key name): spans drift with every edit, names survive. A key
name is the function's long_name, plus a `#N` ordinal when the file gives that
name to more than one function — see `keys`. The first twin keeps the bare name,
so a marks file written before the ordinal reads unchanged.

The file opens with a metric stamp comment naming the analysis version and the
lizard that produced the numbers. Marks measured under different rules are not
comparable, and without the stamp that reads as a clean run.
"""
from __future__ import annotations

from typing import NamedTuple

from .invocation import _self
from .score import ScoredRow

_HEADER = "path\tlong_name\tcrap"


class RatchetEntry(NamedTuple):
    path: str
    long_name: str
    crap: float


class TightenRefusal(NamedTuple):
    """One mark a bouncing measurement was not allowed to pull down."""
    path: str
    long_name: str
    previous: float
    fresh: float


def stamp_text(analysis_version: int, lizard_version: str) -> str:
    """The metric identity a set of marks was measured under, as one line."""
    return f"crapkit-analysis={analysis_version} lizard={lizard_version}"


def metric_version() -> str:
    # Imported here, not at module scope: the git merge driver runs this module
    # in a temp dir with no analysis stack, and it passes its own stamp through.
    import lizard

    from .analyze import ANALYSIS_VERSION
    return stamp_text(ANALYSIS_VERSION, lizard.version)


def read_stamp(text: str) -> str:
    """The metric a marks file was written under; "" for one written before stamping."""
    for line in text.splitlines():
        if line.startswith("#"):
            return line[1:].strip()
        if line.strip():
            return ""
    return ""


def stamp_conflict(recorded: str, current: str) -> str | None:
    """The refusal when marks and the running metric disagree; None when they compare.

    An unstamped file has nothing to disagree with — the caller warns instead.
    """
    if not recorded or recorded == current:
        return None
    return (f"ratchet marks were recorded under [{recorded}] but this run measures "
            f"[{current}] — CRAP scores are not comparable across metric versions; "
            f"re-baseline with `{_self()} ratchet seed`")


def _is_skippable(line: str) -> bool:
    """Blank lines, the header and comment lines (the metric stamp) carry no mark."""
    return not line.strip() or line == _HEADER or line.startswith("#")


def read_ratchet(text: str) -> tuple[list[RatchetEntry], list[str]]:
    """The marks, and one complaint per line that carries none.

    Split out from `load_ratchet` so a READ-ONLY caller can salvage the file. A
    mark is one optional field of what `explain` and `brief` answer, so a single
    hand-edited line used to cost the whole answer: a ValueError traceback where
    a trajectory, a source listing and a churn count were all available. Two
    shapes reach here, and a hand edit or a botched merge produces both: too few
    fields, and three fields whose mark is empty or is not a number. Dropping a
    line can only take a ceiling away from a gate, never raise one, so the read
    side is safe to salvage. The write side is not, and keeps `load_ratchet`.
    """
    entries, complaints = [], []
    for i, line in enumerate(text.splitlines()):
        if _is_skippable(line):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            complaints.append(f"ratchet line {i + 1} has {len(parts)} fields, "
                              f"expected 3: {line!r}")
            continue
        try:
            entries.append(RatchetEntry(parts[0], parts[1], float(parts[2])))
        except ValueError:
            complaints.append(f"ratchet line {i + 1} has an unreadable mark: {line!r}")
    return entries, complaints


def load_ratchet(text: str) -> list[RatchetEntry]:
    """Every mark, refusing a file that holds a line carrying none. What every
    caller that REWRITES the file reads with: a line skipped there would delete
    a mark the repo signed for."""
    entries, complaints = read_ratchet(text)
    if complaints:
        raise ValueError(complaints[0])
    return entries


def mark_for(entries: list[RatchetEntry], path: str, long_name: str) -> float | None:
    """One function's recorded high-water mark, or None when it carries no mark.

    `long_name` is the KEY name: bare for a name only one function in the file
    holds, `name#2` for the second function holding it. `keys.key_names` builds
    it from the rows; passing a raw long_name for a twin asks about twin #1.
    """
    for e in entries:
        if e.path == path and e.long_name == long_name:
            return e.crap
    return None


def dump_ratchet(entries: list[RatchetEntry], *, stamp: str | None = None) -> str:
    """`stamp` None takes the running metric; a version string is written verbatim
    and "" writes none, which is how the merge driver keeps two legacy sides legacy."""
    version = metric_version() if stamp is None else stamp
    lines = [f"# {version}"] if version else []
    lines.append(_HEADER)
    for e in sorted(entries, key=lambda e: (e.path, e.long_name)):
        lines.append(f"{e.path}\t{e.long_name}\t{e.crap:.4f}")
    return "\n".join(lines) + "\n"


def _merge_key(b: float | None, o: float | None, t: float | None) -> float | None:
    """git 3-way semantics per key: the changed side wins over the unchanged one.
    Both changed: keep the mark (it can only fall; prune is re-runnable) at min."""
    if o == t:
        return o
    if o == b:
        return t
    if t == b:
        return o
    # both changed differently; both-None is impossible past the o == t check
    return min(x for x in (o, t) if x is not None)


def merge_ratchets(base: list[RatchetEntry], ours: list[RatchetEntry],
                   theirs: list[RatchetEntry]) -> list[RatchetEntry]:
    b = {(e.path, e.long_name): e.crap for e in base}
    o = {(e.path, e.long_name): e.crap for e in ours}
    t = {(e.path, e.long_name): e.crap for e in theirs}
    merged = []
    for key in sorted(set(b) | set(o) | set(t)):
        crap = _merge_key(b.get(key), o.get(key), t.get(key))
        if crap is not None:
            merged.append(RatchetEntry(key[0], key[1], crap))
    return merged


def seed_ratchet(prior: list[RatchetEntry], fresh: list[ScoredRow], *, target: int,
                 scope_targets: dict[str, int] | None = None) -> tuple[list[RatchetEntry], int, int]:
    """First-class mark entry: record every over-ceiling function at its current
    CRAP. A mark never rises, seeding included — an existing lower mark stays."""
    from .verify import rows_by_key

    ceilings = scope_targets or {}
    marks = {(e.path, e.long_name): e for e in prior}
    added = tightened = 0
    for key, row in rows_by_key(fresh).items():
        if row.crap <= ceilings.get(row.scope, target):
            continue
        a, t = _seed_mark(marks, key, row.crap)
        added += a
        tightened += t
    return sorted(marks.values(), key=lambda e: (e.path, e.long_name)), added, tightened


def _seed_mark(marks: dict, key: tuple, crap: float) -> tuple[int, int]:
    """Place one mark; returns (added, tightened) deltas. A mark never rises."""
    old = marks.get(key)
    mark = round(crap, 4)
    if old is not None and mark >= old.crap:
        return 0, 0
    marks[key] = RatchetEntry(key[0], key[1], mark)
    return (1, 0) if old is None else (0, 1)


def _repath(entries: list[RatchetEntry], dest_of) -> tuple[list[RatchetEntry], int]:
    """Rewrite paths by a per-mark chooser (None leaves a mark alone); values never move."""
    out = []
    moved = 0
    for e in entries:
        dest = dest_of(e)
        if dest is None:
            out.append(e)
            continue
        out.append(e._replace(path=dest))
        moved += 1
    return sorted(out, key=lambda e: (e.path, e.long_name)), moved


def _moved_path(path: str, old: str, new: str) -> str | None:
    """One mark's destination under an explicit move, or None when `old` misses it."""
    if old.endswith("/"):
        return f"{new.rstrip('/')}/{path[len(old):]}" if path.startswith(old) else None
    return new if path == old else None


def move_marks(entries: list[RatchetEntry], old: str,
               new: str) -> tuple[list[RatchetEntry], int]:
    """Re-path marks at their recorded values. A trailing "/" on `old` moves a whole
    directory; anything else matches one exact path."""
    return _repath(entries, lambda e: _moved_path(e.path, old, new))


def _rename_target(entry: RatchetEntry, present: set, renames: dict[str, str]) -> str | None:
    """Where a mark should follow a renamed file, or None to leave it alone.

    Three conditions, all required: the function is gone from its recorded path,
    git calls that path renamed, and the SAME key name exists at the new one. A
    copy fails the first (the source survives), so its mark never travels.
    """
    if (entry.path, entry.long_name) in present:
        return None
    dest = renames.get(entry.path)
    if dest is None or (dest, entry.long_name) not in present:
        return None
    return dest


def follow_renames(prior: list[RatchetEntry], fresh: list[ScoredRow],
                   renames: dict[str, str]) -> tuple[list[RatchetEntry], int]:
    """Marks for renamed files, re-pathed. Runs BEFORE prune so a rename reads as a
    move, not as code that left the repo and forfeits its high-water mark."""
    from .verify import rows_by_key

    present = set(rows_by_key(fresh))
    return _repath(prior, lambda e: _rename_target(e, present, renames))


def prune_ratchet(prior: list[RatchetEntry],
                  fresh: list[ScoredRow]) -> tuple[list[RatchetEntry], int]:
    """Deliberate mark exit: drop entries whose function is absent from the run.
    The automatic update keeps them (an exclude glob or a lane outage also removes
    rows); prune is the human confirming the code is really gone."""
    from .verify import rows_by_key

    present = set(rows_by_key(fresh))
    kept = [e for e in prior if (e.path, e.long_name) in present]
    return kept, len(prior) - len(kept)


def _jumped(previous: float, fresh: float, factor: float) -> bool:
    """True when two measurements of one commit differ by more than `factor`.

    Written as a multiplication, not a ratio: a division would have to special-case
    a zero, and the comparison is the same one either way.
    """
    low, high = sorted((previous, fresh))
    return high > low * factor


def unstable_marks(prior: list[RatchetEntry], fresh: list[ScoredRow],
                   previous: dict[tuple[str, str], float], *,
                   max_jump: float) -> list[TightenRefusal]:
    """Marks whose measurement moved too far between two runs of the SAME commit.

    A tighten claims the code improved. One commit measured twice cannot have
    improved, so a score that jumped past `max_jump` in either direction is
    reporting the measurement's own noise: the lucky half lowers the mark and
    the unlucky half then fails it, and the gate becomes a coin flip on an
    unchanged tree. Only marked functions can be tightened, so only they are
    walked; a key the earlier run never scored has nothing to disagree with.
    """
    from .verify import rows_by_key

    worst = rows_by_key(fresh)
    refusals = []
    for entry in prior:
        key = (entry.path, entry.long_name)
        row, was = worst.get(key), previous.get(key)
        if row is None or was is None or not _jumped(was, round(row.crap, 4), max_jump):
            continue
        refusals.append(TightenRefusal(entry.path, entry.long_name, was, round(row.crap, 4)))
    return refusals


def update_ratchet(prior: list[RatchetEntry], fresh: list[ScoredRow], *, target: int,
                   scope_targets: dict[str, int] | None = None,
                   hold: frozenset[tuple[str, str]] = frozenset()) -> list[RatchetEntry]:
    """`hold` names keys whose mark this run may not move — `unstable_marks`
    picks them. A held mark keeps its recorded value, drop included: leaving the
    file is the deepest tighten there is."""
    from .verify import rows_by_key

    fresh_by_key = rows_by_key(fresh)
    updated = []
    for entry in prior:
        row = fresh_by_key.get((entry.path, entry.long_name))
        if row is None or (entry.path, entry.long_name) in hold:
            # Two ways a mark passes through untouched. Absent from the scored
            # rows is NOT proof the code is gone — an exclude glob or a lane
            # outage also removes it, and dropping the entry would erase an
            # audited override's only diff-visible record; stale entries are
            # inert (verify checks only present functions). Held is a
            # measurement this run cannot vouch for.
            updated.append(entry)
            continue
        if row.crap <= (scope_targets or {}).get(row.scope, target):
            continue  # fixed for real: below the scope's ceiling needs no mark
        updated.append(RatchetEntry(entry.path, entry.long_name, min(entry.crap, round(row.crap, 4))))
    return sorted(updated, key=lambda e: (e.path, e.long_name))
