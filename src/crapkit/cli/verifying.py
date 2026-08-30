"""The verdict commands: `verify` (baseline pick, lanes, gate/ratchet/failure
evaluation, override audit, claim release), `hook-precommit` (the staged-function
ceiling gate and its audited env override) and `test-scoped` (routing changed
files to their scope's isolated test command). All three answer the same
question at different moments: does this change hold?"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from ..errors import ConfigError, CrapkitError, ToolError
from ..store import SnapshotStore
from ._shared import (_analysis_tools, _dirty_tag, _emit_findings, _gate_line,
                      _load_ratchet_or_die, _load_repo_config, _print_json, _write_tsv)
from .scoring import _scored_run


def _emit_verify_findings(root: Path, args, verdict, uncovered: list) -> None:
    if not (args.sarif or args.github):
        return
    from ..sarif import diff_uncovered_results, gate_results, regression_results

    _emit_findings(root, args.sarif, args.github,
                   gate_results(verdict.gate_violations)
                   + regression_results(verdict.ratchet_regressions)
                   + diff_uncovered_results(uncovered))


def _no_baseline(root: Path) -> str:
    return (f"no trusted scored baseline in {root} — run `crapkit coverage` first "
            "(failed verifies and hook runs never serve as baselines)")


def _taint_note(pick) -> str:
    """Why the newest trusted run is not the baseline, and the two ways past it.

    Both escapes are in the line because both are legitimate: answer the
    findings, or accept the newer run by name, which is a visible act somebody
    can audit later.
    """
    fallback = ("nothing older is left to measure against" if pick.run is None else
                f"measuring against run {pick.run['id']} @ {pick.run['commit'][:11]} instead")
    return (f"run {pick.skipped['id']} is not the baseline: verify run {pick.blocker['id']} "
            f"FAILED with {pick.blocker['findings']} finding(s) and no passing verify has "
            f"cleared it since — {fallback}, so those findings stay visible. Fix them, or "
            f"pass `--baseline {pick.skipped['id']}` to accept the newer run deliberately.")


def _named_baseline(store: SnapshotStore, root: Path, requested: int) -> dict:
    """`--baseline ID` bypasses the taint rule: naming a run is the deliberate act."""
    from ..store import trusted_runs

    baseline = next((r for r in trusted_runs(store) if r["id"] == requested), None)
    if baseline is None:
        raise CrapkitError(_no_baseline(root))
    return baseline


def _verify_baseline(root: Path, store: SnapshotStore, requested: int | None) -> dict:
    """The trusted run this verify measures against."""
    from ..store import pick_baseline

    if requested is not None:
        return _named_baseline(store, root, requested)
    pick = pick_baseline(store.list_runs())
    if pick.run is None:
        raise CrapkitError(_taint_note(pick) if pick.blocker else _no_baseline(root))
    if pick.blocker:
        print(f"warning: {_taint_note(pick)}", file=sys.stderr)
    return pick.run


def _require_ancestor(git, commit: str) -> None:
    from ..errors import GitError

    if not git.is_ancestor(commit):
        raise GitError(
            f"baseline commit {commit[:11]} is not an ancestor of HEAD "
            "(rebase or amend rewrote history) — run `crapkit coverage` for a fresh baseline")


def _baseline_behind(git, store: SnapshotStore, basis: str) -> dict:
    """The newest trusted run at or behind the fork point.

    A run made further up the branch would measure the diff from its own commit,
    and everything committed before it stops being touched — which is exactly the
    shrinking --base exists to stop.
    """
    from ..store import trusted_runs

    behind = [r for r in trusted_runs(store) if git.is_ancestor(r["commit"], basis)]
    if not behind:
        raise CrapkitError(
            f"no trusted scored run at or behind {basis[:11]} — run `crapkit coverage` "
            "on the base commit before verifying against it")
    return behind[-1]


def _tsv_baseline(root: Path, rel: str) -> dict:
    """A baseline read from a file the repo carries, for a clone whose .crapkit/
    is gitignored. It holds no lane provenance, so it can neither report a
    shrinking suite nor forgive a failure the baseline run already had."""
    from ..verify import parse_baseline_tsv

    path = root / rel
    if not path.is_file():
        raise CrapkitError(f"no baseline file at {path} — write one with `verify --emit-baseline`")
    try:
        parsed = parse_baseline_tsv(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"unreadable baseline file {rel}: {exc}") from exc
    return {"id": None, "commit": parsed.commit, "kind": parsed.kind,
            "lanes": {}, "rows": parsed.rows}


def _pick_baseline(root: Path, store: SnapshotStore, args, basis: str | None, git) -> dict:
    if args.baseline_tsv:
        return _tsv_baseline(root, args.baseline_tsv)
    if basis:
        return _baseline_behind(git, store, basis)
    return _verify_baseline(root, store, args.baseline)


def _verify_basis(root: Path, store: SnapshotStore, args, git) -> tuple[dict, str]:
    """(the baseline record, the commit the diff is measured from).

    --base pins the basis to merge-base(REF, HEAD) instead of the baseline run's
    own commit; without it the two are the same commit and nothing changes.
    """
    from ..gitio import merge_base

    basis = merge_base(root, args.base) if args.base else None
    baseline = _pick_baseline(root, store, args, basis, git)
    _require_ancestor(git, baseline["commit"])
    return baseline, basis or baseline["commit"]


def _emit_baseline(root: Path, store: SnapshotStore, baseline: dict, rel: str | None) -> None:
    """Write the baseline this run used as a portable file, before the verdict:
    a run that ends in a failure still owes the operator its basis."""
    from ..verify import baseline_tsv_lines

    if not rel:
        return
    rows = baseline.get("rows")
    if rows is None:
        rows = store.read_scored(baseline["id"])
    _write_tsv(root / rel, baseline_tsv_lines(baseline["commit"], baseline["kind"], rows))


def _verify_store(root: Path, tsv_baseline: str | None) -> SnapshotStore:
    """A fresh clone has no .crapkit/ at all. With a portable baseline the store
    is created here, since this run is the first thing that will ever write it."""
    db_path = root / ".crapkit" / "crap.sqlite"
    if not (db_path.is_file() or tsv_baseline):
        raise CrapkitError(f"no baseline snapshot in {root} — run `crapkit coverage` first")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SnapshotStore(db_path)


def _ratchet_sha256(path: Path) -> str | None:
    """The ratchet's bytes as the verdict saw them — hashed before a clean pass
    tightens the file, so the receipt names the input, not the output."""
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _guard_ratchet_stamp(ratchet_path: Path, name: str) -> None:
    """Refuse to weigh fresh scores against marks another metric produced.

    Runs before the lanes do: a metric bump that silently kept 40k old marks is
    what this exists to stop, and finding out after a 40-minute run is too late.
    """
    from ..ratchet import metric_version, read_stamp, stamp_conflict

    if not ratchet_path.is_file():
        return
    recorded = read_stamp(ratchet_path.read_text(encoding="utf-8"))
    if not recorded:
        print(f"warning: {name} carries no metric stamp (written before stamping) — "
              "re-baseline with `crapkit ratchet seed` to stamp it", file=sys.stderr)
        return
    conflict = stamp_conflict(recorded, metric_version())
    if conflict:
        raise ConfigError(conflict)


def _apply_verify_override(store: SnapshotStore, run_id: int, root: Path, cfg, verdict, reason):
    """Grant --override for pure gate violations; regressions and new failures never qualify."""
    from ..override import record_override

    if verdict.ok or not reason or not verdict.gate_violations \
            or verdict.ratchet_regressions or verdict.new_failures:
        return verdict, []
    record_override(store=store, run_id=run_id, root=root, ratchet_file=cfg.ratchet_file,
                    alert_command=cfg.alert_command, violations=verdict.gate_violations,
                    reason=reason)
    overridden = verdict.gate_violations
    return verdict._replace(ok=True, gate_violations=[]), overridden


def _prior_crap(store: SnapshotStore, commit: str, run_id: int) -> dict[tuple[str, str], float]:
    """What the same commit's last trusted run measured, by RATCHET KEY.

    Keyed the way the marks it will be compared against are keyed, twin ordinal
    included: a module's second `__post_init__` answers to `__post_init__#2`, and
    reading its score under the bare name asks about a different function. The
    fresh side of the same comparison is keyed by `verify.rows_by_key`, so the
    two agree by construction.

    Worst per key covers the one collision the ordinal leaves — two scopes
    claiming one path score one span twice — or the comparison is between two
    measurements of different things.
    """
    from ..keys import key_names, key_of

    prior_id = store.prior_scored_run(commit=commit, before=run_id)
    if prior_id is None:
        return {}
    rows = store.read_scored(prior_id)
    names = key_names(rows)
    worst: dict[tuple[str, str], float] = {}
    for row in rows:
        key = key_of(names, row)
        worst[key] = max(worst.get(key, 0.0), round(row.crap, 4))
    return worst


def _no_tighten_line(refusal) -> str:
    """One mark this run left alone, and the two numbers that decided it."""
    return (f"  NO TIGHTEN  {refusal.path}  {refusal.long_name}: measurement moved "
            f"{refusal.previous} -> {refusal.fresh} on the same commit; not tightening")


def _held_marks(store: SnapshotStore, cfg, commit: str, run_id: int, ratchet,
                scored) -> frozenset[tuple[str, str]]:
    """Marks a bouncing measurement may not pull down, named on stderr as it decides.

    stderr, not stdout: `--json` prints one object and nothing else, and this is
    a warning about the measurement rather than part of the verdict.
    """
    from ..ratchet import unstable_marks

    if not ratchet:
        return frozenset()  # no marks, nothing a tighten could move
    refusals = unstable_marks(ratchet, scored, _prior_crap(store, commit, run_id),
                              max_jump=cfg.tighten_max_jump)
    for refusal in refusals:
        print(_no_tighten_line(refusal), file=sys.stderr)
    return frozenset((r.path, r.long_name) for r in refusals)


def _settle_verify(store: SnapshotStore, run_id: int, verdict, overridden,
                   ratchet_path: Path, ratchet, scored, cfg, *, args, commit: str) -> None:
    """Stamp the verdict; a clean pass (not an override) tightens the ratchet.

    `--no-tighten` is the blunt escape: the verdict still stands, the marks file
    is simply not rewritten.
    """
    from ..ratchet import dump_ratchet, update_ratchet
    from ..verify import dirty_counts

    store.set_verdict_ok(run_id, verdict.ok, findings=sum(dirty_counts(verdict)))
    if not verdict.ok or overridden or args.no_tighten:
        return
    hold = _held_marks(store, cfg, commit, run_id, ratchet, scored)
    updated = update_ratchet(ratchet, scored, target=cfg.target,
                             scope_targets=cfg.scope_targets, hold=hold)
    ratchet_path.write_text(dump_ratchet(updated), encoding="utf-8", newline="\n")


def _release_claims(store: SnapshotStore, git, cfg, scored) -> None:
    """Verify is the only command that both rescores everything and knows where
    HEAD is, so it is the one that can tell a finished claim from a held one."""
    from ..worklist import closable_claims

    claims = store.open_claims()
    if not claims:
        return
    stale = {c["commit"] for c in claims if not git.is_ancestor(c["commit"])}
    store.close_claims(closable_claims(claims, scored, target=cfg.target,
                                       scope_targets=cfg.scope_targets, stale_commits=stale))


def _print_verify_findings(verdict, overridden) -> None:
    dirty_ids = set(verdict.dirty_failures)
    for v in verdict.gate_violations:
        print(_gate_line(v))
    for r in verdict.ratchet_regressions:
        print(f"  RATCHET  {r.path}  {r.long_name}: {r.recorded} -> {r.fresh_crap}{_dirty_tag(r.dirty)}")
    for f in verdict.new_failures:
        print(f"  NEW FAILURE  {f}{_dirty_tag(f in dirty_ids)}")
    for v in overridden:
        print(f"  OVERRIDDEN  {v.path}:{v.start}  {v.long_name}")


def _print_finding_split(verdict) -> None:
    """A verdict measures the working tree, so a concurrent session's edits land
    in it. One line says how much of this one is not yours.

    "uncommitted edits and untracked files", not "uncommitted tracked edits":
    the dirty set is `status_names`, which unions `ls-files --others`, so a new
    failure in a test file git has never seen is counted here too.
    """
    from ..verify import dirty_counts

    committed, dirty = dirty_counts(verdict)
    if committed or dirty:
        print(f"  findings: {committed} committed / {dirty} dirty "
              "(uncommitted edits and untracked files)")


def _verify_exit_code(verdict, diff_breach: bool = False) -> int:
    if verdict.gate_violations:
        return 6
    if verdict.ratchet_regressions:
        return 7
    if verdict.new_failures:
        return 8
    return 9 if diff_breach else 0


def _diff_cover_breach(cfg, uncovered: list) -> bool:
    if cfg.diff_uncovered_max is None:
        return False
    if len(uncovered) <= cfg.diff_uncovered_max:
        return False
    print(f"diff coverage: {len(uncovered)} uncovered changed line(s) over the ceiling "
          f"{cfg.diff_uncovered_max}", file=sys.stderr)
    return True


def _baseline_failures(baseline: dict) -> set:
    return {f for prov in baseline["lanes"].values() for f in prov.get("failures", ())}


def _verify_attribution(verdict) -> dict:
    from ..verify import dirty_counts

    committed, dirty = dirty_counts(verdict)
    return {"committed_findings": committed, "dirty_findings": dirty,
            "dirty_failures": list(verdict.dirty_failures)}


def _verify_result(verdict, overridden, run_id: int, baseline: dict, commit: str, ranges,
                   uncovered: list) -> dict:
    return {
        "ok": verdict.ok,
        "run_id": run_id,
        "baseline_run": baseline["id"],
        "baseline_commit": baseline["commit"],
        "commit": commit,
        "changed_files": len(ranges),
        "gate_violations": [v._asdict() for v in verdict.gate_violations],
        "ratchet_regressions": [r._asdict() for r in verdict.ratchet_regressions],
        "new_failures": verdict.new_failures,
        "overridden": [v._asdict() for v in overridden],
        "diff_uncovered_count": len(uncovered),
        "diff_uncovered": [{"path": p, "line": ln} for p, ln in uncovered[:50]],
        **_verify_attribution(verdict),
    }


def _warn_diff_uncovered(uncovered: list) -> None:
    if not uncovered:
        return
    print(f"warning: {len(uncovered)} changed line(s) have no coverage", file=sys.stderr)
    for path, line in uncovered[:20]:
        print(f"  uncovered {path}:{line}", file=sys.stderr)


def _report_verify(as_json: bool, out: dict, verdict, overridden) -> None:
    if as_json:
        _print_json(out)
        return
    state = "OK" if verdict.ok else "FAILED"
    print(f"verify {state} @ {out['commit'][:11]} vs baseline {out['baseline_commit'][:11]} "
          f"({out['changed_files']} changed files)")
    _print_verify_findings(verdict, overridden)
    _print_finding_split(verdict)


def _refuse_lane_less_verify(cfg) -> None:
    """Refuse a verdict on a scope nothing measures and no key excuses.

    Stricter than `coverage`, on purpose. `coverage` scores such a scope and
    flags every row `no-lane`, because scoring is its whole job; `verify` calls
    coverage half the verdict, so an unmeasured scope leaves it with half an
    answer and it says so instead. The test that a cc-only repo passes is that
    `lane_less_scopes` is empty, not that lanes exist: such a repo has none and
    never will, and its verdict is the gate and the ratchet, neither of which
    needs a coverage number.
    """
    if cfg.lane_less_scopes:
        raise ConfigError(f"verify needs a [[lane]] for scope(s) {', '.join(cfg.lane_less_scopes)}"
                          " — coverage is half the verdict; a scope no coverage parser can read "
                          "declares coverage_optional = true instead")


def cmd_verify(args: argparse.Namespace) -> int:
    from ..diffparse import changed_ranges
    from ..gitio import GitFacts, diff_since
    from ..uncovered import missing_by_path
    from ..verify import diff_uncovered, evaluate

    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    _refuse_lane_less_verify(cfg)
    store = _verify_store(root, args.baseline_tsv)
    _guard_ratchet_stamp(root / cfg.ratchet_file, cfg.ratchet_file)
    # One context for the whole command: the ancestry checks, the lane runner and
    # this attribution all used to spawn their own git. Asking here also FIXES the
    # dirty set before any lane command runs, so a lane writing into a tracked file
    # cannot enlarge the set this verdict blames on somebody else.
    git = GitFacts(root)
    dirty = set(git.status_names())
    baseline, basis = _verify_basis(root, store, args, git)
    _emit_baseline(root, store, baseline, args.emit_baseline)

    # Six of the eight fields by name; corpus and cache_hits are coverage's report
    # line and no part of a verdict.
    run = _scored_run(root, cfg, list(cfg.lanes), reuse_artifacts=args.reuse_artifacts,
                      reuse_unchanged=args.reuse_unchanged, git=git)
    commit, scored, provenance = run.commit, run.scored, run.provenance
    tool_versions, fresh_failures = run.tool_versions, run.test_failures
    if run.lane_errors:
        raise ToolError(f"verify cannot conclude with failed lanes: {'; '.join(run.lane_errors)}")

    ranges = changed_ranges(diff_since(root, basis))
    ratchet_path = root / cfg.ratchet_file
    ratchet = _load_ratchet_or_die(ratchet_path, cfg.ratchet_file)
    receipt = {"tool_versions": tool_versions, "ratchet_sha256": _ratchet_sha256(ratchet_path)}

    verdict = evaluate(fresh=scored, changed_ranges=ranges, ratchet=ratchet,
                       baseline_failures=_baseline_failures(baseline), fresh_failures=fresh_failures,
                       target=cfg.target, scope_targets=cfg.scope_targets, dirty_paths=dirty)
    verdict = _maybe_flake_retry(root, cfg, provenance, verdict)
    _warn_suite_shrink(baseline, provenance)
    uncovered = diff_uncovered(ranges, missing_by_path(root, cfg))
    _warn_diff_uncovered(uncovered)
    breach = _diff_cover_breach(cfg, uncovered)
    if breach:
        verdict = verdict._replace(ok=False)  # a breached run never advances the baseline
    run_id = store.write_run(commit=commit, tool_versions=tool_versions, rows=scored,
                             lanes=provenance, kind="verify")
    verdict, overridden = _apply_verify_override(store, run_id, root, cfg, verdict, args.override)
    _settle_verify(store, run_id, verdict, overridden, ratchet_path, ratchet, scored, cfg,
                   args=args, commit=commit)
    _release_claims(store, git, cfg, scored)
    _emit_verify_findings(root, args, verdict, uncovered)

    _report_verify(args.json,
                   {**_verify_result(verdict, overridden, run_id, baseline, commit, ranges, uncovered),
                    **receipt},
                   verdict, overridden)
    return _verify_exit_code(verdict, breach)


def _flake_retry(root: Path, cfg, provenance: dict, new_failures: set) -> set:
    """Rerun just the newly-failed ids in lanes that declare retest_command;
    lanes without one keep their failures untouched."""
    from ..lanes import retest_lane

    survivors = set(new_failures)
    for lane in cfg.lanes:
        lane_new = set(provenance.get(lane.name, {}).get("failures", ())) & new_failures
        if not lane_new or not lane.retest_command:
            continue
        survivors -= retest_lane(root, lane, lane_new)
    return survivors


def _maybe_flake_retry(root: Path, cfg, provenance: dict, verdict):
    if not verdict.new_failures:
        return verdict
    survivors = _flake_retry(root, cfg, provenance, set(verdict.new_failures))
    if len(survivors) == len(verdict.new_failures):
        return verdict
    print(f"flake retry: {len(verdict.new_failures) - len(survivors)} of "
          f"{len(verdict.new_failures)} new failures passed on rerun", file=sys.stderr)
    ok = not (verdict.gate_violations or verdict.ratchet_regressions or survivors)
    return verdict._replace(ok=ok, new_failures=sorted(survivors))


def _warn_suite_shrink(baseline: dict, provenance: dict) -> None:
    """Suite decay passes a pass/fail check silently; say it out loud."""
    for name, prov in provenance.items():
        base = baseline.get("lanes", {}).get(name, {})
        b_total, b_skip = base.get("tests_total"), base.get("tests_skipped")
        if b_total and prov.get("tests_total", 0) < b_total:
            print(f"warning: lane {name!r} runs {b_total - prov['tests_total']} "
                  f"fewer tests than the baseline", file=sys.stderr)
        if b_skip is not None and prov.get("tests_skipped", 0) > b_skip:
            print(f"warning: lane {name!r} skips {prov['tests_skipped'] - b_skip} "
                  f"more tests than the baseline", file=sys.stderr)


def _under_scope_path(path: str, scope_path: str) -> bool:
    """True when path is the declared scope path itself or sits under it."""
    return path == scope_path or path.startswith(scope_path.rstrip("/") + "/")


def _owning_scope(path: str, scope_paths: dict[str, tuple[str, ...]]) -> str | None:
    """The scope matching deepest, so a nested scope wins over the parent that also contains it."""
    matches = [(len(sp), name) for name, paths in scope_paths.items()
               for sp in paths if _under_scope_path(path, sp)]
    return max(matches)[1] if matches else None


def _is_test_path(path: str) -> bool:
    parts = path.lower().split("/")
    return any(p in ("test", "tests", "__tests__") for p in parts[:-1])         or parts[-1].startswith("test_") or ".test." in parts[-1] or ".spec." in parts[-1]


_AMBIGUOUS_TEST = (
    "{path} is a test file outside every scope and {n} scopes declare templates "
    "({names}). Two routes work: name a SOURCE file from the scope you mean and "
    "give that scope a scoped_tests template with no {{files}} placeholder, which "
    "runs the scope's whole suite; or move the test file under one scope's paths, "
    "where a {{files}} template can name it."
)


def _route_unowned(path: str, templates: dict) -> str:
    """Test directories sit outside every scope by design, so a test file routes
    to the templated scope — unambiguously when there is exactly one."""
    if _is_test_path(path) and len(templates) == 1:
        return next(iter(templates))
    if _is_test_path(path) and templates:
        raise ConfigError(_AMBIGUOUS_TEST.format(path=path, n=len(templates),
                                                 names=", ".join(sorted(templates))))
    raise ConfigError(f"{path} belongs to no declared scope")


def _group_files_by_scope(files, scope_paths: dict, templates: dict) -> dict[str, list[str]]:
    """Route each requested file to its owning scope; unowned or untemplated is a config error."""
    by_scope: dict[str, list[str]] = {}
    for raw in files:
        path = raw.replace("\\", "/")
        owner = _owning_scope(path, scope_paths) or _route_unowned(path, templates)
        if owner not in templates:
            raise ConfigError(f"no [crapkit.scoped_tests] template for scope {owner!r}")
        by_scope.setdefault(owner, []).append(path)
    return by_scope


def _scoped_command(template: str, files: list[str]) -> str:
    """The scope's test command: its template with the quoted file list, or the
    template verbatim when it names no {files}.

    A template without {files} runs the scope's whole suite. That is the coarse
    but working escape for the ordinary layout, where tests live in a top-level
    tests/ directory owned by no scope: substituting a SOURCE file there hands
    pytest a collection target with no tests in it (exit 5).
    """
    if "{files}" not in template:
        return template
    return template.replace("{files}", " ".join(f'"{f}"' for f in files))


def cmd_test_scoped(args: argparse.Namespace) -> int:
    import subprocess

    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    templates = dict(cfg.scoped_tests)
    by_scope = _group_files_by_scope(args.files, cfg.scope_paths, templates)

    for scope, files in sorted(by_scope.items()):
        command = _scoped_command(templates[scope], files)
        proc = subprocess.run(command, shell=True, cwd=root)
        if proc.returncode != 0:
            print(f"crapkit: scoped tests for {scope!r} failed (runner exit {proc.returncode})", file=sys.stderr)
            return 1  # the runner's own code would collide with crapkit's 3/5/6/7/8
    return 0


def _note_stale_staged(root: Path, flagged_paths: set) -> None:
    """A developer who fixed the file but forgot `git add` gets told exactly that.

    The difference is git's to decide, through its own filters: a byte compare
    of the blob against the file called every CRLF checkout stale and sent the
    reader hunting for a staging problem that did not exist.
    """
    from ..gitio import unstaged_paths

    for path in sorted(flagged_paths & unstaged_paths(root)):
        print(f"  note: {path} differs from the working tree — the STAGED blob is "
              "what commits; re-stage with `git add` if you already fixed it.")


def _grant_env_override(root: Path, cfg, violations, reason: str) -> None:
    """The audited hook override: alert line, ratchet debt (staged into the
    pending commit), and a snapshot record — all three or nothing."""
    from ..gitio import head_commit, stage_path
    from ..override import record_override
    from ..verify import GateViolation

    db_path = root / ".crapkit" / "crap.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SnapshotStore(db_path)
    run_id = store.write_run(commit=head_commit(root), tool_versions={}, rows=[],
                             lanes={"_hook_override": {"staged": True}}, kind="hook")
    gate = [GateViolation(v.path, v.long_name, v.start, v.ccn, 0.0,
                          float(v.ccn * v.ccn + v.ccn), "decompose", False, v.key_name)
            for v in violations]
    record_override(store=store, run_id=run_id, root=root, ratchet_file=cfg.ratchet_file,
                    alert_command=cfg.alert_command, violations=gate, reason=reason,
                    raise_marks=False)
    stage_path(root, cfg.ratchet_file)  # the debt must be IN the commit, not dangling
    print(f"crapkit: override granted with full audit ({reason}).")
    print("crapkit: unset CRAPKIT_OVERRIDE_REASON now — while set it grants again on every commit.")


def _warn_unscoped_staged(unscoped: list) -> None:
    """Staged source no scope claims. A staged file ABOVE the crapkit root is
    never named here by design: the gate reads a diff relative to the root, and
    a file outside the root is outside the universe crapkit can score."""
    if unscoped:
        print(f"crapkit gate: {len(unscoped)} staged file(s) belong to no scope and were "
              f"not gated: {', '.join(unscoped)} — add a [[scope]] claiming them "
              "(see docs/configuration.md)", file=sys.stderr)


def _split_marked(violations: list, entries: list) -> tuple[list, list]:
    """Staged violations split into (gated, exempt) on ratchet-mark EXISTENCE.

    Existence, not the `crap > mark` rule `rescore --gate` uses: the gate judges
    staged blobs, a blob carries no coverage, and without coverage there is no
    CRAP to compare against a mark. So the question the hook can answer is the
    only one it asks — did the repo already sign for this function?

    Without it, touching signed debt is a wall. A comment inside one of
    openclaw's 40,303 marked rows refused the commit while `rescore --gate` on
    the same tree passed, so a session of green advisories ended at a red
    commit. `verify` keeps the numeric check and is what catches a mark that
    actually rose.
    """
    from ..keys import stated_key

    marked = {(e.path, e.long_name) for e in entries}
    gated, exempt = [], []
    for v in violations:
        (exempt if stated_key(v) in marked else gated).append(v)
    return gated, exempt


def _note_marked_staged(exempt: list) -> None:
    """One line, never a list. The count says the exemption fired; the marks
    themselves are in the committed TSV, and naming them at every commit would
    reprint debt the repo reads through `crapkit ratchet report`."""
    if exempt:
        print(f"crapkit gate: {len(exempt)} staged function(s) carry a ratchet mark and "
              "were not gated — `crapkit verify` fails a mark that rises", file=sys.stderr)


def _gated_violations(root: Path, cfg, violations: list) -> list:
    """The breaches the commit is actually refused for.

    The marks file is read only once something breached: a clean commit is the
    common case and must not pay to load 40,303 rows it has no question for.

    Through `_load_ratchet_or_die`, so an unparseable marks file names itself
    and exits 3. Reading it raw would end a `git commit` in a traceback, which
    is the one thing a gate on the mandatory path must not do.
    """
    if not violations:
        return []
    entries = _load_ratchet_or_die(root / cfg.ratchet_file, cfg.ratchet_file)
    gated, exempt = _split_marked(violations, entries)
    _note_marked_staged(exempt)
    return gated


def _staged_gate(root: Path, cfg):
    """The gate's verdict, with both git reads started before lizard is imported.

    Neither answer is needed until the import is paid for and the two do not
    depend on each other, so the spawns run underneath it. No git ANSWER is read
    until the analyzer is in hand: a machine without lizard still exits 5 having
    said nothing about the commit.
    """
    from ..gitio import staged_reads

    with staged_reads(root) as reads:
        _analysis_tools()  # importing crapkit.hook reaches lizard too, so it waits its turn
        from ..hook import gate_staged

        return gate_staged(root, cfg, reads)


def cmd_hook_precommit(args: argparse.Namespace) -> int:
    import os

    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    gate = _staged_gate(root, cfg)
    _warn_unscoped_staged(gate.unscoped)
    violations = _gated_violations(root, cfg, gate.violations)
    if not violations:
        return 0
    print(f"crapkit gate: {len(violations)} staged function(s) exceed the complexity ceiling of {cfg.target}:")
    for v in violations:
        print(f"  ccn {v.ccn:>3}  {v.path}:{v.start}  {v.long_name}")
    _note_stale_staged(root, {v.path for v in violations})

    # CRAPKIT_OVERRIDE_REASON is not a bypass: it routes through the full
    # three-record audit and the gate holds unless all three land.
    reason = os.environ.get("CRAPKIT_OVERRIDE_REASON", "").strip()
    if reason:
        _grant_env_override(root, cfg, violations, reason)
        return 0

    print("decompose before committing (coverage cannot save a function above the target).")
    return 6
