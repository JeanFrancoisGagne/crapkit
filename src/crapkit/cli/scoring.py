"""The scoring pipeline and the commands built on it: `inventory` (complexity
snapshot), `coverage` (lanes run, coverage joined onto a fresh inventory, scored
run written) and `rescore` (fresh complexity for named files over the latest
run's coverage, plus its --gate policy). The lane runner lives here because
scoring is what lanes exist to feed; `verify` borrows _scored_run from it."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

from .. import __version__
from ..cache import merged_cache
from ..errors import ConfigError, CrapkitError, ToolError
from ..gitio import GitFacts, ls_files
from ..snapshot import build_inventory_rows, tsv_lines
from ..store import SnapshotStore
from ..universe import assign_files, scan_files
from ._shared import (_analysis_tools, _emit_findings, _file_sizer, _gate_line,
                      _latest_scored, _load_repo_config, _print_json, _ratchet_entries,
                      _write_tsv)


def _tracked_files(files_by_scope: dict) -> list[str]:
    """Every scope's files flattened into one sorted list, each path once."""
    return sorted({f for files in files_by_scope.values() for f in files})


def _present_on_disk(root: Path, tracked: list[str]) -> list[str]:
    """Keep the tracked paths that exist, naming each dropped one on stderr."""
    # git ls-files lists staged deletions too; a tracked-but-absent file has no
    # functions and must not crash the run
    present = [f for f in tracked if (root / f).is_file()]
    for gone in set(tracked) - set(present):
        print(f"crapkit: tracked file missing from working tree, skipped: {gone}", file=sys.stderr)
    return present


def _records_by_scope(files_by_scope: dict, records_by_path: dict) -> dict:
    """Regroup per-file analysis records under the scope that owns each file."""
    return {
        scope: [r for f in files for r in records_by_path[f]]
        for scope, files in files_by_scope.items()
    }


def _analysis_workers(cfg) -> int | None:
    """[crapkit] analysis_workers, as ProcessPoolExecutor wants it: 0 means
    'unset', which is one worker per core — the pool's own default."""
    return cfg.analysis_workers or None


def _analyzed_corpus(root: Path, cache_path: Path, flat: list,
                     workers: int | None = None) -> tuple[dict, int]:
    """Analyze the whole corpus cache-first and write the rebuilt cache back.

    The prior cache dies with this frame on purpose: it holds a second copy of
    every record on a warm corpus, and nothing downstream reads it.
    """
    _, analyze_files, load_cache, save_cache = _analysis_tools()
    prior = load_cache(cache_path)
    records_by_path, cache_hits, new_cache = analyze_files(root, flat, cache=prior, workers=workers)
    # Saved unmerged on purpose: this rebuild is the one point that evicts the
    # entries for content no longer in the corpus.
    save_cache(cache_path, new_cache, prior=prior)
    return records_by_path, cache_hits


class _Corpus(NamedTuple):
    """What the analyzed file list came to: files in, files the byte ceiling cut."""
    files: int
    skipped_max_bytes: int


def _build_inventory(root: Path, cfg, git=None) -> tuple[str, list, _Corpus, int, dict]:
    """Shared by inventory/coverage: returns (commit, rows, corpus, cache_hits, tool_versions)."""
    lizard, *_ = _analysis_tools()
    commit = (git or GitFacts(root)).head_commit()
    universe = scan_files(ls_files(root), cfg, size_of=_file_sizer(root))
    flat = _present_on_disk(root, _tracked_files(universe.by_scope))
    records_by_path, cache_hits = _analyzed_corpus(
        root, root / ".crapkit" / "cache.json", flat, _analysis_workers(cfg))
    rows = build_inventory_rows(_records_by_scope(universe.by_scope, records_by_path))
    tool_versions = {"crapkit": __version__, "lizard": lizard.version}
    return commit, rows, _Corpus(len(flat), len(universe.oversized)), cache_hits, tool_versions


def cmd_inventory(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    commit, rows, corpus, cache_hits, tool_versions = _build_inventory(root, cfg)
    state_dir = root / ".crapkit"

    db_path = Path(args.db) if args.db else state_dir / "crap.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SnapshotStore(db_path)
    run_id = store.write_run(commit=commit, tool_versions=tool_versions, rows=rows, kind="inventory")

    if args.export:
        _write_tsv(root / args.export, tsv_lines(rows))

    summary = {
        "run_id": run_id,
        "commit": commit,
        "files": corpus.files,
        "functions": len(rows),
        "cache_hits": cache_hits,
        "skipped_max_bytes": corpus.skipped_max_bytes,
        "db": str(db_path),
    }
    if args.json:
        _print_json(summary)
    else:
        print(f"run {run_id} @ {commit[:11]}: {summary['functions']} functions "
              f"in {summary['files']} files ({cache_hits} cached) -> {db_path}")
    return 0


def _lane_reuse(root: Path, lane, scope_paths: dict, reuse_artifacts: bool, reuse_unchanged: bool,
                git) -> bool:
    from ..lanes import lane_unchanged

    if reuse_artifacts:
        return True
    if reuse_unchanged and lane_unchanged(root, lane, scope_paths, git):
        print(f"crapkit: lane {lane.name!r}: artifact still matches its scopes; reusing without rerun",
              file=sys.stderr)
        return True
    return False


def _progress(message: str) -> None:
    """One write, so two lanes reporting at once cannot split each other's line."""
    sys.stderr.write(f"crapkit: {message}\n")


def _run_one_lane(root: Path, lane, reuse: bool, scope_paths: dict | None, git):
    """One lane's outcome or its error text; a failed lane never sinks the run."""
    from ..lanes import run_lane

    try:
        return run_lane(root, lane, reuse_artifact=reuse, scope_paths=scope_paths, git=git), ""
    except ToolError as exc:
        return None, str(exc)


def _traced_lane(root: Path, lane, reuse: bool, scope_paths: dict | None, git):
    _progress(f"lane {lane.name!r} started")
    outcome = _run_one_lane(root, lane, reuse, scope_paths, git)
    _progress(f"lane {lane.name!r} finished")
    return outcome


def _execute_parallel(root: Path, ordered, reuse: dict, scope_paths, git, max_parallel: int) -> dict:
    """Lanes are subprocess-bound, so threads are enough: subprocess.run drops the
    GIL for the whole command and each lane streams to its own log file."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        futures = {lane: pool.submit(_traced_lane, root, lane, reuse[lane], scope_paths, git)
                   for lane in ordered}
    return {lane: future.result() for lane, future in futures.items()}


def _execute_lanes(root: Path, ordered, reuse: dict, scope_paths, git, max_parallel: int) -> dict:
    """lane -> (outcome, error text), keyed by the Lane itself rather than its
    name, which the config does not force to be unique. Serial below 2, which is
    the default: same thread, same order, none of the started/finished chatter."""
    if max_parallel < 2:
        return {lane: _run_one_lane(root, lane, reuse[lane], scope_paths, git) for lane in ordered}
    return _execute_parallel(root, ordered, reuse, scope_paths, git, max_parallel)


def _collect_lanes(root: Path, lanes, outcomes: dict):
    """Fold the outcomes back together in DECLARATION order, whatever order they
    finished in, and persist every stamp in one write."""
    from ..lanes import write_stamps

    coverage_by_path: dict[str, list] = {}
    provenance: dict[str, dict] = {}
    lane_errors: dict[str, str] = {}
    stamps: dict[str, dict] = {}
    succeeded = []
    for lane in lanes:
        outcome, error = outcomes[lane]
        if error:
            lane_errors[lane.name] = error
            print(f"crapkit: lane {lane.name!r} FAILED: {error}", file=sys.stderr)
            continue
        for path, fns in outcome.coverage.items():
            coverage_by_path.setdefault(path, []).extend(fns)
        provenance[lane.name] = outcome.provenance
        stamps[lane.artifact] = outcome.stamp
        succeeded.append(lane)
    write_stamps(root, stamps)
    # `lane_errors` and not `lanes`: a cc-only repo declares no lanes, so nothing
    # succeeded and nothing failed either, and that run is still a scored run.
    if lane_errors and not succeeded:
        raise ToolError(f"every lane failed: {'; '.join(lane_errors.values())}")
    return coverage_by_path, provenance, lane_errors, succeeded


def _run_lanes(root: Path, lanes, reuse_artifacts: bool, scope_paths: dict | None = None,
               reuse_unchanged: bool = False, max_parallel: int = 1, git=None):
    """Run each lane; a failed lane is recorded and skipped, never fatal alone.

    Every reuse decision is taken up front, on one thread: it reads the working
    tree and a lane command WRITES to the working tree, so deciding lane by lane
    would let one lane's output change the next lane's answer. Results then merge
    in declaration order however the lanes finished, so max_parallel_lanes moves
    wall time only — never a score.
    """
    from ..lanes import lane_order

    facts = git or GitFacts(root)
    reuse = {lane: _lane_reuse(root, lane, scope_paths or {}, reuse_artifacts,
                               reuse_unchanged, facts)
             for lane in lanes}
    ordered = lane_order(root, list(lanes)) if max_parallel > 1 else list(lanes)
    return _collect_lanes(root, lanes,
                          _execute_lanes(root, ordered, reuse, scope_paths, facts, max_parallel))


def _scored_run(root: Path, cfg, lanes, *, reuse_artifacts: bool, reuse_unchanged: bool = False,
                git=None):
    """Shared by coverage/verify: inventory + lanes + score. Returns everything both need.

    `git` is the caller's GitFacts when it already has one — verify asks for the
    dirty set before this runs, and that answer is the one the lanes must see too.
    """
    from ..score import score_rows

    git = git or GitFacts(root)
    commit, rows, corpus, cache_hits, tool_versions = _build_inventory(root, cfg, git)

    coverage_by_path, provenance, lane_errors, succeeded = _run_lanes(
        root, lanes, reuse_artifacts, cfg.scope_paths, reuse_unchanged,
        cfg.max_parallel_lanes, git)

    # Only scopes a SUCCESSFUL lane covers count as measured; a failed lane's
    # scopes fall back to no-lane flags rather than reading as untested code.
    lane_scopes = {s for lane in succeeded for s in lane.scopes}
    scored = score_rows(rows, coverage_by_path, lane_scopes=lane_scopes, target=cfg.target,
                        scope_targets=cfg.scope_targets,
                        cc_only_scopes=cfg.coverage_optional_scopes)
    test_failures = {f for prov in provenance.values() for f in prov.get("failures", ())}
    return commit, scored, provenance, lane_errors, test_failures, tool_versions, corpus, cache_hits


def _run_kind(lanes, cfg, failures) -> str:
    """A --lane subset or a run with failed lanes must never serve as the
    verify baseline: its lane set differs from what verify runs, so every
    pre-existing failure in the missing lanes would read as NEW forever."""
    full = not failures and {l.name for l in lanes} == {l.name for l in cfg.lanes}
    return "coverage" if full else "partial"


def _refuse_empty_lane_run(cfg, requested) -> None:
    """Refuse a run that selected no lane, unless running with none is right.

    Right for a repo whose every scope declares `coverage_optional`: nine of the
    fourteen languages have no coverage parser at all, such a repo is scored
    from complexity alone, and demanding a lane refused exactly the repos that
    can never supply one. Wrong the moment one scope has neither, which is what
    the message names — the scopes, not the empty list, because the list is a
    symptom and the scopes are the gap.
    """
    if requested is not None:
        raise ConfigError(f"no lane named {requested!r}")
    if cfg.lane_less_scopes:
        raise ConfigError(
            f"no [[lane]] to run for scope(s) {', '.join(cfg.lane_less_scopes)} — declare a "
            "[[lane]] measuring them in crapkit.toml, or set coverage_optional = true on a "
            "scope no coverage parser can read")


def _select_lanes(cfg, requested):
    """The lanes this run executes; an empty list is a legitimate answer."""
    lanes = [l for l in cfg.lanes if requested is None or l.name == requested]
    if not lanes:
        _refuse_empty_lane_run(cfg, requested)
    return lanes


def _export_scored(root: Path, export: str, scored) -> None:
    from ..score import scored_tsv_lines

    _write_tsv(root / export, scored_tsv_lines(scored))


def _flag_counts(scored) -> dict[str, int]:
    flags = {"measured": 0, "untested": 0, "no-lane": 0, "cc-only": 0}
    for r in scored:
        flags[r.flag] += 1
    return flags


def _by_scope(scored, cfg) -> dict[str, dict]:
    from ..digest import scope_rollup, scope_totals

    return scope_rollup(scope_totals(scored, target=cfg.target,
                                     scope_targets=cfg.scope_targets))


def _coverage_summary(run_id, commit, scored, cfg, provenance, failures, corpus, cache_hits, db_path):
    from ..score import grade

    flags = _flag_counts(scored)
    over = sum(1 for r in scored if r.crap > cfg.scope_targets.get(r.scope, cfg.target))
    return {
        "run_id": run_id, "commit": commit, "files": corpus.files, "functions": len(scored),
        "cache_hits": cache_hits, "measured": flags["measured"], "untested": flags["untested"],
        "no_lane": flags["no-lane"], "cc_only": flags["cc-only"],
        "skipped_max_bytes": corpus.skipped_max_bytes,
        "over_target": over, "grade": grade(over, len(scored)),
        "by_scope": _by_scope(scored, cfg),
        "crap_load": round(sum(r.crap for r in scored), 2), "lanes": provenance,
        "lane_failures": failures, "db": str(db_path),
    }


def _print_coverage(as_json: bool, summary: dict, cfg, failures: dict) -> None:
    if as_json:
        _print_json(summary)
        return
    print(f"run {summary['run_id']} @ {summary['commit'][:11]}: {summary['functions']} functions scored — "
          f"{summary['measured']} measured / {summary['untested']} untested / "
          f"{summary['no_lane']} no-lane / {summary['cc_only']} cc-only, "
          f"{summary['over_target']} over target {cfg.target}, CRAP load {summary['crap_load']}, "
          f"grade {summary['grade']}")
    for name, err in failures.items():
        print(f"  lane {name!r} FAILED: {err}")


def cmd_coverage(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    lanes = _select_lanes(cfg, args.lane)

    commit, scored, provenance, failures, _, tool_versions, corpus, cache_hits = _scored_run(
        root, cfg, lanes, reuse_artifacts=args.reuse_artifacts, reuse_unchanged=args.reuse_unchanged)

    db_path = root / ".crapkit" / "crap.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SnapshotStore(db_path)
    run_id = store.write_run(commit=commit, tool_versions=tool_versions, rows=scored,
                             lanes=provenance, kind=_run_kind(lanes, cfg, failures))
    if args.export:
        _export_scored(root, args.export, scored)
    _emit_coverage_findings(root, args, scored, cfg)

    summary = _coverage_summary(run_id, commit, scored, cfg, provenance, failures,
                                corpus, cache_hits, db_path)
    _print_coverage(args.json, summary, cfg, failures)
    return 5 if failures else 0


def _emit_coverage_findings(root: Path, args, scored, cfg) -> None:
    if not (args.sarif or args.github):
        return
    from ..sarif import over_target_results

    _emit_findings(root, args.sarif, args.github,
                   over_target_results(scored, cfg.scope_targets, cfg.target))


def _rescored_records(root: Path, cache_path: Path, flat: list,
                      workers: int | None = None) -> dict:
    """Fresh records for `flat`, folded INTO the shared cache rather than over it.

    A rescore knows about a handful of files; writing its entry map straight out
    would throw away every other file's analysis and leave the next full run cold.
    """
    _, analyze_files, load_cache, save_cache = _analysis_tools()
    prior = load_cache(cache_path)
    records_by_path, _, new_cache = analyze_files(root, flat, cache=prior, workers=workers)
    save_cache(cache_path, merged_cache(prior, new_cache), prior=prior)
    return records_by_path


def _rescore_analyze(root: Path, cfg, files) -> tuple[list, list, dict]:
    """Fresh complexity for the named files; the shared cache is merged, never truncated."""
    from ..hook import file_ceilings

    rel_paths = sorted({p.replace("\\", "/") for p in files})
    files_by_scope = assign_files(rel_paths, cfg, size_of=_file_sizer(root))
    flat = sorted(set().union(*files_by_scope.values())) if files_by_scope else []
    records_by_path = _rescored_records(root, root / ".crapkit" / "cache.json", flat,
                                        _analysis_workers(cfg))
    by_scope = {scope: [r for f in scope_files for r in records_by_path[f]]
                for scope, scope_files in files_by_scope.items()}
    return build_inventory_rows(by_scope), flat, file_ceilings(cfg, files_by_scope, flat)


def _baseline_rows(store: SnapshotStore, run_id: int, flat: list) -> list:
    """The baseline run's scored rows for the rescored files, read per file.

    A rescore knows a handful of paths. Reading the whole run and dropping the
    rest built a ScoredRow for every function in the repo to keep fifty of them:
    799.5 ms against 0.8 ms on a 140,990-row store, and `watch` pays it on every
    file it sees change.

    The concatenation is re-sorted into the store's own row order (scope, path,
    span, name). Reading path by path does not produce it when the files span
    two scopes, and the overlay picks the nearest start among same-name twins,
    so a different order can hand a function a different baseline row.
    """
    rows = [r for path in flat for r in store.read_scored_file(run_id, path)]
    rows.sort(key=lambda r: (r.scope, r.path, r.start, r.end, r.long_name))
    return rows


def _rescore_overlay(store: SnapshotStore, latest: dict, rows: list, flat: list, cfg):
    """Fresh complexity joined onto the LATEST run's stale coverage, by NAME first."""
    from ..score import overlay_stale_coverage

    lane_scopes = {s for prov in latest["lanes"].values() for s in prov.get("scopes", ())}
    return overlay_stale_coverage(rows, _baseline_rows(store, latest["id"], flat),
                                  lane_scopes=lane_scopes, target=cfg.target,
                                  scope_targets=cfg.scope_targets,
                                  cc_only_scopes=cfg.coverage_optional_scopes)


def _rescore_json(overlay, latest: dict) -> None:
    _print_json({
        "baseline_run": latest["id"], "baseline_commit": latest["commit"],
        "functions": [{
            "scope": r.scope, "path": r.path, "function": r.long_name, "start": r.start,
            "end": r.end, "ccn": r.ccn, "cov": r.cov, "flag": r.flag, "crap": r.crap,
            "remedy": r.remedy, "stale_coverage": True,
        } for r in overlay],
        "note": "coverage is the baseline run's; complexity is the working tree's. Run verify for the real verdict.",
    })


def _ceiling_breaches(rows, ceilings: dict[str, int]) -> list:
    """The pre-commit hook's policy over already-scored rows: ccn against the
    file's ceiling, coverage ignored. Shaped as gate violations so verify's
    printer serves this verdict too."""
    from ..verify import GateViolation

    breaches = [GateViolation(r.path, r.long_name, r.start, r.ccn, r.cov, r.crap, r.remedy)
                for r in rows if r.ccn > ceilings[r.path]]
    breaches.sort(key=lambda v: (-v.ccn, v.path, v.start))
    return breaches


def _gate_candidates(root: Path, rows: list) -> list:
    """The functions this commit could be about: spans the working tree changed
    against HEAD, index included, which is the set the pre-commit hook will see.

    Judging the whole file instead would flag every legacy function in it, so on
    any repo with seeded debt the flag is red forever and says nothing.
    """
    from ..diffparse import changed_ranges
    from ..gitio import diff_since
    from ..verify import touched_rows

    return touched_rows(rows, changed_ranges(diff_since(root, "HEAD")))


def _unmarked_breaches(breaches: list, entries: list) -> list:
    """Breaches no ratchet mark already covers.

    A mark is a recorded decision to carry a function as it stands. At or under
    it the function is exactly the debt the repo signed up for; past it, verify's
    ratchet check would fail too, so the gate says so early.
    """
    from ..ratchet import mark_for

    kept = []
    for v in breaches:
        mark = mark_for(entries, v.path, v.long_name)
        if mark is None or round(v.crap, 4) > mark:
            kept.append(v)
    return kept


def _untracked_of(root: Path, overlay) -> set[str]:
    """Rescored paths git tracks nothing of. Invisible to git diff, so without
    special handling their violations print and then exit 0 — the one state
    where the gate lies."""
    tracked = set(ls_files(root))
    return {r.path for r in overlay} - tracked


def _warn_untracked(untracked: set[str]) -> None:
    if untracked:
        print(f"crapkit: {len(untracked)} untracked file(s) gated in full "
              f"({', '.join(sorted(untracked))}) — git add to gate only future edits",
              file=sys.stderr)


def _rescore_gate(root: Path, cfg, overlay, ceilings: dict[str, int]) -> int:
    """The commit's verdict, hours before the commit. Reported on stderr so
    `--json` stdout stays one parseable object."""
    untracked = _untracked_of(root, overlay)
    _warn_untracked(untracked)
    candidates = _gate_candidates(root, overlay) + [r for r in overlay if r.path in untracked]
    touched = _ceiling_breaches(candidates, ceilings)
    breaches = _unmarked_breaches(touched, _ratchet_entries(root, cfg) or [])
    if not breaches:
        return 0
    print(f"crapkit gate: {len(breaches)} rescored function(s) over their scope ceiling:",
          file=sys.stderr)
    for v in breaches:
        print(_gate_line(v), file=sys.stderr)
    return 6


def cmd_rescore(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    db_path = root / ".crapkit" / "crap.sqlite"
    if not db_path.is_file():
        raise CrapkitError(f"no snapshot in {root} — run `crapkit coverage` first")
    store = SnapshotStore(db_path)
    latest = _latest_scored(store)
    if latest is None:
        raise CrapkitError(f"no scored run in {root} — run `crapkit coverage` first")

    rows, flat, ceilings = _rescore_analyze(root, cfg, args.files)
    overlay = _rescore_overlay(store, latest, rows, flat, cfg)
    if args.json:
        _rescore_json(overlay, latest)
    else:
        _print_rescore_table(overlay, latest)
    return _rescore_gate(root, cfg, overlay, ceilings) if args.gate else 0


def _print_rescore_table(overlay, latest: dict) -> None:
    """The refactor loop's view: fresh ccn, worst first, stale cov labeled."""
    print(f"rescore vs run {latest['id']} @ {latest['commit'][:11]} (coverage STALE, complexity fresh)")
    print(f"  {'ccn':>4} {'cov':>5} {'crap':>8}  {'remedy':10} function")
    for r in sorted(overlay, key=lambda x: (-x.ccn, x.path, x.start)):
        print(f"  {r.ccn:>4} {r.cov:>5.0%} {r.crap:>8.1f}  {r.remedy:10} {r.path}:{r.start}  {r.long_name}")
