"""The burn-down queue and everything that reads it: `next-item` (the ranked
candidate an agent session takes, with its admission rules and empty-queue
reasons), `claims` (the loop state that keeps two sessions off one function),
`brief` (one function's whole start-editing packet, one at a time or a batch of
them) and `worklist` (the ranked risk queue and its batch split)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import packet
from ..churn_cache import load_churn
from ..errors import ConfigError, CrapkitError
from ..gitio import head_commit, ls_files
from ..keys import key_names, key_of, split_ordinal
from ..store import SnapshotStore
from ..uncovered import load_uncovered
from ..worklist import admission, build_worklist, sql_floor
from ._shared import (_latest_scored, _load_repo_config, _load_sources, _open_store,
                      _print_json, _ratchet_entries)


def _scored_store(root: Path) -> tuple[SnapshotStore, dict]:
    """The store and the run next-item ranks, or the error naming what to run."""
    db_path = root / ".crapkit" / "crap.sqlite"
    if not db_path.is_file():
        raise CrapkitError(f"no snapshot in {root} — run `crapkit coverage` first")
    store = SnapshotStore(db_path)
    latest = _latest_scored(store)
    if latest is None:
        raise CrapkitError(f"no scored run in {root} — run `crapkit coverage` first")
    return store, latest


def _pushdown_floor(cfg) -> int:
    """The lowest ccn next-item's SQL read may skip.

    Nothing under it can be admitted by any rule — not the floor, not hot
    promotion, and not a ceiling, since its worst-case CRAP cannot reach the
    smallest one configured — so that one number is a COUNT rather than 100k rows.
    """
    return sql_floor(cfg.worklist_floor, min([cfg.target, *cfg.scope_targets.values()]))


def cmd_next_item(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    store, latest = _scored_store(root)
    scopes = args.scope or []
    scored = store.read_scored(latest["id"], min_ccn=_pushdown_floor(cfg), scopes=scopes)
    adm = admission(load_churn(root, cfg.churn_window_months), cfg.worklist_floor)
    ranked, skipped_no_lane = _next_ranked(scored, adm)
    excludes = args.exclude or []
    ranked = [r for r in ranked if not _excluded_item(r, excludes)]
    ranked, skipped_claimed = _unclaimed(store, ranked)
    # one HEAD read for both the staleness verdict and the claims this call takes
    commit = head_commit(root)
    head = _next_head(latest, skipped_no_lane, skipped_claimed, latest["commit"] != commit)
    handles = _Handles(store, latest["id"])
    _maybe_claim(store, commit, args.claim, _claimable(ranked, args.top), handles)
    _emit_next(store, head, ranked, args.top, adm, cfg, scored, excludes, scopes,
               load_uncovered(root, cfg), handles)
    return 0


class _Handles:
    """The handle for a ranked row, counted over its whole file.

    The queue is a cut of the run: rows under the pushdown floor never reach it,
    and a scope filter can drop more. An ordinal counted over that cut would
    number a different function than the one `brief` names, so the count runs
    over the file's rows, read once per path and kept.
    """

    def __init__(self, store, run_id: int) -> None:
        self._store = store
        self._run_id = run_id
        self._by_path: dict[str, dict] = {}

    def of(self, row) -> str:
        if row.path not in self._by_path:
            self._by_path[row.path] = packet.handles(
                self._store.read_scored_file(self._run_id, row.path))
        return self._by_path[row.path][row.start]


def _unclaimed(store, ranked: list) -> tuple[list, int]:
    """The rows no session is holding, and how many an open claim hid.

    Filtering happens whether or not this session claims anything: a claim is
    worthless if only the session that took it honours it.
    """
    held = {(c["path"], c["long_name"]) for c in store.open_claims()}
    free = [r for r in ranked if (r.path, r.long_name) not in held]
    return free, len(ranked) - len(free)


def _next_head(latest: dict, skipped_no_lane: int, skipped_claimed: int,
               stale: bool) -> dict:
    """skipped_claimed appears only when a claim actually hid something, so a
    store nobody ever claimed in emits exactly the JSON it emitted before.

    `stale` is the same verdict worklist prints its warning from: the snapshot
    describes a commit HEAD has moved past, so the spans in it may have moved.
    """
    head = {"run_id": latest["id"], "commit": latest["commit"],
            "skipped_no_lane": skipped_no_lane, "stale": stale}
    if skipped_claimed:
        head["skipped_claimed"] = skipped_claimed
    return head


def _claimable(ranked: list, top: int) -> list:
    """What this call is about to hand out — nothing at all once the queue is
    finished, so an exploratory --claim on it cannot hide tomorrow's top item."""
    return ranked[:max(top, 1)] if _actionable(ranked) else []


def _maybe_claim(store, commit: str, take: bool, items: list, handles=None) -> None:
    """Record a claim per item this invocation is about to hand out.

    HEAD, not the snapshot's commit: the claim describes the tree the session
    starts editing, which is what makes the ancestor test at verify meaningful.

    The handle goes down with it, because it is the string the release takes:
    an anonymous function's long_name names every anonymous function in its file.
    """
    if not take:
        return
    for r in items:
        store.record_claim(path=r.path, long_name=r.long_name, commit=commit,
                           handle=None if handles is None else handles.of(r))


def _actionable(ranked: list) -> list:
    """The candidates with work left in them.

    A row whose remedy is "ok" sits at or under its ceiling with nothing to
    decompose and nothing to test; handing it back is what made the burn-down
    loop run forever.
    """
    return [r for r in ranked if r.remedy != "ok"]


def _next_reasons(store, run_id: int, ranked: list, scored, adm, cfg,
                  excludes: list, scopes: list) -> dict:
    """Why the queue is empty, including the case where it is empty because the
    work is done rather than because a filter ate everything."""
    reasons = _empty_reasons(store, run_id, scored, adm, cfg, excludes, scopes)
    if ranked:
        reasons["all_remaining_at_or_under_target"] = len(ranked)
    return reasons


def _emit_next(store, head: dict, ranked, top: int, adm, cfg, scored,
               excludes: list, scopes: list, uncovered, handles=None) -> None:
    if not _actionable(ranked):
        head.update(empty=True,
                    reasons=_next_reasons(store, head["run_id"], ranked, scored, adm, cfg,
                                          excludes, scopes))
    elif top and top > 1:
        head.update(empty=False,
                    items=[_next_item_payload(r, adm, cfg, uncovered, _handle(handles, r))
                           for r in ranked[:top]])
    else:
        head.update(empty=False,
                    item=_next_item_payload(ranked[0], adm, cfg, uncovered,
                                            _handle(handles, ranked[0])))
    _print_json(head)


def _handle(handles, row) -> str | None:
    """This row's handle, or None for a caller holding no file rows to count."""
    return None if handles is None else handles.of(row)


def _excluded_item(r, excludes: list) -> bool:
    return any(pat in r.path or pat in r.long_name for pat in excludes)


def _skip_reason(r, adm, excludes: list) -> str | None:
    """The reasons bucket this scored row falls in, or None when the queue took it.

    One walk, one rule: every count here is the complement of the admission the
    ranking used, so `below_floor` can never claim a row the queue handed out.
    """
    if r.flag == "no-lane":
        return "no_lane"
    if _excluded_item(r, excludes):
        return "excluded_by_flag"
    if _rankable(r, adm):
        return None
    return "no_churn_in_window" if r.path not in adm.churn else "below_floor"


def _no_lane_debt(r) -> bool:
    """A no-lane row that is over its ceiling.

    `no_lane` alone counts a wiring gap, which may hold nothing but healthy
    code. This is the half a stop condition has to read: work the queue can
    never hand out, because no lane measures its scope.
    """
    return r.flag == "no-lane" and r.remedy != "ok"


def _empty_reasons(store, run_id: int, scored, adm, cfg, excludes: list,
                   scopes: list) -> dict:
    """An empty queue must say what was filtered, or the silence reads as done.

    Rows under the pushdown floor can never be admitted by any rule, so one
    COUNT stands in for them; every row the read did return is bucketed by the
    admission itself. The count takes the same scope cut, or a scoped queue
    reports rows it was never going to offer.
    """
    reasons = {"no_lane": 0, "no_churn_in_window": 0, "excluded_by_flag": 0,
               "below_floor": store.count_scored_below(run_id, _pushdown_floor(cfg), scopes),
               "churn_window_months": cfg.churn_window_months,
               "no_lane_over_target": sum(1 for r in scored if _no_lane_debt(r))}
    for r in scored:
        bucket = _skip_reason(r, adm, excludes)
        if bucket:
            reasons[bucket] += 1
    return reasons


def _rankable(r, adm) -> bool:
    """A scored row the queue may hand out.

    no-lane rows score cov=0 for lack of TOOLING, not lack of tests; handing one
    to a session would rank a wiring gap above real risk. A file with no churn in
    the window belongs to the worklist's dormant list rather than to the queue —
    unless it is over its ceiling, and then it is debt wherever it sleeps.
    """
    over = r.remedy != "ok"
    if r.flag == "no-lane" or (r.path not in adm.churn and not over):
        return False
    return adm.admits(r.path, r.ccn, over_target=over)


def _no_lane_gap(r, adm) -> bool:
    """A row the queue declines only because its scope has no lane.

    Its cov=0 is a tooling gap, so it never ranks, but an agent still has to be
    told it exists — including when only the ceiling would have admitted it.
    """
    return r.flag == "no-lane" and adm.admits(r.path, r.ccn, over_target=r.remedy != "ok")


def _next_ranked(scored, adm):
    ranked = sorted((r for r in scored if _rankable(r, adm)),
                    key=lambda r: (-r.crap, -adm.of(r.path).commits, r.path, r.start))
    return ranked, sum(1 for r in scored if _no_lane_gap(r, adm))


def _uncovered_fields(uncovered, row) -> dict:
    """The dark lines inside one function's span, or null and the reason there
    are none to be had.

    null, not []: [] is what a function every artifact ran reports, so answering
    [] for a file no artifact spoke about tells an agent there is nothing left to
    test. The note key stays opt-in, so a repo whose artifacts answer emits
    exactly the JSON it emitted before dark lines existed.
    """
    note = uncovered.note_for(row.path, row.flag, row.scope)
    if note:
        return {"uncovered_lines": None, "uncovered_lines_note": note}
    return {"uncovered_lines": uncovered.in_span(row.path, row.start, row.end)}


def _next_item_payload(top, adm, cfg, uncovered, handle: str | None = None) -> dict:
    c = adm.of(top.path)
    ceiling = cfg.scope_targets.get(top.scope, cfg.target)
    return {
        "scope": top.scope, "path": top.path, "function": top.long_name,
        # the name form that survives the session's own edit: a start line moves,
        # a position among the file's anonymous functions does not
        "handle": handle,
        "start": top.start, "end": top.end, "ccn": top.ccn, "ccn_std": top.ccn_std,
        "cov": top.cov, "flag": top.flag, "crap": top.crap, "remedy": top.remedy,
        "nloc": top.nloc, "nesting": top.nesting, "cognitive": top.cognitive,
        "commits": c.commits, "authors": c.authors,
        "target": ceiling,
        # budgeting hints: pieces a decomposition needs; decision paths no test
        # walks. brief publishes these from the same helper, never a second copy
        **packet.budget(top, ceiling),
        # est_uncovered_paths counts them; these name them, so an add-tests
        # remedy no longer costs a by-hand read of the coverage artifact
        **_uncovered_fields(uncovered, top),
    }


def _claims_summary(claims: list) -> str:
    return ", ".join(f"{c['path']} {c['long_name']}" for c in claims) or "none"


def _print_claims(as_json: bool, claims: list) -> None:
    if as_json:
        _print_json({"claims": claims, "open": len(claims)})
        return
    print(f"{len(claims)} open claim(s)")
    for c in claims:
        print(f"  {c['created_at']}  {c['commit'][:11]}  {c['path']}  {c['long_name']}")


def _release_target(target: list) -> tuple[str, str]:
    if len(target) != 2:
        raise CrapkitError("claims release needs PATH NAME — the path and function "
                           "next-item printed — or --all to close every open claim")
    return target[0], target[1]


def _claim_matches(c: dict, name: str) -> bool:
    """Does this claim answer to `name`?

    Either name form it was handed out under. The handle is the one that works
    for an anonymous function: its long_name is `(anonymous)`, which every
    anonymous function in the file also answers to, so a release by long_name
    would close whichever claim sorts first.
    """
    return _name_matches(c["long_name"], name) or c.get("handle") == name


def _named_claims(claims: list, path: str, name: str) -> list:
    held = [c for c in claims if c["path"] == path and _claim_matches(c, name)]
    if not held:
        raise CrapkitError(f"no open claim on {name!r} in {path} — "
                           f"open: {_claims_summary(claims)}")
    return held


def _claims_to_release(claims: list, release_all: bool, target: list) -> list:
    if release_all:
        return claims
    return _named_claims(claims, *_release_target(target))


def _print_released(as_json: bool, closed: int) -> None:
    if as_json:
        _print_json({"released": closed})
        return
    print(f"released {closed} claim(s)")


def cmd_claims(args: argparse.Namespace) -> int:
    """The queue's loop state, and the way back out of a `next-item --claim`.

    Without a release path, one exploratory claim hides the top item until some
    verify happens to score that function at its ceiling — which, for the worst
    function in the repo, is the whole job.
    """
    store = _open_store(Path(args.repo).resolve())
    claims = store.open_claims()
    if args.action != "release":
        _print_claims(args.json, claims)
        return 0
    to_close = _claims_to_release(claims, args.all, args.target)
    _print_released(args.json, store.close_claims([c["id"] for c in to_close]))
    return 0


def _name_prefix(long_name: str) -> str:
    """The identifier a long_name opens with, before its parameter list.

    packet.bare_name is the definition. This was a second copy of the cut, and
    it is what kept `brief` rejecting a Rust or Go bare name after the packet
    that published it had already been fixed.
    """
    return packet.bare_name(long_name)


def _name_matches(long_name: str, name: str) -> bool:
    """Does `name` name this function, bare or whole?

    next-item, worklist and brief all publish `function` as the long_name, so the
    string an agent has just read has to be a string it can pass back — here, and
    to `claims release`. Matching only the bare identifier broke the chain: the
    exact value one command printed was rejected by the next.
    """
    return name in (long_name, _name_prefix(long_name))


def _matching_rows(rows: list, name: str) -> list:
    """The rows NAME resolves to, by the rule `explain` runs on the same string.

    Both commands go through `packet.matching_names`, so a name that picks one
    function in a packet cannot pick three in a trajectory.
    """
    wanted = set(packet.matching_names([r.long_name for r in rows], name))
    return [r for r in rows if r.long_name in wanted]


def _no_match_message(path: str, name: str, rows: list, candidates: list) -> str:
    if candidates:
        return f"{name!r} in {path} is ambiguous — candidates: {', '.join(candidates)}"
    known = sorted({_name_prefix(r.long_name) for r in rows})
    return (f"no function named {name!r} in {path} in the latest scored run"
            f" — it holds: {', '.join(known) or 'nothing'}")


def _row_at_line(path: str, rows: list, name: str):
    """The row opening on this line, when NAME is a bare start line.

    An `(anonymous)` function has no name to pass back and a file can hold two
    functions sharing one, so the line a row opens on is the disambiguator that
    always exists: no two functions in a file start on the same line.
    """
    if not name.isdigit():
        return None
    at = [r for r in rows if r.start == int(name)]
    if not at:
        raise CrapkitError(_no_line_message(path, name, rows))
    return at[0]


def _no_line_message(path: str, name: str, rows: list) -> str:
    starts = ", ".join(str(s) for s in sorted({r.start for r in rows}))
    return (f"no function starts at line {name} in {path} in the latest scored run"
            f" — it starts functions at: {starts or 'nothing'}")


def _row_by_handle(path: str, rows: list, name: str):
    """The row `(anonymous)#N` names, or None when NAME is not that form.

    N counts the file's anonymous functions in start order, so the handle
    outlives an edit above it that a start line would not. Out of range is an
    error here rather than a fall-through to the name forms: `(anonymous)#5` is
    unambiguously a handle, and reporting it as an unknown NAME would send a
    session hunting for a function whose real handle is on the list.
    """
    ordinal = packet.handle_ordinal(name)
    if ordinal is None:
        return None
    starts = packet.anonymous_starts(rows)
    if not 1 <= ordinal <= len(starts):
        raise CrapkitError(_no_handle_message(path, name, rows))
    return next(r for r in rows if r.start == starts[ordinal - 1])


def _no_handle_message(path: str, name: str, rows: list) -> str:
    held = ", ".join(packet.handle_names(rows))
    return (f"no {name} in {path} in the latest scored run"
            f" — it holds: {held or 'no anonymous functions'}")


def _pick_function(path: str, rows: list, name: str):
    """The one row `name` names, or an error listing what the file does hold."""
    at_line = _row_at_line(path, rows, name)
    if at_line is not None:
        return at_line
    by_handle = _row_by_handle(path, rows, name)
    if by_handle is not None:
        return by_handle
    wanted, ordinal = split_ordinal(name)
    matched = _matching_rows(rows, wanted)
    candidates = sorted({r.long_name for r in matched})
    if len(candidates) != 1:
        raise CrapkitError(_no_match_message(path, name, rows, candidates))
    return _one_of(path, matched, name, wanted, ordinal)


def _one_of(path: str, matched: list, name: str, wanted: str, ordinal: int):
    """Which of the same-named rows NAME meant.

    A bare name means the burn-down item, and that is the worst twin — the rule
    the ratchet, the verdict and `next-item` all run. The -start term is the
    tie-break: equal-scoring twins resolve to the one that appears first.

    `name#2` means the second of them in file order, which is the function its
    own ratchet key `name#2` is about. That is how a session addresses the twin
    a bare name does not pick.
    """
    if wanted == name:
        return max(matched, key=lambda r: (r.crap, -r.start))
    ordered = sorted(matched, key=lambda r: r.start)
    if ordinal > len(ordered):
        raise CrapkitError(_no_twin_message(path, name, wanted, len(ordered)))
    return ordered[ordinal - 1]


def _no_twin_message(path: str, name: str, wanted: str, held: int) -> str:
    return (f"no {name} in {path} in the latest scored run"
            f" — it holds {held} function(s) named {wanted!r}")


def _brief_mark(entries: list | None, key: tuple[str, str]) -> float | None:
    """The committed mark on this function, or None when the repo carries none."""
    from ..ratchet import mark_for

    return None if entries is None else mark_for(entries, *key)


def _brief_churn(churn: dict, path: str) -> dict | None:
    c = churn.get(path)
    return None if c is None else {"commits": c.commits, "authors": c.authors,
                                   "weight": c.weight}


def _brief_coupling(ranked: list, path: str) -> list[dict]:
    """This file's partners, cut out of the ranking every path in a batch shares."""
    from .verifying import _is_test_path

    return packet.coupling_partners(ranked, path, _is_test_path)


def _brief_twins(loader, row) -> list[dict]:
    from ..dup import find_twins

    return packet.with_contained(find_twins(row, loader.rows(), loader.sources(),
                                            indexed=loader.twin_index()))


class _BriefLoader:
    """Everything a packet reads that is not about one function, read once.

    A batch of N packets asked git for the same churn window N times, split the
    same file texts N times and reopened the same scored file per packet. Each
    read here happens on first use and is answered from memory afterwards, keyed
    by what actually invalidates it — the path — never by which packet asked.
    """

    def __init__(self, root: Path, cfg, store, latest: dict) -> None:
        self.root = root
        self.cfg = cfg
        self.store = store
        self.latest = latest
        self._whole_repo: dict = {}
        self._scored_files: dict = {}
        self._file_keys: dict = {}
        self._attempts: dict = {}

    def _once(self, key: str, build):
        """The one read behind `key`, kept for every packet after the first."""
        if key not in self._whole_repo:
            self._whole_repo[key] = build()
        return self._whole_repo[key]

    def rows(self) -> list:
        return self._once("rows", lambda: self.store.read_rows(self.latest["id"]))

    def sources(self) -> dict:
        return self._once("sources",
                          lambda: _load_sources(self.root, {r.path for r in self.rows()}))

    def twin_index(self):
        """Every function's shingles, built once for the whole batch.

        Built at find_twins' own min_lines. Nothing reaches brief with another
        one today; a `brief --min-lines` would have to key this memo on it, and
        until then find_twins rebuilds rather than answering at the wrong
        threshold. Process-local by construction: shingles are builtin hash()
        values, so this memo can never become a file.
        """
        from ..dup import function_index

        return self._once("twin_index",
                          lambda: function_index(self.rows(), self.sources()))

    def source(self, path: str) -> str | None:
        return self.sources().get(path)

    def churn(self) -> dict:
        return self._once("churn",
                          lambda: load_churn(self.root, self.cfg.churn_window_months))

    def coupling(self) -> list:
        """Every co-change pair in the window, ranked before any per-path cut."""
        return self._once("coupling", self._rank_coupling)

    def _rank_coupling(self) -> list:
        from ..coupling_cache import load_coupling

        return load_coupling(self.root, self.cfg.churn_window_months, ls_files(self.root))

    def uncovered(self):
        return self._once("uncovered", lambda: load_uncovered(self.root, self.cfg))

    def head(self) -> str:
        return self._once("head", lambda: head_commit(self.root))

    def stale(self) -> bool:
        return self.latest["commit"] != self.head()

    def versions(self) -> dict:
        return self._once("versions", _brief_versions)

    def scored_file(self, path: str) -> list:
        if path not in self._scored_files:
            self._scored_files[path] = self.store.read_scored_file(self.latest["id"], path)
        return self._scored_files[path]

    def key(self, row) -> tuple[str, str]:
        """The row's ratchet key, counted over the whole file it lives in.

        Cached per path beside the rows it is built from: a batch of packets
        about one file would otherwise recount its ordinals per packet.
        """
        if row.path not in self._file_keys:
            self._file_keys[row.path] = key_names(self.scored_file(row.path))
        return key_of(self._file_keys[row.path], row)

    def mark(self, row) -> float | None:
        return _brief_mark(self._once("marks",
                                      lambda: _ratchet_entries(self.root, self.cfg)),
                           self.key(row))

    def mark_age(self, row, mark: float | None) -> int | None:
        """How long the mark has stood. No mark, no history read: reading the
        ratchet file's git log is a spawn, and an unmarked function owes none."""
        if mark is None:
            return None
        return packet.mark_age_days(self._once("mark_events", self._read_mark_events),
                                    self.key(row))

    def _read_mark_events(self) -> list:
        from ..gitio import file_log_patches
        from ..ratchet_report import mark_events

        return mark_events(file_log_patches(self.root, self.cfg.ratchet_file))

    def attempts(self, row) -> list:
        key = (row.path, row.long_name)
        if key not in self._attempts:
            self._attempts.update(self.store.attempts_for([key]))
        return self._attempts[key]

    def prime_attempts(self, rows: list) -> None:
        """One query for a whole batch's claims, before the packets ask one by one."""
        self._attempts.update(
            self.store.attempts_for([(r.path, r.long_name) for r in rows]))


def _brief_versions() -> dict:
    """What produced these numbers. doctor already assembles the tool block, so
    a packet and a doctor report name the same three strings."""
    from ..analyze import ANALYSIS_VERSION
    from .admin import _version_report

    return packet.versions_block(_version_report(), ANALYSIS_VERSION)


def _row_ceiling(cfg, row) -> int:
    return cfg.scope_targets.get(row.scope, cfg.target)


def _packet_scope(cfg, row) -> str:
    """The scope that OWNS the path, which is what routes a lane and a scoped
    test command. The scored row's scope is the fallback, for a path no
    [[scope]] claims by prefix.

    universe's rule, the one that also set row.scope when the run scored this
    file: the deepest declared path whose languages claim the extension too.
    Asked with the extension arm open it answered `b` for a nested path the
    scored row put in `a`, so one packet named the deeper scope's lane and test
    command beside the shallower scope's ceiling.
    """
    from ..universe import owning_scope, scope_matchers

    return owning_scope(row.path, scope_matchers(cfg.scopes)) or row.scope


def _scope_config(cfg, name: str):
    return next((s for s in cfg.scopes if s.name == name), None)


def _packet_gate(loader, row) -> dict:
    mark = loader.mark(row)
    return packet.gate_rule(ceiling=_row_ceiling(loader.cfg, row), mark=mark,
                            mark_age_days=loader.mark_age(row, mark),
                            diff_uncovered_max=loader.cfg.diff_uncovered_max)


def _packet_commands(cfg, row, scope: str) -> dict:
    from .verifying import _scoped_command

    template = dict(cfg.scoped_tests).get(scope)
    scoped = _scoped_command(template, [row.path]) if template else None
    return packet.commands(row.path, scoped,
                           f"no [crapkit.scoped_tests] template for scope {scope!r}")


def _packet_context(loader, row, rows: list) -> dict:
    """What the packet ADDED around the brief: the function's own text, the rest
    of its file, the rule it will be judged by, the lane that measures it, its
    history of attempts, and the commands to run next."""
    cfg = loader.cfg
    scope = _packet_scope(cfg, row)
    return {
        "source": packet.function_source(loader.source(row.path), row.start, row.end),
        "file_functions": packet.file_functions(rows),
        "file_totals": packet.file_totals(rows, cfg.scope_targets, cfg.target),
        "gate_rule": _packet_gate(loader, row),
        "lane": packet.lane_record(packet.lane_for(scope, cfg.lanes)),
        "stale": loader.stale(),
        "versions": loader.versions(),
        "commands": _packet_commands(cfg, row, scope),
        "attempts": loader.attempts(row),
        "regrowth": packet.regrowth(loader.store.function_history(row.path, row.long_name)),
        "params": packet.params(row.long_name),
        "notes": packet.notes(cfg, _scope_config(cfg, scope)),
    }


def _brief_packet(loader, row) -> dict:
    """One function's whole start-editing context.

    Every field `brief --json` published before is here unchanged; the rest is
    what a session used to open the file, the config and the store to work out.

    `remedy` and the two estimates are promoted out of `scored` and out of
    `next-item`: they are what the session decides on, and reading one of them
    off a nested object while the queue published it at the top made the two
    payloads look like different answers.
    """
    rows = loader.scored_file(row.path)
    ceiling = _row_ceiling(loader.cfg, row)
    return {
        "run_id": loader.latest["id"], "commit": loader.latest["commit"],
        "path": row.path, "function": row.long_name,
        "handle": packet.handles(rows)[row.start],
        "scored": dict(row._asdict()),
        "target": ceiling,
        "remedy": row.remedy,
        **packet.budget(row, ceiling),
        "ratchet_mark": loader.mark(row),
        "churn": _brief_churn(loader.churn(), row.path),
        "coupling": _brief_coupling(loader.coupling(), row.path),
        "duplication_twins": _brief_twins(loader, row),
        **_uncovered_fields(loader.uncovered(), row),
        **_packet_context(loader, row, rows),
    }


def _brief_payload(root: Path, cfg, store: SnapshotStore, latest: dict, row) -> dict:
    """One packet for a caller holding a repo and a row rather than a loader."""
    return _brief_packet(_BriefLoader(root, cfg, store, latest), row)


def _mark_text(mark: float | None) -> str:
    return "none" if mark is None else f"{mark:.4f}"


def _churn_text(churn: dict | None) -> str:
    if churn is None:
        return "none in the window"
    return f"{churn['commits']} commits / {churn['authors']} authors"


def _brief_lines_text(out: dict) -> str:
    """null lines print their note; an empty list is a covered function, and says so."""
    lines = out["uncovered_lines"]
    if lines is None:
        return out["uncovered_lines_note"]
    return ", ".join(str(n) for n in lines) or "none"


def _print_brief_neighbours(out: dict) -> None:
    for t in out["duplication_twins"]:
        print(f"  twin {t['similarity']:.0%}  {t['path']}:{t['start']}  {t['long_name']}")
    for c in out["coupling"]:
        print(f"  co-changes {c['confidence']:.0%} ({c['support']}x)  {c['path']}")


def _print_brief_context(out: dict) -> None:
    """The packet fields a human at a terminal still wants: what else is in the
    file, what the gate will hold this function to, and the command to run."""
    totals = out["file_totals"]
    lane = out["lane"]
    print(f"  file: {totals['functions']} function(s), {totals['over_target']} over "
          f"target, crap load {totals['crap_load']}")
    print(f"  gate ceiling {out['gate_rule']['ceiling']}  "
          f"lane {lane['name'] if lane else 'none'}  ->  {out['commands']['gate']}")


def _print_brief(as_json: bool, out: dict) -> None:
    if as_json:
        _print_json(out)
        return
    s = out["scored"]
    print(f"{out['path']}:{s['start']}  {out['function']}")
    print(f"  ccn {s['ccn']} (cognitive {s['cognitive']})  cov {s['cov']:.0%}  "
          f"crap {s['crap']:.1f} vs target {out['target']}  -> {s['remedy']}")
    print(f"  mark {_mark_text(out['ratchet_mark'])}  churn {_churn_text(out['churn'])}")
    print(f"  uncovered lines: {_brief_lines_text(out)}")
    _print_brief_neighbours(out)
    _print_brief_context(out)


def _resolve_batch(requested: int) -> int:
    if requested < 1:
        raise ConfigError(f"brief --batch must be >= 1, got {requested}")
    return requested


def _batch_rows(loader, count: int) -> list:
    """The top N actionable queue items, admitted exactly as next-item admits them."""
    scored = loader.store.read_scored(loader.latest["id"],
                                      min_ccn=_pushdown_floor(loader.cfg))
    ranked, _ = _next_ranked(scored, admission(loader.churn(), loader.cfg.worklist_floor))
    return _actionable(ranked)[:count]


def _brief_batch(loader, count: int) -> dict:
    """N start-editing packets out of ONE process.

    A session that briefs its whole batch one command at a time pays for the
    store, the config, the churn window, the git log and every file text once
    per function. Here they are read once and every packet is cut from them.
    """
    rows = _batch_rows(loader, count)
    loader.prime_attempts(rows)
    return {"run_id": loader.latest["id"], "commit": loader.latest["commit"],
            "stale": loader.stale(),
            "packets": [_brief_packet(loader, row) for row in rows]}


def _brief_target(args: argparse.Namespace) -> tuple[str, str]:
    """PATH and NAME, which only --batch may leave out."""
    if not args.path or not args.name:
        raise CrapkitError("brief needs PATH NAME — the path and function next-item "
                           "printed — or --batch N for the top N queue items")
    return args.path, args.name


def cmd_brief(args: argparse.Namespace) -> int:
    """One call for everything a burn-down session opens a file already knowing."""
    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    store, latest = _scored_store(root)
    loader = _BriefLoader(root, cfg, store, latest)
    if args.batch is not None:
        _print_json(_brief_batch(loader, _resolve_batch(args.batch)))
        return 0
    path, name = _brief_target(args)
    row = _pick_function(path, loader.scored_file(path), name)
    _print_brief(args.json, _brief_packet(loader, row))
    return 0


def _resolve_top(requested: int | None, cfg) -> int:
    top = requested if requested is not None else cfg.worklist_top
    if top < 1:
        raise ConfigError(f"worklist top must be >= 1, got {top}")
    return top


def _stale_warning(stale: bool, as_json: bool, latest: dict) -> None:
    if stale and not as_json:
        print(f"warning: snapshot is for {latest['commit'][:11]}, HEAD has moved on — "
              "rerun `crapkit coverage`", file=sys.stderr)


def _entry_json(e) -> dict:
    """One ranked row. `flag` and `remedy` are the run's verdict on it, so a
    caller can tell a wiring gap and a finished row from real work without a
    second call; both are null on an inventory-only run."""
    return {"scope": e.scope, "path": e.path, "function": e.long_name,
            "start": e.start, "end": e.end, "ccn": e.ccn, "ccn_std": e.ccn_std,
            "nloc": e.nloc, "commits": e.commits, "authors": e.authors,
            "weight": e.weight, "risk": e.risk, "flag": e.flag, "remedy": e.remedy}


def _row_marker(e) -> str:
    """What the burn-down queue will do with this row, when that is not "hand it out".

    `no-lane` is a wiring gap next-item never ranks; `ok` is finished work the
    risk map still lists. Without them the two views read as contradicting each
    other on the same screen.
    """
    marks = []
    if e.flag == "no-lane":
        marks.append("no-lane")
    if e.remedy == "ok":
        marks.append("ok")
    return "  " + " ".join(marks) if marks else ""


def _worklist_payload(wl, latest: dict, cfg, stale: bool, batches: list | None) -> dict:
    """The queue, plus `batches` when one was asked for.

    ADDED, never swapped in: a reader that wants active[] or stale off a batched
    call gets them, because the cut is a second view of the same queue.
    """
    payload = {
        "run_id": latest["id"], "commit": latest["commit"], "stale": stale,
        "floor": cfg.worklist_floor, "churn_window_months": cfg.churn_window_months,
        "active": [_entry_json(e) for e in wl.active],
        "dormant_count": len(wl.dormant),
        "dormant_top": [_entry_json(e) for e in wl.dormant[:10]],
    }
    if batches is not None:
        payload["batches"] = [_batch_json(b) for b in batches]
    return payload


def _worklist_print(as_json: bool, wl, latest: dict, cfg, stale: bool,
                    batches: list | None) -> None:
    _stale_warning(stale, as_json, latest)
    if as_json:
        _print_json(_worklist_payload(wl, latest, cfg, stale, batches))
        return
    print(f"worklist @ {latest['commit'][:11]} (run {latest['id']}, floor ccn>={cfg.worklist_floor}, "
          f"churn {cfg.churn_window_months}mo) — {len(wl.active)} active, {len(wl.dormant)} dormant")
    for e in wl.active:
        print(f"  risk {e.risk:>8.1f}  ccn {e.ccn:>3} ({e.ccn_std:>3} std)  "
              f"{e.commits:>3}c/{e.authors}a w{e.weight:>7.2f}  {e.path}:{e.start}  "
              f"{e.long_name}{_row_marker(e)}")
    _print_batches(batches)


def _worklist_run(root: Path, store) -> dict:
    """The newest TRUSTED run, which is the run next-item ranks: one state, two
    commands.

    `kind != "inventory"` used to stand in for "scored", and it admitted the two
    runs trust refuses. A partial run measures a fraction of the suite and its
    CRAP is inflated to match; a failed verify's scores can come off a red tree.
    Either one ranked the queue while next-item picked its item off an older
    run, so the two commands answered one question differently.

    The fallback is the newest run with rows, for a repo that has only run
    `inventory`: next-item refuses that repo and a complexity-only ranking is
    still worth printing.
    """
    from ..store import default_baseline, rowful_runs

    trusted = default_baseline(store)
    if trusted is not None:
        return trusted
    runs = rowful_runs(store)
    if runs:
        return runs[-1]
    raise CrapkitError(f"no snapshot in {root} — run `crapkit coverage` first "
                       "(or `crapkit inventory` for complexity-only ranking, "
                       "with no coverage, flags or remedies)")


def cmd_worklist(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    store = _open_store(root, first_command="coverage")
    latest = _worklist_run(root, store)
    # the same pushdown next-item uses: no rule can admit anything below it, and
    # anything above it might be over target, so those rows have to be read
    scopes = args.scope or []
    rows = store.read_rows(latest["id"], min_ccn=_pushdown_floor(cfg), scopes=scopes)
    churn = load_churn(root, cfg.churn_window_months)
    wl = build_worklist(rows, churn, floor=cfg.worklist_floor,
                        top=_resolve_top(args.top, cfg),
                        marks=_worklist_marks(store, cfg, latest["id"], scopes))
    batches = _worklist_batches(root, cfg, wl.active, args.batches)
    _worklist_print(args.json, wl, latest, cfg, latest["commit"] != head_commit(root), batches)
    return 0


def _worklist_marks(store, cfg, run_id: int, scopes: list) -> dict:
    """The run's verdict per function: what the floor may not hide, and what
    each ranked row is.

    Empty on an inventory-only run, which scored no remedy: a run with no
    verdict has no debt to protect from the floor and nothing to say about a
    row, and there the floor is the whole admission rule.
    """
    return store.read_marks(run_id, min_ccn=_pushdown_floor(cfg), scopes=scopes)


def _worklist_batches(root: Path, cfg, active: list, requested: int | None) -> list | None:
    """The split queue when --batches asked for one, else None — the flag adds a
    view of the queue, it does not replace the queue."""
    if requested is None:
        return None
    return _split_worklist(root, cfg, active, _resolve_batches(requested))


def _resolve_batches(requested: int) -> int:
    if requested < 1:
        raise ConfigError(f"worklist --batches must be >= 1, got {requested}")
    return requested


def _split_worklist(root: Path, cfg, active: list, count: int) -> list:
    """The active queue cut into collision-free batches, coupling included.

    BATCH_CONTAINMENT is the ranking's own default confidence, so the batch cut
    is a truncation of the cached total order rather than a question of its own.
    test_coupling_cache pins that equality; retune one and this goes back to a
    walk of its own.
    """
    from ..coupling_cache import load_coupling
    from ..worklist import BATCH_PAIR_LIMIT, split_batches

    pairs = load_coupling(root, cfg.churn_window_months, ls_files(root))
    return split_batches(active, pairs[:BATCH_PAIR_LIMIT], batches=count)


def _batch_json(b) -> dict:
    return {"files": b.files, "entries": [_entry_json(e) for e in b.entries]}


def _print_batches(batches: list | None) -> None:
    for i, b in enumerate(batches or [], 1):
        print(f"batch {i}: {len(b.entries)} items in {len(b.files)} files: {', '.join(b.files)}")
