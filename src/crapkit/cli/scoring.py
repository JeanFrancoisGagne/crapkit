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
from ..invocation import _self
from ..snapshot import build_inventory_rows, tsv_lines
from ..store import SnapshotStore
from ..universe import assign_files, scan_files
from ._shared import (_analysis_tools, _emit_findings, _file_sizer, _gate_line,
                      _latest_scored, _load_repo_config, _print_json, _ratchet_entries,
                      _repo_out_path, _repo_relative,
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
    """Regroup per-file analysis records under the scope that owns each file.

    A scope's file list comes from git, so it can name a path the analysis never
    saw — one `_present_on_disk` dropped. Those files contribute no records.
    """
    return {
        scope: [r for f in files for r in records_by_path.get(f, ())]
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
        _write_tsv(_repo_out_path(root, args.export), tsv_lines(rows))

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
    """One lane's outcome or the error that failed it; a failed lane never sinks
    the run. The error object, not its text: a refusal carries the modification
    times of the files the attempt left unwritten, which the fold persists."""
    from ..lanes import run_lane

    try:
        return run_lane(root, lane, reuse_artifact=reuse, scope_paths=scope_paths, git=git), ""
    except ToolError as exc:
        return None, exc


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
    """lane -> (outcome, error), keyed by the Lane itself rather than its
    name, which the config does not force to be unique. Serial below 2, which is
    the default: same thread, same order, none of the started/finished chatter."""
    if max_parallel < 2:
        return {lane: _run_one_lane(root, lane, reuse[lane], scope_paths, git) for lane in ordered}
    return _execute_parallel(root, ordered, reuse, scope_paths, git, max_parallel)


def _refuse_all_failed(lanes, lane_errors: dict, succeeded: list) -> None:
    # `lane_errors` and not `lanes`: a cc-only repo declares no lanes, so nothing
    # succeeded and nothing failed either, and that run is still a scored run.
    if lane_errors and not succeeded:
        # A count and a pointer, not the errors again: every one of them has
        # already printed above, and quoting them here made the reader read the
        # same block twice. Counted off `lanes`, not the error dict: the config
        # does not force lane names to be unique, and two failed lanes sharing
        # a name would collapse to one key.
        failed = len(lanes) - len(succeeded)
        raise ToolError(f"every lane failed ({failed} of {len(lanes)}); the errors are above")


def _collect_lanes(root: Path, lanes, outcomes: dict):
    """Fold the outcomes back together in DECLARATION order, whatever order they
    finished in, and persist every stamp in one write: the fresh stamps of the
    lanes that succeeded, and the refusal each failed lane's error carries for
    the artifact its attempt left unwritten."""
    from ..lanes import refusal_stamp, write_stamps

    coverage_by_path: dict[str, list] = {}
    provenance: dict[str, dict] = {}
    lane_errors: dict[str, str] = {}
    stamps: dict[str, dict] = {}
    succeeded = []
    for lane in lanes:
        outcome, error = outcomes[lane]
        if error:
            lane_errors[lane.name] = str(error)
            print(f"crapkit: lane {lane.name!r} FAILED: {error}", file=sys.stderr)
            stamps.update(refusal_stamp(root, lane, error))
            continue
        for path, fns in outcome.coverage.items():
            coverage_by_path.setdefault(path, []).extend(fns)
        provenance[lane.name] = outcome.provenance
        stamps[lane.artifact] = outcome.stamp
        succeeded.append(lane)
    write_stamps(root, stamps)
    _refuse_all_failed(lanes, lane_errors, succeeded)
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


class _ScoredRun(NamedTuple):
    """One inventory-lanes-score pass, for the two commands that need one.

    Two fields are failures of different kinds and used to sit next to each other
    in an unnamed tuple: `lane_errors` is the lanes that produced no coverage at
    all, keyed by lane name, and `test_failures` is the test ids that failed
    inside the lanes that ran. coverage exits 5 on a lane error and reads no test
    failure; verify refuses to conclude on a lane error and weighs each test
    failure against its baseline. `corpus` and `cache_hits` are coverage's report
    line, which is why verify names neither."""
    commit: str
    scored: list
    provenance: dict
    lane_errors: dict
    test_failures: set
    tool_versions: dict
    corpus: _Corpus
    cache_hits: int


def _scored_run(root: Path, cfg, lanes, *, reuse_artifacts: bool, reuse_unchanged: bool = False,
                git=None) -> _ScoredRun:
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
    return _ScoredRun(commit, scored, provenance, lane_errors, test_failures, tool_versions,
                      corpus, cache_hits)


def _run_kind(lanes, cfg, failures) -> str:
    """A --lane subset or a run with failed lanes must never serve as the
    verify baseline: its lane set differs from what verify runs, so every
    pre-existing failure in the missing lanes would read as NEW forever."""
    full = not failures and {l.name for l in lanes} == {l.name for l in cfg.lanes}
    return "coverage" if full else "partial"


class _RunShape(NamedTuple):
    """What the summary says about the run's own shape, on every path: the
    kind `runs list` files it under, the lanes that ran and failed, and the
    scopes a declared lane measures that no succeeding lane reached."""
    kind: str
    ran: list[str]
    failed: list[str]
    unmeasured: list[str]


def _measured_scopes(provenance: dict) -> set[str]:
    """The scopes the lanes that succeeded this run cover."""
    return {s for prov in provenance.values() for s in prov.get("scopes", ())}


def _unmeasured_scopes(cfg, provenance: dict) -> list[str]:
    """Scopes a declared lane measures that this run did not, in declaration
    order. A scope no lane declares is `no-lane` on every run and is not
    listed: it is a configuration `doctor` names, not this run's shape."""
    declared = {s for lane in cfg.lanes for s in lane.scopes} - _measured_scopes(provenance)
    return [s.name for s in cfg.scopes if s.name in declared]


def _run_shape(lanes, cfg, run: _ScoredRun) -> _RunShape:
    return _RunShape(_run_kind(lanes, cfg, run.lane_errors), list(run.provenance),
                     list(run.lane_errors), _unmeasured_scopes(cfg, run.provenance))


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

    _write_tsv(_repo_out_path(root, export), scored_tsv_lines(scored))


def _flag_counts(scored) -> dict[str, int]:
    flags = {"measured": 0, "untested": 0, "no-lane": 0, "cc-only": 0}
    for r in scored:
        flags[r.flag] += 1
    return flags


def _by_scope(scored, cfg) -> dict[str, dict]:
    from ..digest import scope_rollup, scope_totals

    return scope_rollup(scope_totals(scored, target=cfg.target,
                                     scope_targets=cfg.scope_targets))


def _judged_rows(scored, unmeasured: list[str]) -> list:
    """The rows the run's over-ceiling count and grade are taken over: every
    scope but the ones no lane measured this run. A skipped lane's functions
    score at cov 0 and read as debt; they are the scope's to report under
    `by_scope`, not this run's grade. On a full run this is every row."""
    left_out = set(unmeasured)
    return [r for r in scored if r.scope not in left_out]


def _coverage_summary(run_id: int, run: _ScoredRun, cfg, shape: _RunShape, db_path) -> dict:
    from ..score import grade

    flags = _flag_counts(run.scored)
    judged = _judged_rows(run.scored, shape.unmeasured)
    over = sum(1 for r in judged if r.crap > cfg.ceiling_of(r.scope))
    return {
        "run_id": run_id, "commit": run.commit, "files": run.corpus.files,
        "functions": len(run.scored), "cache_hits": run.cache_hits,
        "measured": flags["measured"], "untested": flags["untested"],
        "no_lane": flags["no-lane"], "cc_only": flags["cc-only"],
        "skipped_max_bytes": run.corpus.skipped_max_bytes,
        "over_target": over, "grade": grade(over, len(judged)),
        "by_scope": _by_scope(run.scored, cfg),
        "crap_load": round(sum(r.crap for r in run.scored), 2), "lanes": run.provenance,
        "lane_failures": run.lane_errors, "db": str(db_path),
        "kind": shape.kind, "unmeasured_scopes": shape.unmeasured, "ceilings": cfg.ceilings,
    }


def _lanes_word(names: list[str]) -> str:
    plural = "s" if len(names) > 1 else ""
    return f"lane{plural} {', '.join(names)}"


def _partial_opening(shape: _RunShape) -> str:
    """The first line of a partial run's text, so a `--lane` run is never
    mistaken for a baseline: what ran, what failed, what went unmeasured."""
    parts = [_lanes_word(shape.ran)]
    if shape.failed:
        parts.append(f"{_lanes_word(shape.failed)} failed")
    if shape.unmeasured:
        parts.append(f"{', '.join(shape.unmeasured)} unmeasured")
    return f"partial run ({'; '.join(parts)}; not a baseline)"


def _bucket_text(summary: dict) -> str:
    """The four flags counted, zero buckets dropped: `2 measured / 1 no-lane`."""
    buckets = [(summary["measured"], "measured"), (summary["untested"], "untested"),
               (summary["no_lane"], "no-lane"), (summary["cc_only"], "cc-only")]
    return " / ".join(f"{n} {word}" for n, word in buckets if n)


def _ceiling_label(ceilings: dict[str, int]) -> str:
    """`ceiling 6`, or `their ceilings (6; reports 12, util 4)` when a scope
    sets its own."""
    own = ", ".join(f"{scope} {c}" for scope, c in ceilings.items() if scope != "default")
    if not own:
        return f"ceiling {ceilings['default']}"
    return f"their ceilings ({ceilings['default']}; {own})"


def _summary_line(summary: dict) -> str:
    buckets = _bucket_text(summary)
    counted = f": {buckets}" if buckets else ""
    return (f"run {summary['run_id']} @ {summary['commit'][:11]}: "
            f"{summary['functions']} functions scored{counted}, "
            f"{summary['over_target']} over {_ceiling_label(summary['ceilings'])}, "
            f"CRAP load {summary['crap_load']}, grade {summary['grade']}")


def _next_command(kind: str) -> str:
    """What to run next: the ranking after a trusted run, the lanes a partial
    run skipped after a partial one."""
    if kind == "partial":
        return f"-> rerun changed lanes: {_self()} coverage --reuse-unchanged"
    return f"-> next: {_self()} worklist"


def _print_coverage(as_json: bool, summary: dict, shape: _RunShape) -> None:
    if as_json:
        _print_json(summary)
        return
    if shape.kind == "partial":
        print(_partial_opening(shape))
    print(_summary_line(summary))
    for name, err in summary["lane_failures"].items():
        print(f"  lane {name!r} FAILED: {err}")
    print(_next_command(shape.kind))


def _warn_suite_drop(store: SnapshotStore, provenance: dict) -> None:
    """Say when a lane ran far fewer tests than the last trusted run measured.

    Read before this run is written, so the comparison point is a run nothing in
    this command has touched. `verify` already reports any shrink against its
    baseline; `coverage` is the command that WRITES a baseline, and until now it
    said nothing at all about a suite that halved.
    """
    from ..lanes import suite_drops
    from ..store import trusted_runs

    trusted = trusted_runs(store)
    if not trusted:
        return
    for note in suite_drops(trusted[-1]["lanes"], provenance):
        print(f"crapkit: {note}", file=sys.stderr)


def cmd_coverage(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    lanes = _select_lanes(cfg, args.lane)

    run = _scored_run(root, cfg, lanes, reuse_artifacts=args.reuse_artifacts,
                      reuse_unchanged=args.reuse_unchanged)

    db_path = root / ".crapkit" / "crap.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SnapshotStore(db_path)
    _warn_suite_drop(store, run.provenance)
    shape = _run_shape(lanes, cfg, run)
    run_id = store.write_run(commit=run.commit, tool_versions=run.tool_versions, rows=run.scored,
                             lanes=run.provenance, kind=shape.kind)
    if args.export:
        _export_scored(root, args.export, run.scored)
    _emit_coverage_findings(root, args, run.scored, cfg)

    _print_coverage(args.json, _coverage_summary(run_id, run, cfg, shape, db_path), shape)
    return 5 if run.lane_errors else 0


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

    rel_paths = sorted({_repo_relative(p, root) for p in files})
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


def _rescore_json(overlay, latest: dict, gate: dict | None = None) -> None:
    """The functions, and under --gate the verdict beside them: one object,
    so an agent reading the payload never has to read stderr for the finding."""
    payload = {
        "baseline_run": latest["id"], "baseline_commit": latest["commit"],
        "functions": [{
            "scope": r.scope, "path": r.path, "function": r.long_name, "start": r.start,
            "end": r.end, "ccn": r.ccn, "cov": r.cov, "flag": r.flag, "crap": r.crap,
            "remedy": r.remedy, "stale_coverage": True,
        } for r in overlay],
        "note": "coverage is the baseline run's; complexity is the working tree's. Run verify for the real verdict.",
    }
    if gate is not None:
        payload["gate"] = gate
    _print_json(payload)


def _ceiling_breaches(rows, ceilings: dict[str, int], keys: dict | None = None) -> list:
    """The pre-commit hook's policy over already-scored rows: ccn against the
    file's ceiling, coverage ignored. Shaped as gate violations so verify's
    printer serves this verdict too.

    `keys` is the ratchet key map over the WHOLE file, because `rows` here is
    the touched subset: counting ordinals over it would call an untouched
    file's second twin the first and hand it the first's mark. Defaulted from
    the rows for a caller holding nothing else, which is right for one function
    per name and the only shape that arises.
    """
    from ..keys import key_names, key_of
    from ..verify import GateViolation

    names = key_names(rows) if keys is None else keys
    breaches = [GateViolation(r.path, r.long_name, r.start, r.ccn, r.cov, r.crap, r.remedy,
                              False, key_of(names, r)[1])
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
    from ..keys import stated_key
    from ..ratchet import mark_for

    kept = []
    for v in breaches:
        mark = mark_for(entries, *stated_key(v))
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


class _GateVerdict(NamedTuple):
    """The commit's verdict, hours before the commit: what was judged, against
    which ceiling per file, and the breaches no ratchet mark covers."""
    judged: int
    ceilings: dict[str, int]
    breaches: list
    untracked: list[str]

    @property
    def ok(self) -> bool:
        return not self.breaches


def _gate_verdict(root: Path, cfg, overlay, ceilings: dict[str, int]) -> _GateVerdict:
    from ..keys import key_names

    untracked = _untracked_of(root, overlay)
    candidates = _gate_candidates(root, overlay) + [r for r in overlay if r.path in untracked]
    touched = _ceiling_breaches(candidates, ceilings, key_names(overlay))
    breaches = _unmarked_breaches(touched, _ratchet_entries(root, cfg) or [])
    return _GateVerdict(len(candidates), ceilings, breaches, sorted(untracked))


def _breach_json(v, ceilings: dict[str, int]) -> dict:
    return {"path": v.path, "function": v.long_name, "start": v.start, "ccn": v.ccn,
            "cov": v.cov, "crap": v.crap, "remedy": v.remedy, "key_name": v.key_name,
            "ceiling": ceilings[v.path]}


def _gate_json(verdict: _GateVerdict) -> dict:
    """The `gate` block of `rescore --gate --json`: the fields a wrapper needs
    to say which function, which rule, and whether the tree clears the gate."""
    return {"ok": verdict.ok, "judged": verdict.judged, "ceilings": verdict.ceilings,
            "breaches": [_breach_json(v, verdict.ceilings) for v in verdict.breaches],
            "untracked": verdict.untracked}


def _gate_ceiling_label(ceilings: dict[str, int]) -> str:
    """`ceiling 6` when every rescored file is judged at one number, else
    `their ceilings` (the per-file map is in the JSON)."""
    distinct = sorted(set(ceilings.values()))
    return f"ceiling {distinct[0]}" if len(distinct) == 1 else "their ceilings"


def _report_gate(verdict: _GateVerdict, as_json: bool) -> int:
    """The verdict on stderr when it fails, so `--json` stdout stays one
    parseable object; one stdout line when it passes, so the exit code is
    not the only signal."""
    _warn_untracked(set(verdict.untracked))
    if verdict.ok:
        if not as_json:
            print(f"gate: {verdict.judged} changed function(s) judged, "
                  f"0 over {_gate_ceiling_label(verdict.ceilings)}")
        return 0
    print(f"crapkit gate: {len(verdict.breaches)} rescored function(s) over their scope ceiling:",
          file=sys.stderr)
    for v in verdict.breaches:
        print(_gate_line(v), file=sys.stderr)
    return 6


def _rescore_baseline(root: Path) -> tuple[SnapshotStore, dict]:
    """The store and the newest scored run a rescore overlays on, or the
    refusal naming the command that makes one."""
    db_path = root / ".crapkit" / "crap.sqlite"
    if not db_path.is_file():
        raise CrapkitError(f"no snapshot in {root} — run `{_self()} coverage` first")
    store = SnapshotStore(db_path)
    latest = _latest_scored(store)
    if latest is None:
        raise CrapkitError(f"no scored run in {root} — run `{_self()} coverage` first")
    return store, latest


def cmd_rescore(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    store, latest = _rescore_baseline(root)

    rows, flat, ceilings = _rescore_analyze(root, cfg, args.files)
    overlay = _rescore_overlay(store, latest, rows, flat, cfg)
    verdict = _gate_verdict(root, cfg, overlay, ceilings) if args.gate else None
    if args.json:
        _rescore_json(overlay, latest, None if verdict is None else _gate_json(verdict))
    else:
        _print_rescore_table(overlay, latest)
    return 0 if verdict is None else _report_gate(verdict, args.json)


def _print_rescore_table(overlay, latest: dict) -> None:
    """The refactor loop's view: fresh ccn, worst first, stale cov labeled."""
    print(f"rescore vs run {latest['id']} @ {latest['commit'][:11]} (coverage STALE, complexity fresh)")
    print(f"  {'ccn':>4} {'cov':>5} {'crap':>8}  {'remedy':10} function")
    for r in sorted(overlay, key=lambda x: (-x.ccn, x.path, x.start)):
        print(f"  {r.ccn:>4} {r.cov:>5.0%} {r.crap:>8.1f}  {r.remedy:10} {r.path}:{r.start}  {r.long_name}")
