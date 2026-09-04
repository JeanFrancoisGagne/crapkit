"""The read-only reporting commands: `digest` (delta between the last two scored
runs, optionally piped to alert_command), `trend` (per-run totals), `runs` (run
history and retention), `overrides` (the audit trail) and `explain` (one
function's trajectory, its ratchet mark, its dark lines, its commits and tests)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

from ..errors import ConfigError, CrapkitError, GitError, ToolError
from ..invocation import _self
from ..store import SnapshotStore
from ..uncovered import MissingLines, load_uncovered
from ._shared import (_command_root, _load_repo_config, _open_store, _print_json, _stand,
                      _ratchet_entries, _repo_out_path, _repo_relative)


def _digest_pair(store):
    """The newest two runs that share a lane set, or None after saying why not."""
    from ..digest import latest_comparable_pair
    from ..store import trusted_runs
    scored_runs = trusted_runs(store)
    pair = latest_comparable_pair(scored_runs)
    if pair is None:
        print(f"crapkit digest: no two of the {len(scored_runs)} scored run(s) share a lane set; "
              "nothing comparable yet (a --lane subset run never pairs with a full run)")
    _warn_skipped_runs(scored_runs, pair)
    return pair


def _warn_skipped_runs(scored_runs: list[dict], pair) -> None:
    """Say so when the pair is not the newest run and the one before it.

    Stdout carries the digest body and the alert carries a copy of it, so this
    goes to stderr like every other crapkit warning: additive for a reader,
    invisible to anything parsing the report. It fires on a quiet digest too —
    silence read off a stale pair is the version of this that costs the most.
    """
    from ..digest import skipped_runs

    skipped = skipped_runs(scored_runs, pair)
    if not skipped:
        return
    print(f"warning: digest compared runs {pair[0]['id']} -> {pair[1]['id']}, "
          f"skipping run(s) {', '.join(str(r['id']) for r in skipped)} — "
          "a run only pairs with one whose lane set is identical", file=sys.stderr)


def _send_digest_alert(root: Path, cfg, prev: dict, cur: dict, lines: list[str]) -> None:
    """Hand the digest body to the configured alert command; a nonzero exit is fatal."""
    import subprocess

    if not cfg.alert_command.strip():
        raise ConfigError("digest --alert needs [crapkit] alert_command")
    body = f"crapkit digest (runs {prev['id']} -> {cur['id']}):\n" + "\n".join(lines) + "\n"
    proc = subprocess.run(cfg.alert_command, shell=True, cwd=root, input=body,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise ToolError(f"digest alert command failed (exit {proc.returncode})")


def cmd_digest(args: argparse.Namespace) -> int:
    from ..digest import build_digest

    root = _command_root(args.repo)
    cfg = _load_repo_config(root)
    db_path = root / ".crapkit" / "crap.sqlite"
    if not db_path.is_file():
        raise CrapkitError(f"no snapshot in {root} — run `{_self()} coverage` first")
    store = SnapshotStore(db_path)
    pair = _digest_pair(store)
    if pair is None:
        return 0
    prev, cur = pair
    # read_crap, not read_scored: build_digest names four of a ScoredRow's
    # sixteen fields, and a digest carries two whole runs at once
    d = build_digest(store.read_crap(prev["id"]), store.read_crap(cur["id"]),
                     ceiling_of=cfg.ceiling_of)
    if d.quiet:
        return 0  # an unchanged week says nothing
    for line in d.lines:
        print(line)
    if args.alert:
        _send_digest_alert(root, cfg, prev, cur, d.lines)
    return 0


def _scope_rollup_from_agg(scope_agg: dict) -> dict[str, dict]:
    """Per-scope SQL sums shaped the way coverage --json shapes rows in hand.

    Both go through totals_from_counts, which is where the rounding rule lives:
    a second rounding here would make the same run read two ways.
    """
    from ..digest import scope_rollup, totals_from_counts

    return scope_rollup({scope: totals_from_counts(*agg) for scope, agg in scope_agg.items()})


def _trend_row(run: dict, agg: tuple, scope_agg: dict) -> dict:
    from ..digest import totals_from_counts

    t = totals_from_counts(*agg)
    return {"run_id": run["id"], "commit": run["commit"], "created_at": run["created_at"],
            "functions": t.functions, "over_target": t.over_target,
            "crap_load": t.crap_load, "avg": t.avg,
            "by_scope": _scope_rollup_from_agg(scope_agg)}


def _print_trend(as_json: bool, rows_out: list, target: int) -> None:
    if as_json:
        _print_json({"target": target, "runs": rows_out})
        return
    for r in rows_out:
        print(f"run {r['run_id']:>3} @ {r['commit'][:11]} {r['created_at']}: "
              f"{r['over_target']} over ceiling, load {r['crap_load']}, avg {r['avg']}")


def _trend_payload(cfg, store: SnapshotStore) -> dict:
    """The whole series, shaped once. `trend --json` prints it and `report`
    renders it, so the page and the payload cannot describe the same run
    differently."""
    from ..store import trusted_runs

    # one GROUP BY for the whole history; this used to build every ScoredRow of
    # every trusted run to add up three numbers per run
    agg = store.run_totals(target=cfg.target, scope_targets=cfg.scope_targets)
    by_scope = store.run_scope_totals(target=cfg.target, scope_targets=cfg.scope_targets)
    return {"target": cfg.target,
            "runs": [_trend_row(run, agg.get(run["id"], (0, 0, 0.0)),
                                by_scope.get(run["id"], {}))
                     for run in trusted_runs(store)]}


def cmd_trend(args: argparse.Namespace) -> int:
    root = _command_root(args.repo)
    cfg = _load_repo_config(root)
    payload = _trend_payload(cfg, _open_store(root))
    _print_trend(args.json, payload["runs"], cfg.target)
    return 0


# --- the static page ---------------------------------------------------------
#
# `report` measures nothing. It collects the payloads `worklist --json` and
# `trend --json` already answer, at their defaults, adds the per-lane staleness
# `load_uncovered` computes, and hands the lot to a pure renderer. Everything
# below is collection; the markup lives in crapkit.report.

def _report_worklist(root: Path, cfg, store: SnapshotStore) -> dict:
    """The ranked queue through the SAME shaping `worklist --json` prints.

    Reassembling it here would let the page rank one function first and the
    command another, off one run.
    """
    from ..gitio import head_commit
    from ..report import report_top
    from .queue import _worklist_for, _worklist_payload, _worklist_run

    latest = _worklist_run(root, store)
    wl = _worklist_for(root, cfg, store, latest, top=report_top(cfg.worklist_top), scopes=[])
    return _worklist_payload(wl, latest, cfg, latest["commit"] != head_commit(root), None)


def _report_lanes(root: Path, cfg) -> list[dict]:
    """Per-lane staleness, the detail the joined note throws away."""
    from ..uncovered import lane_states

    return [{"name": name, "note": note} for name, note in lane_states(root, cfg)]


def _report_payload(root: Path, cfg, store: SnapshotStore) -> dict:
    from datetime import datetime, timezone

    return {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lanes": _report_lanes(root, cfg),
            "repo": root.name,
            "target": cfg.target,
            "trend": _trend_payload(cfg, store),
            "worklist": _report_worklist(root, cfg, store)}


def _write_report(root: Path, out: str, page: str) -> Path:
    """Write the page and say where it went.

    `_repo_out_path` decides where: a relative `--out` is repo-relative and may
    not climb out of the tree, an absolute one is the reader naming a
    destination on purpose, and the parent directory is created. That rule
    started here and three other writers copied it, so it lives in one place
    now. The page carries no relative asset and reads nothing from where it
    lands, so the destination changes nothing but who can open it. LF endings,
    because the page is an artifact people diff and publish.
    """
    path = _repo_out_path(root, out)
    path.write_text(page, encoding="utf-8", newline="\n")
    print(path)
    return path


def cmd_report(args: argparse.Namespace) -> int:
    """One self-contained page: the ranked worklist, the per-scope grades, the
    trend series, and a banner when stale artifacts make them untrustworthy."""
    from ..report import render_report

    root = _command_root(args.repo)
    cfg = _load_repo_config(root)
    payload = _report_payload(root, cfg, _open_store(root))
    _write_report(root, args.out, render_report(payload))
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    """History without SQL: every run with kind, verdict, commit, lane set."""
    store = _open_store(_command_root(args.repo))
    if args.action == "prune":
        return _runs_prune(store, keep=args.keep, as_json=args.json)
    return _runs_list(store, as_json=args.json)


def _runs_list(store: SnapshotStore, *, as_json: bool) -> int:
    """Every run, with the one `verify` compares against today marked.

    "Which run is my baseline" is the question the taint rule turns on, and this
    is the command a reader reaches for to answer it.
    """
    from ..store import pick_baseline

    history = store.list_runs()
    picked = pick_baseline(history).run
    baseline_id = picked["id"] if picked else None
    runs = [{"id": r["id"], "kind": r["kind"], "verdict_ok": r["verdict_ok"],
             "findings": r["findings"], "baseline": r["id"] == baseline_id,
             "commit": r["commit"], "lanes": sorted(r["lanes"]), "created_at": r["created_at"]}
            for r in history]
    if as_json:
        _print_json({"runs": runs})
        return 0
    for r in runs:
        print(_run_line(r))
    return 0


def _run_line(r: dict) -> str:
    """`verdict=-` means a run that produces no verdict, not a run that failed."""
    verdict = {True: "ok", False: "FAILED", None: "-"}[r["verdict_ok"]]
    mark = "  baseline" if r["baseline"] else ""
    return (f"run {r['id']:>3} @ {r['commit'][:11]} {r['created_at']} {r['kind']:<9} "
            f"verdict={verdict:<6} lanes={','.join(r['lanes']) or '-'}{mark}")


def _runs_prune(store: SnapshotStore, *, keep: int, as_json: bool) -> int:
    """Drop the runs outside the keep-set and hand the pages back to the OS.

    Keep is a floor on retention, not a cap: the keep-set also holds the digest
    pair, every passing verify baseline, every run an override names, and the
    newest non-hook run, so a prune can never make another command lie.
    """
    from ..store import prune_keep_set

    if keep < 1:
        raise ConfigError(f"runs prune --keep must be >= 1, got {keep}")
    keep_ids = prune_keep_set(store.list_runs(), store.override_run_ids(), keep=keep)
    before = store.size_bytes()
    store.prune_claims(keep_ids)  # retention is one decision, runs and loop state together
    deleted = store.prune_runs(keep_ids)
    store.vacuum()  # the DELETE alone frees pages, not disk
    _print_prune(deleted, len(keep_ids), before - store.size_bytes(), as_json)
    return 0


def _print_prune(deleted: int, kept: int, freed: int, as_json: bool) -> None:
    if as_json:
        _print_json({"pruned_runs": deleted, "kept_runs": kept, "freed_bytes": freed})
        return
    print(f"runs prune: deleted {deleted} run(s), kept {kept}, freed {freed} byte(s)")


def cmd_overrides(args: argparse.Namespace) -> int:
    """The override audit trail, read back out of the snapshot store."""
    store = _open_store(_command_root(args.repo))
    trail = [{"run_id": rid, "path": path, "function": name, "crap": crap,
              "reason": reason, "created_at": ts, "commit": sha}
             for rid, path, name, crap, reason, ts, sha in store.read_overrides_all()]
    if args.json:
        _print_json({"overrides": trail})
        return 0
    for o in trail:
        print(f"run {o['run_id']:>3} @ {o['commit'][:11]} {o['created_at']}  crap {o['crap']:.1f}  "
              f"{o['path']}  {o['function']}  ({o['reason']})")
    return 0


_NO_SPAN = "function not in the latest run"

_NO_CONTEXT = ("no context data — run the py lane with dynamic_context = "
               "test_function and a --show-contexts JSON report")

# %x01 opens a commit record and %x02 closes it, so a body of any shape stays
# separable from the diff hunks `git log -L` prints between records.
_LOG_FORMAT = "%x01%h %ad %s%n%b%x02"


class _ExplainCtx(NamedTuple):
    """What explain looks up ONCE for the whole command and reuses per match.

    Every one of these used to be redone for each matched function: the lane
    artifacts reparsed for dark lines and again for test attribution, the
    ratchet file reread, crapkit.toml reloaded, the run table rescanned to find
    the newest run. None of them depends on which function is being explained,
    so a file with four overloads paid for four of each.
    """
    root: Path
    path: str
    run_id: int | None
    uncovered: MissingLines
    ratchet: list | None
    contexts: dict
    # The twin NAME selected: 1 for a bare name, 2 for `f#2`. It is the ordinal
    # in the ratchet key, and `(anonymous)#2` reads the same way — that handle
    # and that twin's key are the same string.
    ordinal: int = 1


def cmd_explain(args: argparse.Namespace) -> int:
    """The trajectory behind a verdict, assembled once and then rendered.

    Text and --json read the same payload, so a section can never say one thing
    to a human and another to a wrapper.
    """
    root = _command_root(args.repo)
    cfg = _load_repo_config(root)
    store = _open_store(root)
    args.path = _repo_relative(args.path, root, _stand(args.repo))  # from where the user stands
    matches = store.find_functions(args.path, args.name)
    if not matches:
        raise CrapkitError(f"no function matching {args.name!r} in {args.path} appears in any run")
    ctx = _explain_ctx(root, cfg, store, args)
    _print_explain(args, [_explain_payload(ctx, store, args, name) for name in matches])
    return 0


def _explain_ctx(root: Path, cfg, store: SnapshotStore, args) -> _ExplainCtx:
    from ..keys import split_ordinal
    from ..store import rowful_runs

    runs = rowful_runs(store)
    return _ExplainCtx(root, args.path, runs[-1]["id"] if runs else None,
                       load_uncovered(root, cfg), _ratchet_entries(root, cfg),
                       _contexts_for_path(root, cfg, args.path) if args.tests else {},
                       split_ordinal(args.name)[1])


def _explain_payload(ctx: _ExplainCtx, store: SnapshotStore, args, long_name: str) -> dict:
    """One function's whole packet. The span is looked up once and passed down:
    dark lines, --history and --tests all want the same line range."""
    from ..keys import key_name

    span = _latest_span(store, ctx.run_id, ctx.path, long_name)
    out = {"long_name": long_name,
           "history": store.function_history(ctx.path, long_name),
           **_mark_fields(ctx.ratchet, ctx.path, key_name(long_name, ctx.ordinal)),
           **_dark_fields(ctx.uncovered, ctx.path, span)}
    if args.history:
        out.update(_commits_fields(ctx.root, ctx.path, span))
    if args.tests:
        out.update(_tests_fields(ctx.contexts, span))
    return out


def _print_explain(args, payloads: list[dict]) -> None:
    """Text unless asked for JSON. The flag is read defensively because explain
    answered in text long before it had one, and an absent flag means text."""
    if getattr(args, "json", False):
        _print_json({"path": args.path, "name": args.name, "functions": payloads})
        return
    for p in payloads:
        print(f"{args.path}  {p['long_name']}")
        _print_history(p["history"])
        print(f"  mark: {_ratchet_mark(p)}")
        _print_uncovered(p)
        _explain_extras(p)


def _latest_span(store: SnapshotStore, run_id: int | None, path: str, long_name: str):
    """The newest non-hook run's (start, end) for one function, or None.

    A targeted lookup off the (run_id, path) index; this used to materialize
    every row of that run to find one.
    """
    return None if run_id is None else store.function_span(run_id, path, long_name)


def _mark_fields(ratchet: list | None, path: str, long_name: str) -> dict:
    """null and a note when the repo carries no marks file: an unmarked function
    and a repo with no ratchet both read as null, and they want different moves.

    `long_name` arrives as the KEY name, so `explain path f#2` reads the second
    twin's mark and a bare `f` reads the first's.
    """
    from ..ratchet import mark_for

    if ratchet is None:
        return {"ratchet_mark": None, "ratchet_mark_note": "no ratchet file"}
    return {"ratchet_mark": mark_for(ratchet, path, long_name)}


def _dark_fields(uncovered: MissingLines, path: str, span) -> dict:
    """The dark lines inside one span, or null and the reason there are none.

    null when no artifact could answer, never []: [] is what a function every
    artifact ran reports, and it would read as nothing left to test.
    """
    if span is None:
        return {"uncovered_lines": None, "uncovered_lines_note": _NO_SPAN}
    note = uncovered.note_for(path)
    if note:
        return {"uncovered_lines": None, "uncovered_lines_note": note}
    return {"uncovered_lines": uncovered.in_span(path, span[0], span[1])}


def _commits_fields(root: Path, path: str, span) -> dict:
    if span is None:
        return {"commits": None, "commits_note": _NO_SPAN}
    return {"commits": _function_commits(root, path, span[0], span[1])}


def _tests_fields(contexts: dict, span) -> dict:
    """No span means silence, not guidance: nothing is missing for a function
    the latest run does not carry."""
    if span is None:
        return {"tests": None}
    tests = _contexts_for_span(contexts, span)
    return {"tests": tests} if tests else {"tests": None, "tests_note": _NO_CONTEXT}


def _contexts_for_path(root: Path, cfg, path: str) -> dict[int, set]:
    """line -> test ids for ONE file, off every coveragepy artifact, parsed once.

    Every matched function used to reparse every artifact to ask the same
    question about the same file.
    """
    from ..coverage_py import parse_coveragepy_contexts

    by_line: dict[int, set] = {}
    for lane in cfg.lanes:
        artifact = root / lane.artifact
        if lane.parser != "coveragepy" or not artifact.is_file():
            continue
        ctx = parse_coveragepy_contexts(artifact.read_text(encoding="utf-8"),
                                        path_prefix=lane.path_prefix)
        for line, ids in ctx.get(path, {}).items():
            by_line.setdefault(line, set()).update(ids)
    return by_line


def _contexts_for_span(contexts: dict, span) -> list[str]:
    """The test ids recorded against any line inside one span, sorted."""
    return sorted({t for line, ids in contexts.items()
                   if span[0] <= line <= span[1] for t in ids})


def _function_commits(root: Path, rel_path: str, start: int, end: int,
                      limit: int = 10) -> list[dict]:
    """Commits that touched one line span, subject AND body, from `git log -L`.

    The body is what says why a span keeps changing; a subject line rarely does.
    """
    from ..gitio import _git

    try:
        out = _git(root, "log", f"-L{start},{end}:{rel_path}", f"--format={_LOG_FORMAT}",
                   "--date=short", f"--max-count={limit}")
    except GitError:
        return []
    return _parse_log_records(out)


def _parse_log_records(out: str) -> list[dict]:
    """Records out of `git log -L`, dropping the diff hunks printed between them.

    A line is body text only while a record is open, so a hunk that happens to
    look like prose can never land in one.
    """
    records: list[dict] = []
    body: list[str] | None = None
    for line in out.splitlines():
        if line.startswith("\x01"):
            sha, date, subject = line[1:].split(" ", 2)
            records.append({"sha": sha, "date": date, "subject": subject, "body": ""})
            body = []
        elif body is None:
            continue
        elif line == "\x02":
            records[-1]["body"] = "\n".join(body).strip("\n")
            body = None
        else:
            body.append(line)
    return records


def _explain_extras(p: dict) -> None:
    if "commits" in p:
        _explain_commits(p)
    if "tests" in p:
        _explain_tests(p)


def _explain_commits(p: dict) -> None:
    """A body indents under its commit; a bodiless commit keeps the one line it
    always printed. A paragraph break stays blank rather than six spaces."""
    if p["commits"] is None:
        print(f"  commits: {p['commits_note']}")
        return
    for c in p["commits"]:
        print(f"    {c['sha']} {c['date']} {c['subject']}")
        for line in c["body"].splitlines():
            print(f"      {line}" if line else "")


def _explain_tests(p: dict) -> None:
    if p["tests"] is None:
        if "tests_note" in p:
            print(f"  tests: {p['tests_note']}")
        return
    for t in p["tests"]:
        print(f"    covered by {t}")


def _print_uncovered(p: dict) -> None:
    print(f"  uncovered lines: {_uncovered_text(p)}")


def _uncovered_text(p: dict) -> str:
    lines = p["uncovered_lines"]
    if lines is None:
        return f"none ({p['uncovered_lines_note']})"
    return ", ".join(str(n) for n in lines) or "none"


def _print_history(history: list) -> None:
    for h in history:
        cov = "-" if h["cov"] is None else f"{h['cov']:.0%}"
        crap = "-" if h["crap"] is None else f"{h['crap']:.1f}"
        print(f"  run {h['run_id']:>3} @ {h['commit'][:11]} {h['kind']:<9} "
              f"ccn {h['ccn']:>3}  cov {cov:>5}  crap {crap:>8}  {h['flag'] or '-'}")


def _ratchet_mark(p: dict) -> str:
    if "ratchet_mark_note" in p:
        return p["ratchet_mark_note"]
    mark = p["ratchet_mark"]
    return ("none (below target or never marked)" if mark is None
            else f"{mark:.4f} (committed high-water mark)")
