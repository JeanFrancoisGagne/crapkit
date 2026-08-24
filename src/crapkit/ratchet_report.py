"""Burn-down analytics from the ratchet file's own git history. Pure.

No timestamps live in the TSV; the commits that changed it carry them, so a
fixed history reports deterministically. Ages and velocity anchor on the
NEWEST commit in the history, never the wall clock.
"""
from __future__ import annotations

DAY = 86400


def _mark_line(line: str) -> tuple | None:
    """((path, long_name), crap) when a +/- patch line is a mark row; else None."""
    body = line[1:]
    if body.startswith(("++ ", "-- ", "path\t")):
        return None
    parts = body.split("\t")
    if len(parts) != 3:
        return None
    try:
        return (parts[0], parts[1]), float(parts[2])
    except ValueError:
        return None


def _commit_delta(patch: str) -> tuple[dict, dict]:
    """The marks one commit's patch added and removed, keyed."""
    added: dict = {}
    removed: dict = {}
    for line in patch.splitlines():
        if not line or line[0] not in "+-":
            continue
        mark = _mark_line(line)
        if mark is None:
            continue
        (added if line[0] == "+" else removed)[mark[0]] = mark[1]
    return added, removed


def mark_events(patches: list[tuple[int, str]]) -> list[tuple]:
    """(ts, key, 'added'|'dropped', crap) per commit. A key removed and re-added
    in the same commit is a tightening and emits nothing — only entry and
    repayment count."""
    events = []
    for ts, patch in patches:
        added, removed = _commit_delta(patch)
        for key in sorted(set(added) - set(removed)):
            events.append((ts, key, "added", added[key]))
        for key in sorted(set(removed) - set(added)):
            events.append((ts, key, "dropped", removed[key]))
    return events


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
    and the timestamp of every repayment."""
    entered: dict = {}
    crap: dict = {}
    dropped: list[int] = []
    for ts, key, kind, value in events:
        if kind == "added":
            entered[key] = ts
            crap[key] = value
        else:
            entered.pop(key, None)
            crap.pop(key, None)
            dropped.append(ts)
    return entered, crap, dropped


def _open_marks(entered: dict, working: dict | None, anchor: int) -> dict:
    """Open marks keyed to when they entered. The working tree says WHICH marks
    are open, history says how old each one is; a mark with no commit behind it
    entered at the anchor and reports 0d."""
    if working is None:
        return entered
    return {key: entered.get(key, anchor) for key in working}


def _uncommitted(crap: dict, working: dict | None) -> int:
    """Marks the working tree and the newest committed version disagree on:
    added, repaid or tightened on disk and not committed yet."""
    if working is None:
        return 0
    return sum(1 for key in set(crap) | set(working) if crap.get(key) != working.get(key))


def _age_rows(opened: dict, anchor: int) -> list[dict]:
    return sorted(({"path": k[0], "long_name": k[1], "age_days": (anchor - ts) // DAY}
                   for k, ts in opened.items()),
                  key=lambda e: (-e["age_days"], e["path"], e["long_name"]))


def report_from_events(events: list[tuple], working: dict | None = None) -> dict:
    """`working` is the marks on disk, keyed (path, long_name) -> crap. It decides
    which marks are open, because a seed that has not been committed is still debt
    somebody owes. Repayment counts and velocity stay on committed history, which
    is the only place a timestamp exists. None reports the committed state alone.
    """
    entered, crap, dropped = _replay(events)
    anchor = max((ts for ts, *_ in events), default=0)
    opened = _open_marks(entered, working, anchor)
    return {"open": len(opened), "dropped_total": len(dropped), "anchor_ts": anchor,
            "uncommitted": _uncommitted(crap, working),
            "oldest": _age_rows(opened, anchor)[:20], **_drop_velocity(dropped, anchor)}
