"""Burn-down analytics from the ratchet file's own git history. Pure.

No timestamps live in the TSV; the commits that changed it carry them, so a
fixed history reports deterministically. Ages and velocity anchor on the
NEWEST commit in the history, never the wall clock.
"""
from __future__ import annotations

from .keys import MarkIndex
from .marks_history import MarksRevision, revision_moves

DAY = 86400


def crap_by_key(entries) -> dict:
    """A marks file's entries as key -> crap, the first mark under a key
    answering, as `keys.MarkIndex` does for every other reader."""
    index = MarkIndex(entries)
    return {key: index.mark(key) for key in index.keys()}


def mark_events(revisions: list) -> list[tuple]:
    """Committed additions, updates, moves and repayments, plus the clock.

    `revisions` are the marks file's parsed revisions, oldest first
    (marks_history.marks_history). Each one diffs the marks it found against
    the marks it left: a key it adds enters, a changed value is an update that
    keeps the entry date, a mark marks_history.revision_moves pairs is one
    "moved" event keyed (old key, new key) at its new value, and any other key
    it removes is repaid. A revision with no mark change still advances the
    clock through an observed event with no key.
    """
    return [event for revision in revisions
            for event in _commit_events(revision.time, *_delta(
                revision.before or {}, revision.marks or {}, revision.changed),
                _pairs(revision))]


def _pairs(revision: MarksRevision) -> dict:
    """old key -> new key for each mark the revision moved."""
    return {move.old_key: move.new_key for move in revision_moves(revision)}


def held_event(revisions: list) -> list[tuple]:
    """The newest revision's marks as one "held" event, the committed marks;
    [] when it holds none. A replay cannot give them: a merge shows no change,
    so a mark a conflicted merge repaid replays as open."""
    newest = revisions[-1].marks if revisions else None
    return [] if newest is None else [(revisions[-1].time, None, "held", newest)]


def _delta(before: dict, after: dict, keys: frozenset | None) -> tuple[dict, dict]:
    """The marks one revision added and removed against the marks it found,
    keyed; a changed value is in both. `keys` is MarksRevision.changed: when
    the reader narrowed it, every other key holds one mark in both, so a large
    file costs the keys a revision changed."""
    return _left(after, before, keys), _left(before, after, keys)


def _left(side: dict, other: dict, keys: frozenset | None) -> dict:
    """The marks `side` holds that `other` does not hold at that value, among
    `keys`, or among every key `side` holds when `keys` is None."""
    held = side if keys is None else {key: side[key] for key in keys & side.keys()}
    return {key: crap for key, crap in held.items() if other.get(key) != crap}


def _commit_events(ts: int, added: dict, removed: dict, moved: dict) -> list[tuple]:
    """One revision's events; `moved` maps each moved mark's old key to its
    new key, so neither key is an add or a repayment."""
    arrived = set(moved.values())
    events = [(ts, key, "updated" if key in removed else "added", added[key])
              for key in sorted(added.keys() - arrived)]
    events += [(ts, pair, "moved", added[pair[1]]) for pair in sorted(moved.items())]
    events += [(ts, key, "dropped", removed[key])
               for key in sorted(removed.keys() - added.keys() - moved.keys())]
    return events or [(ts, None, "observed", 0.0)]


def _drop_velocity(dropped: list[int], anchor: int) -> dict:
    return {"dropped_last_30d": sum(1 for ts in dropped if anchor - ts <= 30 * DAY),
            "dropped_last_90d": sum(1 for ts in dropped if anchor - ts <= 90 * DAY)}


def _expired_marks(report: dict, max_age_months: int | None) -> list[str]:
    if max_age_months is None:
        return []
    limit = max_age_months * 30
    # "oldest" is age-sorted, so any expired mark appears in it; a backlog of
    # more than its cap still reports the worst offenders
    return [f"mark {e['long_name']} in {e['path']} is {e['age_days']}d old (limit {limit}d)"
            for e in report["oldest"] if e["age_days"] > limit]


def _stalled_repayment(report: dict, min_repaid_30d: int | None) -> list[str]:
    if min_repaid_30d is None or not report["open"]:
        return []
    if report["dropped_last_30d"] >= min_repaid_30d:
        return []
    return [f"repayment stalled: {report['dropped_last_30d']} mark(s) repaid in 30d "
            f"(policy wants {min_repaid_30d})"]


def policy_violations(report: dict, max_age_months: int | None,
                      min_repaid_30d: int | None) -> list[str]:
    """Findings against the debt policy knobs; no knobs, no findings."""
    return _expired_marks(report, max_age_months) + _stalled_repayment(report, min_repaid_30d)


def _replay(events: list[tuple]) -> tuple[dict, dict, list[int]]:
    """The committed state: when each surviving mark entered, what it is worth,
    and the timestamp of every repayment. A moved mark takes its entry date to
    its new key and repays nothing. Dropping a mark that is not open repays
    nothing: `git log -p` prints no patch for a merge, so a mark both sides of
    one repaid shows as two drops."""
    entered: dict = {}
    crap: dict = {}
    dropped: list[int] = []
    for ts, key, kind, value in events:
        if kind == "moved":
            key = _carried(entered, crap, key)
        if kind in ("added", "updated", "moved"):
            entered.setdefault(key, ts)
            crap[key] = value
        elif kind == "dropped" and entered.pop(key, None) is not None:
            crap.pop(key)
            dropped.append(ts)
    return entered, crap, dropped


def _carried(entered: dict, crap: dict, pair: tuple) -> tuple:
    """The new key of a moved mark, which takes the old key's entry date; a
    mark the replay does not hold open enters at the move."""
    old, new = pair
    crap.pop(old, None)
    if old in entered:
        entered[new] = entered.pop(old)
    return new


def _open_marks(entered: dict, marks: dict, anchor: int, moved: dict) -> dict:
    """Open marks keyed to when they entered. `marks` say WHICH marks are open,
    history says how old each one is, and a mark the working tree moved reads
    the entry date of the committed key it left (`moved`, new key -> old
    key). A mark with no commit behind it entered at the anchor: 0d."""
    return {key: entered.get(moved.get(key, key), anchor) for key in marks}


def _committed(events: list[tuple], replayed: dict) -> dict:
    """The committed marks: the newest revision a "held" event read, else the
    replay's own state. A replay cannot say which marks a merge kept, since a
    merge shows no change."""
    return next((value for _, _, kind, value in reversed(events) if kind == "held"), replayed)


def mark_age_days(events: list[tuple], key: tuple) -> int | None:
    """One surviving mark's age, using the report's committed history rules."""
    entered, _, _ = _replay(events)
    since = entered.get(key)
    anchor = max((ts for ts, *_ in events), default=0)
    return None if since is None else (anchor - since) // DAY


def _uncommitted(committed: dict, working: dict | None, moved: dict) -> int:
    """Marks the working tree and the newest committed version disagree on:
    added, repaid, tightened or moved on disk and not committed yet. A moved
    mark (`moved`, new key -> old key) counts once."""
    if working is None:
        return 0
    differ = sum(1 for key in committed.keys() | working.keys()
                 if committed.get(key) != working.get(key))
    return differ - len(moved)


def _working_moves(committed: dict, working: dict | None) -> dict:
    """new key -> old key for each mark the working tree moved away from the
    newest committed marks, paired as a commit's moves are."""
    if working is None:
        return {}
    moves = revision_moves(MarksRevision("", 0, working, committed))
    return {move.new_key: move.old_key for move in moves}


def _age_rows(opened: dict, anchor: int) -> list[dict]:
    return sorted(({"path": k[0], "long_name": k[1], "age_days": (anchor - ts) // DAY}
                   for k, ts in opened.items()),
                  key=lambda e: (-e["age_days"], e["path"], e["long_name"]))


def report_from_events(events: list[tuple], working: dict | None = None) -> dict:
    """`working` is the marks on disk, keyed (path, long_name) -> crap. It decides
    which marks are open, because a seed that has not been committed is still debt
    somebody owes, and a mark it moved away from a committed key keeps that key's
    age. Repayment counts and velocity stay on committed history, which is the
    only place a timestamp exists. None reports the committed state alone: the
    newest revision a "held" event carries, else the replay's own state.
    """
    entered, crap, dropped = _replay(events)
    committed = _committed(events, crap)
    anchor = max((ts for ts, *_ in events), default=0)
    moved = _working_moves(committed, working)
    opened = _open_marks(entered, committed if working is None else working, anchor, moved)
    return {"open": len(opened), "dropped_total": len(dropped), "anchor_ts": anchor,
            "uncommitted": _uncommitted(committed, working, moved),
            "oldest": _age_rows(opened, anchor)[:20], **_drop_velocity(dropped, anchor)}
