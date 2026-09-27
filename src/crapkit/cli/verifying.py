"""The verdict commands: `verify` (baseline pick, lanes, gate/ratchet/failure
evaluation, override audit, claim release), `hook-precommit` (the staged-function
ceiling gate and its audited env override) and `test-scoped` (routing changed
files to their scope's isolated test command). All three answer the same
question at different moments: does this change hold?"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from .. import config, lane_results
from ..errors import ConfigError, CrapkitError, ToolError
from ..invocation import _self
from ..named import first_few
from ..repopath import typed_path
from ..store import SnapshotStore
from ..universe import owning_scope, path_matchers
from ._shared import (_analysis_tools, _command_root, _dirty_tag, _emit_findings, _gate_line,
                      _load_ratchet_or_die, _load_repo_config, _print_json,
                      _ratchet_key_version, _refuse_unwritable_outputs, _repo_out_path,
                      _repo_relative, _say_left_out, _stand, _unreadable_json, _write_tsv,
                      behind_head, no_config, repo_text)
from .scoring import _scored_run

if TYPE_CHECKING:
    from ..ratchet import RatchetDelta, RatchetEntry


def _emit_verify_findings(root: Path, args, verdict, uncovered: list) -> None:
    if not (args.sarif or args.github):
        return
    from ..sarif import diff_uncovered_results, gate_results, regression_results, unread_results

    _emit_findings(root, args.sarif, args.github,
                   gate_results(verdict.gate_violations)
                   + unread_results(verdict.unread_files)
                   + regression_results(verdict.ratchet_regressions)
                   + diff_uncovered_results(uncovered))


def _no_baseline(root: Path, runs: list[dict] = ()) -> str:
    """No trusted run to measure against. When the store holds a partial run,
    the line names the lanes it went without: while one of them keeps failing,
    every `coverage` comes back partial, and "run coverage first" loops."""
    partial = next((r for r in reversed(runs) if r["kind"] == "partial"), None)
    if partial is None:
        return (f"no trusted scored baseline in {root} - run `{_self()} coverage` first "
                "(failed verifies and hook runs never serve as baselines)")
    return (f"no trusted scored baseline in {root}: run {partial['id']} is partial, measured "
            f"without {_missing_lanes(root, partial)} (a lane that failed, or one `--lane` "
            f"left out), and a partial run never serves as a baseline; `{_self()} coverage` "
            "records one once every lane passes")


def _missing_lanes(root: Path, run: dict) -> str:
    """`lane ui` or `lanes a, b`: the declared lanes a run holds no provenance for."""
    missing = [lane.name for lane in _load_repo_config(root).lanes if lane.name not in run["lanes"]]
    return f"lane{'s' if len(missing) > 1 else ''} {', '.join(missing)}"


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
            f"cleared it since - {fallback}, so those findings stay visible. Fix them, or "
            f"pass `--baseline {pick.skipped['id']}` to accept the newer run deliberately.")


def _named_baseline(store: SnapshotStore, root: Path, requested: int) -> dict:
    """`--baseline ID` bypasses the taint rule: naming a run is the deliberate act.
    `ratchet seed` and `ratchet prune` admit a named run by the same rule."""
    from ..store import admit_baseline

    return admit_baseline(store.list_runs(), requested, none_trusted=_no_baseline(root))


def _verify_baseline(root: Path, store: SnapshotStore, requested: int | None, git) -> dict:
    """The trusted run this verify measures against: the newest one behind HEAD
    that the taint rule admits."""
    from ..store import pick_baseline

    if requested is not None:
        return _named_baseline(store, root, requested)
    runs = store.list_runs()
    pick = pick_baseline(runs, behind_head(git))
    if pick.run is None:
        raise _unpicked(root, runs, pick, git)
    if pick.blocker:
        print(f"warning: {_taint_note(pick)}", file=sys.stderr)
    return pick.run


def _unpicked(root: Path, runs: list[dict], pick, git) -> CrapkitError:
    """Why no run behind HEAD can serve: a failure stands in front of every one,
    the store holds no trusted run at all, or every trusted run sits on history
    HEAD does not contain (exit 4, as the ancestor check always gave)."""
    from ..errors import GitError
    from ..store import is_trusted

    if pick.blocker:
        return CrapkitError(_taint_note(pick))
    trusted = [r for r in runs if is_trusted(r)]
    if not trusted:
        return CrapkitError(_no_baseline(root, runs))
    commit = trusted[-1]["commit"]
    newest = f"the newest, run {trusted[-1]['id']} @ {commit[:11]},"
    return GitError(f"no trusted run in {root} is at or behind HEAD; "
                    f"{_not_behind(git, commit, newest)}")


def _require_ancestor(git, commit: str, held=None) -> None:
    """Exit 4 when a named or portable baseline's commit is not behind HEAD.

    `held(commit)` says whether this clone holds the commit at all. `git
    merge-base --is-ancestor` exits 128 on a commit it does not hold, which read
    as "not an ancestor" and was blamed on a rewrite."""
    from ..errors import GitError

    if not git.is_ancestor(commit):
        raise GitError(_not_behind(git, commit, f"baseline commit {commit[:11]}", held))


def _not_behind(git, commit: str, subject: str, held=None) -> str:
    """`SUBJECT is not an ancestor of HEAD` and the thing to blame, in order of
    what git can prove: a shallow clone that never fetched it (fetch deeper), a
    commit this clone does not hold, which a store copied from another clone (a
    CI cache keyed on a branch) can name (fetch it), a branch that holds it and
    HEAD does not (a branch switch: measure this branch), or no branch at all (a
    rebase or an amend rewrote it; an amend keeps the old commit in the object
    store, so that one still reads as a rewrite)."""
    head = f"{subject} is not an ancestor of HEAD"
    if git.is_shallow():
        return (f"{head} in this shallow clone, which does not hold it; set fetch-depth: 0 "
                "on the checkout or run git fetch --unshallow")
    if held is not None and not held(commit):
        return (f"{subject} is not in this clone, so git cannot say whether it is behind HEAD; "
                f"fetch it with `git fetch origin {commit}`, or run `{_self()} coverage` here "
                "for a baseline this clone holds")
    branches = git.branches_containing(commit)
    if branches:
        return (f"{head}: it was made on branch {', '.join(branches[:3])} - run "
                f"`{_self()} coverage` on this branch for a baseline here")
    return f"{head} (rebase or amend rewrote history) - run `{_self()} coverage` for a fresh baseline"


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
            f"no trusted scored run at or behind {basis[:11]} - run `{_self()} coverage` "
            "on the base commit before verifying against it")
    return behind[-1]


def _tsv_baseline(root: Path, rel: str) -> dict:
    """A baseline read from a file the repo carries, for a clone whose .crapkit/
    is gitignored. Its lanes are the test results the file carries: a file
    written before it carried them holds none, so it can neither report a
    shrinking suite nor forgive a failure the baseline run already had, and
    verify says so."""
    from ..verify import parse_baseline_tsv

    path = typed_path(rel, root)
    if not path.is_file():
        raise CrapkitError(f"no baseline file at {path} - write one with `verify --emit-baseline`")
    try:
        parsed = parse_baseline_tsv(repo_text(path, rel))
    except (ValueError, UnicodeError) as exc:
        raise ConfigError(f"unreadable baseline file {rel}: {exc}") from exc
    return {"id": None, "commit": parsed.commit, "kind": parsed.kind,
            "lanes": parsed.lanes, "rows": parsed.rows, "file": rel}


def _pick_baseline(root: Path, store: SnapshotStore, args, basis: str | None, git) -> dict:
    if args.baseline_tsv:
        return _tsv_baseline(root, args.baseline_tsv)
    if basis:
        return _baseline_behind(git, store, basis)
    return _verify_baseline(root, store, args.baseline, git)


def _seed_hint(store: SnapshotStore, args, baseline: dict, git) -> tuple[dict | None, bool]:
    """What a stamp refusal may say about the seed that clears it: the run
    `--baseline ID` named (None without one), and whether a failed verify pins
    a plain `ratchet seed` to an older run (#75).

    Only a run the caller named goes into the refusal. The newer run the taint
    rule passed over lives in this store alone: the Action quotes the refusal in
    a pull request comment, where its id names nothing, and on a runner that
    keeps its workspace it was the pull request head's own run, whose seed would
    sign the breach the failed verify found as the new ceiling. The refusal says
    the seed is pinned; the taint warning and seed's own line name the run on
    the machine that holds it.
    """
    from ..store import pick_baseline

    if args.baseline is not None:
        return baseline, False
    return None, pick_baseline(store.list_runs(), behind_head(git)).skipped is not None


def _verify_basis(root: Path, store: SnapshotStore, args, git) -> tuple[dict, str]:
    """(the baseline record, the commit the diff is measured from).

    --base pins the basis to merge-base(REF, HEAD) instead of the baseline run's
    own commit; without it the two are the same commit and nothing changes.
    """
    from ..gitio import has_commit, merge_base

    basis = merge_base(root, args.base) if args.base else None
    baseline = _pick_baseline(root, store, args, basis, git)
    _require_ancestor(git, baseline["commit"], held=lambda commit: has_commit(root, commit))
    return baseline, basis or baseline["commit"]


def _emit_baseline(root: Path, store: SnapshotStore, baseline: dict, rel: str | None) -> None:
    """Write the baseline this run used as a portable file, before the verdict:
    a run that ends in a failure still owes the operator its basis.

    Through `_repo_out_path`, so `--emit-baseline out/new/b.tsv` creates the
    directory the way `--export`, `--sarif` and `report --out` do."""
    from ..verify import baseline_tsv_lines

    if not rel:
        return

    rows = baseline.get("rows")
    if rows is None:
        rows = store.read_scored(baseline["id"])
    _write_tsv(_repo_out_path(root, rel, "--emit-baseline"),
               baseline_tsv_lines(baseline["commit"], baseline["kind"], rows,
                                  lane_results.portable_results(baseline)))


def _verify_store(root: Path, tsv_baseline: str | None) -> SnapshotStore:
    """A fresh clone has no .crapkit/ at all. With a portable baseline the store
    is created here, since this run is the first thing that will ever write it."""
    db_path = root / ".crapkit" / "crap.sqlite"
    if not (db_path.is_file() or tsv_baseline):
        raise CrapkitError(f"no baseline snapshot in {root} - run `{_self()} coverage` first")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SnapshotStore(db_path)


def _guard_ratchet_stamp(saved, name: str, named: dict | None = None, pinned: bool = False) -> None:
    """Refuse to weigh fresh scores against marks another metric produced.

    Runs before the lanes do: a metric bump that silently kept 40k old marks is
    what this exists to stop, and finding out after a 40-minute run is too late.
    It runs after the baseline is read, so `named`, the run `--baseline ID`
    names, can be the run the refusal says to seed from, and `pinned` can say
    a failed verify holds a plain seed on an older run.
    """
    from ..ratchet import coverage_then_seed, metric_version

    if saved.blank:
        return
    if not saved.metric_stamp:
        print(f"warning: {name} carries no metric stamp (written before stamping) - "
              f"{coverage_then_seed()} to stamp it", file=sys.stderr)
        return
    conflict = saved.stamp_conflict(metric_version())
    if conflict:
        raise ConfigError(_stamp_refusal(conflict, named, pinned))


# Why the stock remedy alone would loop on a store a failed verify pins.
_PINNED_SEED = ("; a failed verify in this store pins a plain seed to an older run, and "
                "seed's line then names the newer run to read instead")

class _JudgedMarks(NamedTuple):
    """The marks verify judges against, and the commit that held them: None when
    they are the file on the tree."""
    marks: object
    commit: str | None


def _judged_marks(root: Path, saved, baseline: dict, name: str) -> _JudgedMarks:
    """The marks verify judges against: the file on disk, or, when it is missing
    or blank, the newest marks the history from the baseline's commit to HEAD
    committed.

    A deleted or emptied marks file read as a repo that never marked any debt,
    so a marked function whose CRAP rose passed with exit 0. Marks are usually
    seeded after the baseline run, so the baseline's own commit is not enough.
    The stand-in is only judged against; verify never writes it back. A history
    the clone cannot read refuses with GitError, exit 4, since judging against
    no marks would pass the rise the stand-in exists to catch.
    """
    from ..marks_history import newest_committed_marks

    found = newest_committed_marks(root, baseline["commit"], name) if saved.blank else None
    if found is None:
        return _JudgedMarks(saved, None)
    commit, committed = found
    _warn_marks_stand_in(saved, committed, commit[:11], name)
    return _JudgedMarks(committed, commit)


def _warn_marks_stand_in(saved, committed, commit: str, name: str) -> None:
    state = "missing" if saved.text is None else "empty"
    print(f"warning: {name} is {state}, but commit {commit}, the newest since the baseline to "
          f"hold it, has {len(committed.entries)} mark(s); verify judged against those and left "
          f"{name} as it is. Restore it with `git checkout {commit} -- {name}`, or drop the "
          f"marks of code that is gone with `{_self()} ratchet prune`", file=sys.stderr)


def _stamp_refusal(conflict: str, named: dict | None, pinned: bool = False) -> str:
    """The stamp refusal, with what it takes to clear it on this store.

    The stock remedy's `ratchet seed` reads the run verify would pick. A failed
    verify can pin that to a run an older crapkit measured, or one written
    before same-line positions, and seed then keeps the old stamp or refuses
    outright, so the remedy alone led back to this refusal (#75). `named` is
    the run `--baseline ID` named, the seed to name back; `pinned` adds that a
    failed verify holds the plain seed. Marks a newer crapkit wrote carry no
    seed remedy at all: an upgrade clears them, and a seed would restamp them
    backwards.
    """
    from ..ratchet import coverage_then_seed

    stock = coverage_then_seed()
    if not conflict.endswith(stock):
        return conflict
    if named is not None:
        return conflict.removesuffix(stock) + _named_seed(named)
    return conflict + (_PINNED_SEED if pinned else "")


def _named_seed(named: dict) -> str:
    """The seed that re-baselines the marks from the run verify was handed.

    Seed stamps the metric of the run it reads, so a run another metric measured
    needs a coverage run first, and that run is the one to name.
    """
    from ..ratchet import metric_version, run_stamp

    run_id = named["id"]
    if run_stamp(named["tool_versions"]) == metric_version():
        return f"re-baseline from run {run_id} with `{_self()} ratchet seed --baseline {run_id}`"
    return (f"run {run_id} was measured under another metric too, so run `{_self()} coverage`, "
            f"then re-baseline from the run it writes with `{_self()} ratchet seed --baseline ID`")


def _override_applies(verdict, reason: str | None) -> bool:
    """--override grants pure gate violations: a reason, a failed verdict, gate
    violations, and none of the findings that never qualify."""
    return (bool(reason) and not verdict.ok and bool(verdict.gate_violations)
            and not _refusal_parts(verdict))


def _refused_cause(count: int, noun: str, first: str) -> str:
    """`1 ratchet regression (app/m.py pick( a ) 10.75 -> 20.0)`: the count, and
    the first one named so the line stands on its own in a CI log."""
    return f"{count} {noun}{'' if count == 1 else 's'} ({first})"


def _regression_cause(regressions) -> str:
    r = regressions[0]
    return _refused_cause(len(regressions), "ratchet regression",
                          f"{r.path} {r.long_name} {r.recorded} -> {r.fresh_crap}")


def _failure_cause(failures) -> str:
    return _refused_cause(len(failures), "new test failure", failures[0])


def _unread_cause(unread) -> str:
    return _refused_cause(len(unread), "unread file", f"{unread[0].path}: {unread[0].reason}")


def _never_granted() -> tuple:
    """Each finding kind an override never grants, how to name it, and its escape.

    The escape for a regression is the only one there is; verify never raises a
    mark, and the override path cannot reach a marked function without also
    seeing its regression (docs/ratchet.md, Overrides and the audit trail). An
    unread file holds no function to record as debt, and granting the functions
    beside it would sign debt while the gate still refuses the file.
    """
    from ..merge import UNREAD_ADVICE

    return (("ratchet_regressions", _regression_cause, "raise the mark by hand and commit it"),
            ("new_failures", _failure_cause, "fix the failing test first"),
            ("unread_files", _unread_cause, UNREAD_ADVICE))


def _refusal_parts(verdict) -> list[tuple[int, str, str]]:
    """(count, cause, escape) for each finding kind present that no override grants."""
    return [(len(getattr(verdict, kind)), cause(getattr(verdict, kind)), escape)
            for kind, cause, escape in _never_granted() if getattr(verdict, kind)]


def _override_refusal(verdict) -> str | None:
    """Why a refused --override did not apply, or None when nothing disqualified it.

    Every cause on one line, each with its own escape: a run holding a
    regression and a new failure is refused once, not twice.
    """
    parts = _refusal_parts(verdict)
    if not parts:
        return None
    counts, causes, escapes = zip(*parts)
    verb = "qualifies" if sum(counts) == 1 else "qualify"
    return (f"override refused: {' and '.join(causes)} never {verb} for an override; "
            f"{'; '.join(escapes)}")


def _require_override_reason(reason: str | None) -> None:
    """Refuse an empty or blank --override before any lane runs.

    `--override ""` was falsy, so it ran as a plain verify and recorded the
    failure it was meant to grant; a blank one was refused only after the lanes,
    by the audit. The audit keeps its own check for the hook's grant.
    """
    if reason is not None and not reason.strip():
        raise ConfigError("an override requires a non-empty reason")


def _refuse_override(verdict, reason: str | None) -> None:
    """One stderr line when a reason was given and something disqualified it.

    stderr, because `--json` prints one object on stdout. Printed after the
    verdict's own lines, so a terminal reads the findings first and then why
    the override did not take. A passing run (an override that applied
    included), or a run with no reason, prints nothing: nothing was refused.
    """
    refusal = _override_refusal(verdict) if reason and not verdict.ok else None
    if refusal:
        print(refusal, file=sys.stderr)


def _apply_verify_override(store: SnapshotStore, run_id: int, root: Path, cfg, verdict, reason,
                            *, key_version: int | None = None, identity_rows=None,
                            ratchet_input=None):
    """Grant --override for pure gate violations; regressions and new failures
    never qualify (`_refuse_override` says so once the verdict is printed)."""
    from ..override import record_override
    from ..ratchet import metric_version
    from ..verify import settle_verdict

    if not _override_applies(verdict, reason):
        return verdict
    record_override(store=store, run_id=run_id, root=root, ratchet_file=cfg.ratchet_file,
                    alert_command=cfg.alert_command, violations=verdict.gate_violations,
                    reason=reason, key_version=key_version, identity_rows=identity_rows,
                    ratchet_input=ratchet_input, metric=metric_version())
    overridden = verdict.gate_violations
    return settle_verdict(verdict._replace(gate_violations=[], overridden=tuple(overridden)))


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


def _write_marks_if_changed(saved, prior: list[RatchetEntry],
                            updated: list[RatchetEntry], *, key_version: int | None = None) -> RatchetDelta | None:
    """Rewrite the marks file only when its text would change, and never create
    one to hold zero marks. Returns what the write did, or None when the file
    was left alone.

    A clean checkout used to end every green verify with an untracked marks
    file holding a stamp, a header and no rows, and a repo with marks got a
    byte-identical rewrite whose mtime alone made it look touched. The tighten
    can only drop or lower marks, so a marks file that does not exist has
    nothing to write, and a text that matches the disk has nothing to say. An
    emptied file is left empty too: restamped into a header with no rows, it
    asked for a `git add` that committed the lost marks as a valid empty file.
    """
    from ..ratchet import metric_version, ratchet_delta

    if saved.blank:
        return None
    if not saved.publish(saved.measured(updated, metric_version(), keys=key_version)):
        return None
    return ratchet_delta(prior, updated)


def _settle_verify(store: SnapshotStore, run_id: int, verdict,
                   saved, ratchet, scored, cfg, *, args,
                   commit: str, key_version: int | None = None) -> RatchetDelta | None:
    """Stamp the verdict; a clean pass (not an override) tightens the ratchet.
    Returns the tighten's counts, or None when the tighten wrote nothing.

    `--no-tighten` is the blunt escape: the verdict still stands, the marks file
    is simply not rewritten.
    """
    from ..ratchet import update_ratchet
    from ..verify import dirty_counts

    changes = None
    if verdict.ok and not verdict.overridden and not args.no_tighten:
        hold = _held_marks(store, cfg, commit, run_id, ratchet, scored)
        updated = update_ratchet(ratchet, scored, target=cfg.target,
                                 scope_targets=cfg.scope_targets, hold=hold)
        changes = _write_marks_if_changed(saved, ratchet, updated, key_version=key_version)
    store.set_verdict_ok(run_id, verdict.ok, findings=sum(dirty_counts(verdict)))
    return changes


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


def _print_gate_findings(verdict) -> None:
    from ._shared import _unread_line

    for v in verdict.gate_violations:
        print(_gate_line(v))
    for u in verdict.unread_files:
        print(_unread_line(u.path, u.reason, u.dirty))


def _print_verify_findings(verdict) -> None:
    dirty_ids = set(verdict.dirty_failures)
    _print_gate_findings(verdict)
    for r in verdict.ratchet_regressions:
        print(f"  RATCHET  {r.path}  {r.long_name}: {r.recorded} -> {r.fresh_crap}{_dirty_tag(r.dirty)}")
    for f in verdict.new_failures:
        print(f"  NEW FAILURE  {f}{_dirty_tag(f in dirty_ids)}")
    for v in verdict.overridden:
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


# One exit code per finding kind, in the order the first one present decides.
_EXIT_ORDER = (("gate_violations", 6), ("unread_files", 6), ("ratchet_regressions", 7),
               ("new_failures", 8), ("uncovered_violations", 9))


def _verify_exit_code(verdict) -> int:
    return next((code for kind, code in _EXIT_ORDER if getattr(verdict, kind)), 0)


def _warn_diff_cover_breach(verdict, maximum: int | None) -> None:
    if verdict.uncovered_violations:
        print(f"diff coverage: {len(verdict.uncovered_violations)} uncovered changed line(s) "
              f"over the ceiling {maximum}", file=sys.stderr)


def _test_files(root: Path, failures: set[str]) -> dict[str, str]:
    """Each failing test's file part as git spells the file, read off the disk
    here so the verdict stays a pure function of its inputs."""
    from ..repopath import Reported
    from ..verify import file_part

    reported = Reported(root)
    return {part: reported(part) for part in {file_part(f) for f in failures}}


class _RunsBehind:
    """Trusted runs older than the baseline and at or behind its commit, newest
    first: where a lane the baseline recorded no test results for is looked up.

    Read the first time a lane asks, so a baseline that recorded every lane
    costs no store read and no git call. A baseline file has no run behind it.
    """

    def __init__(self, store: SnapshotStore, git, baseline: dict):
        self._store, self._git, self._baseline = store, git, baseline
        self._older: list[dict] | None = None

    def __call__(self):
        commit = self._baseline["commit"]
        return (run for run in self._runs() if self._git.is_ancestor(run["commit"], commit))

    def _runs(self) -> list[dict]:
        if self._older is None:
            self._older = self._read()
        return self._older

    def _read(self) -> list[dict]:
        from ..store import trusted_runs

        if self._baseline["id"] is None:
            return []
        newest_first = reversed(trusted_runs(self._store))
        return [run for run in newest_first if run["id"] < self._baseline["id"]]


def _warn_baseline_gaps(found: lane_results.BaselineFailures, baseline: dict,
                        provenance: dict, new_failures) -> list[str]:
    """Say where a lane's baseline failures came from when the baseline held no
    list for it, and which lanes' new failures nothing recorded could judge.
    Returns those lanes.

    A failure the baseline cannot vouch for still counts as new, because a gate
    fails closed; the line is what keeps that from reading as this change's doing.
    """
    unjudged = lane_results.unjudged_lanes(found, provenance, new_failures)
    if _warn_baseline_file(baseline, provenance):
        return unjudged
    for name, run in found.borrowed.items():
        print(f"warning: lane {name!r}: baseline run {baseline['id']} "
              f"{_why_unread(baseline, name)}, so its failures are compared with run "
              f"{run['id']}'s", file=sys.stderr)
    for name in unjudged:
        print(_unjudged_line(name, baseline, provenance, new_failures), file=sys.stderr)
    return unjudged


def _why_unread(baseline: dict, name: str) -> str:
    """Why the baseline's own failure list for a lane was passed over."""
    if lane_results.distrusted_list(baseline, name):
        return (f"was written by crapkit {baseline['tool_versions']['crapkit']}, which kept a "
                "failure that passed its flake retry in its failure list")
    return "recorded no test results"


def _unjudged_line(name: str, baseline: dict, provenance: dict, new_failures) -> str:
    count = len(set(new_failures) & lane_results.recorded_failures(provenance, name))
    where = (f"the baseline file {baseline['file']} holds no record of" if baseline.get("file")
             else "no trusted run at or behind the baseline recorded")
    return (f"warning: lane {name!r}: {where} which of its tests failed, so its {count} new "
            f"failure{'' if count == 1 else 's'} may predate this change; a baseline measured "
            "with results_artifact declared tells them apart")


def _warn_baseline_file(baseline: dict, provenance: dict) -> bool:
    """A baseline file written before files carried test results: said once,
    when a lane this run recorded results the file cannot be compared with."""
    stale = bool(baseline.get("file")) and not baseline["lanes"] \
        and any(map(lane_results.lists_failures, provenance.values()))
    if stale:
        print(f"warning: the baseline file {baseline['file']} holds no test results, so every "
              "test failure counts as new and no suite size is compared; write it again with "
              f"`{_self()} verify --emit-baseline {baseline['file']}` to carry them",
              file=sys.stderr)
    return stale


def _refuse_unreadable_junits(lanes, provenance: dict) -> None:
    """Refuse a verdict over a declared junit this run reused and could not read.

    A lane that runs refuses that report and exits 5. Under `--reuse-artifacts`
    the lane only warns, which suits `coverage`, since scoring off a salvaged
    coverage file is its job. verify then passed with no test checked, stored
    the run as a passing verify, and made it the next baseline. So it exits 5
    here, before anything is stored, as a real run does. A lane that declares
    no junit had nothing to read and still passes, under `lanes_without_results`.
    """
    unreadable = _unreadable_junits(lanes, provenance)
    if unreadable:
        raise ToolError("; ".join(_unreadable_junit_line(lane) for lane in unreadable))


def _unreadable_junits(lanes, provenance: dict) -> list:
    """The lanes, in declaration order, that declare a `results_artifact` and
    recorded no failure list: this run reused a junit it could not read. A lane
    that ran refuses that report itself, and a lane that declares none has no
    report to read, so neither is named here."""
    unrecorded = set(lane_results.without_results(provenance))
    return [lane for lane in lanes if lane.results_artifact and lane.name in unrecorded]


def _unreadable_junit_line(lane) -> str:
    return (f"lane {lane.name!r} declares results_artifact {lane.results_artifact}, which "
            "this verify reused and could not read, so no test in it was checked for a new "
            "failure; run verify without --reuse-artifacts so the lane writes it again")


def _warn_unseen_failures(lanes, provenance: dict) -> None:
    """A lane with no results_artifact that exited nonzero: its exit code is
    recorded, not enforced, and it is the only sign a test failed."""
    for lane in lanes:
        code = provenance.get(lane.name, {}).get("exit_code")
        if code and not lane.results_artifact:
            print(f"warning: lane {lane.name!r} exited {code} and declares no results_artifact, "
                  "so verify cannot see which of its tests failed; declare results_artifact "
                  "(the lane's junit report) to check them", file=sys.stderr)


def _stored_lanes(provenance: dict, retried: tuple[str, ...]) -> dict:
    """Lane provenance as a verify run stores it: `failures` stays the lane's
    own report, and the ones that passed their flake retry are named again."""
    return {name: _with_retried(prov, retried) for name, prov in provenance.items()}


def _with_retried(prov: dict, retried: tuple[str, ...]) -> dict:
    passed = [f for f in sorted(lane_results.read_results(prov).failures or ()) if f in retried]
    return {**prov, "retried_passes": passed} if passed else prov


def _verify_attribution(verdict) -> dict:
    from ..verify import dirty_counts

    committed, dirty = dirty_counts(verdict)
    return {"committed_findings": committed, "dirty_findings": dirty,
            "dirty_failures": list(verdict.dirty_failures)}


_RECORD_FINDINGS = ("gate_violations", "unread_files", "ratchet_regressions", "overridden")


def _finding_lists(verdict) -> dict:
    """The finding kinds whose entries are records, each as a list of objects."""
    return {kind: [f._asdict() for f in getattr(verdict, kind)] for kind in _RECORD_FINDINGS}


def _verify_result(verdict, run_id: int, baseline: dict, commit: str, ranges,
                   uncovered: list, diff_uncovered_max: int | None,
                   unmarked_over_target: int) -> dict:
    """`diff_uncovered_max` travels with the count it judges: a reader of exit 9
    (the Action's comment) can say which ceiling the lines went over.
    `unmarked_over_target` is the standing debt no mark covers (see
    `_warn_standing_debt`); it fires no exit code and is the one number that
    says how much of the tree the ratchet is not holding."""
    return {
        "ok": verdict.ok,
        "run_id": run_id,
        "baseline_run": baseline["id"],
        "baseline_commit": baseline["commit"],
        "commit": commit,
        "changed_files": len(ranges),
        "changed_paths": sorted(ranges),
        **_finding_lists(verdict),
        "new_failures": verdict.new_failures,
        "forgiven_failures": list(verdict.forgiven_failures),
        "retried_passes": list(verdict.retried_passes),
        "diff_uncovered_count": len(uncovered),
        "diff_uncovered": [{"path": p, "line": ln} for p, ln in uncovered[:50]],
        "diff_uncovered_max": diff_uncovered_max,
        "unmarked_over_target": unmarked_over_target,
        **_verify_attribution(verdict),
    }


def _warn_standing_debt(unmarked: list) -> None:
    """One stderr line for the over-ceiling functions no mark covers.

    The gate never looks at an untouched function and the ratchet check compares
    marks only, so a rise on these (coverage loss included) reaches no finding;
    docs/ratchet.md says "seed once, early" for exactly this, and a header-only
    marks file cannot say whether seed ever ran. stderr, because
    `--json` prints one object on stdout. Silent at zero, which is the state
    of a repo with no debt and of one seeded in full.
    """
    if not unmarked:
        return
    named = first_few([f"{row.path} {row.long_name}" for row in unmarked])
    print(f"warning: {len(unmarked)} function(s) over the ceiling carry no ratchet mark "
          f"({named}), so a rise on them (coverage loss included) passes unseen; record them "
          f"with `{_self()} ratchet seed`", file=sys.stderr)


def _untracked_in_scope(root: Path, cfg) -> list[str]:
    """Source files a scope would score if git tracked them.

    verify's diff and its corpus hold git-tracked files only, so a new file
    nobody added was judged as nothing and read as `(0 changed files)`. Asked
    before any lane runs, like the dirty set: a file a lane writes is the
    lane's output, not somebody's unjudged work.

    Each name is asked which scope takes it, not scanned: the scan refuses a
    claimed name that is not UTF-8, and verify judges no untracked file, so
    such a name is named here in its `\\xNN` spelling like any other."""
    from ..gitio import untracked_files
    from ..gitpaths import shown
    from ..universe import claiming_scope

    return sorted(shown(path) for path in untracked_files(root) if claiming_scope(path, cfg))


def _warn_untracked_in_scope(untracked: list[str]) -> None:
    """stderr, because `--json` prints one object on stdout; the paths are in
    it as `untracked_in_scope`."""
    if untracked:
        print(f"warning: {len(untracked)} untracked file(s) in a scope were not judged "
              f"({first_few(untracked)}): verify scores git-tracked files only; `git add` "
              "them to have them judged", file=sys.stderr)


def _warn_diff_uncovered(uncovered: list) -> None:
    if not uncovered:
        return
    print(f"warning: {len(uncovered)} changed line(s) have no coverage", file=sys.stderr)
    for path, line in uncovered[:20]:
        print(f"  uncovered {path}:{line}", file=sys.stderr)


def _receipt(tool_versions: dict, saved, judged: _JudgedMarks,
             changes: RatchetDelta | None) -> dict:
    """What produced the verdict and what the run did to the marks file: the
    tool versions, the marks file on the tree as read (hashed before any
    tighten, so the receipt names the input; null when the tree has none), the
    marks verify judged against (`ratchet_source` "tree", or "committed" with
    the commit that held them and their digest), and the tighten's counts, null
    when this run's tighten wrote nothing (a failed run, --no-tighten, nothing
    to move). An override's grant is its own write and is listed under
    `overridden`, not counted here."""
    return {"tool_versions": tool_versions, "ratchet_sha256": saved.sha256,
            "ratchet_source": "committed" if judged.commit else "tree",
            "ratchet_source_commit": judged.commit,
            "ratchet_source_sha256": judged.marks.sha256,
            "ratchet_changes": None if changes is None else changes._asdict()}


def _marks_moved(changes: dict) -> str:
    """`6 dropped, 1 tightened`, or `restamped` for the one rewrite that moves
    no mark: a file written before stamping gains its stamp line."""
    if changes["dropped"] or changes["tightened"]:
        return f"{changes['dropped']} dropped, {changes['tightened']} tightened"
    return "restamped"


def _ratchet_suffix(changes: dict | None, overridden: list, ratchet_file: str) -> str:
    """The OK line's tail when the run wrote the marks file, by a tighten or by
    an override's grant. The `git add` is the point: a dirty marks file after
    a green run was a surprise before."""
    if overridden:
        plural = "" if len(overridden) == 1 else "s"
        return f" ratchet: {len(overridden)} mark{plural} granted -> git add {ratchet_file}"
    if changes is None:
        return ""
    return f" ratchet: {_marks_moved(changes)} -> git add {ratchet_file}"


def _counted(ids, noun: str, verb: str) -> str:
    """` (2 unchanged failures forgiven, first a::b)`, or nothing for no ids."""
    if not ids:
        return ""
    plural = "" if len(ids) == 1 else "s"
    return f" ({len(ids)} {noun}{plural} {verb}, first {ids[0]})"


def _forgiven_suffix(out: dict) -> str:
    """Failures this verdict forgives because the baseline carries them too.

    A regression verdict is about change, so an unchanged failure is not a
    regression. Saying nothing about it made `verify OK` read as a clean suite
    beside three failing tests, and the release guard downstream, which does not
    forgive them, then refused evidence the operator had just watched pass."""
    return _counted(out.get("forgiven_failures") or (), "unchanged failure", "forgiven")


def _retried_suffix(out: dict) -> str:
    """New failures that passed their flake retry, in words of their own.

    The baseline never failed them, so calling them unchanged was wrong. The
    stored run still records the failed first attempt, and the release guard
    refuses a lane that failed any test, so the OK line has to say one did."""
    return _counted(out.get("retried_passes") or (), "new failure", "passed on rerun")


def _report_verify(as_json: bool, out: dict, verdict, ratchet_file: str) -> None:
    if as_json:
        _print_json(out)
        return
    state = "OK" if verdict.ok else "FAILED"
    print(f"verify {state} @ {out['commit'][:11]} vs baseline {out['baseline_commit'][:11]} "
          f"({out['changed_files']} changed files)"
          f"{_forgiven_suffix(out)}{_retried_suffix(out)}"
          f"{_ratchet_suffix(out['ratchet_changes'], verdict.overridden, ratchet_file)}")
    _print_changed_paths(out["changed_paths"])
    _print_verify_findings(verdict)
    _print_finding_split(verdict)


def _print_changed_paths(paths: list[str]) -> None:
    """The files behind the count, on their own line so the verdict line keeps
    its shape. Nothing for an empty diff."""
    if paths:
        print(f"  changed files: {first_few(paths)}")


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
                          " - coverage is half the verdict; a scope no coverage parser can read "
                          "declares coverage_optional = true instead")


def cmd_verify(args: argparse.Namespace) -> int:
    from ..diffparse import changed_ranges
    from ..gitio import GitFacts, diff_since
    from ..uncovered import missing_by_path
    from ..verify import (diff_uncovered, evaluate, unmarked_over_ceiling, with_diff_coverage,
                          with_unread)
    from ..ratchetfile import RatchetFile
    from ._shared import _check_ratchet_identity

    _require_override_reason(args.override)
    root = _command_root(args.repo)
    cfg = _load_repo_config(root)
    _refuse_lane_less_verify(cfg)
    _refuse_unwritable_outputs(root, {"--sarif": args.sarif, "--emit-baseline": args.emit_baseline})
    store = _verify_store(root, args.baseline_tsv)
    saved = RatchetFile.read(root / cfg.ratchet_file)
    # One context for the whole command: the ancestry checks, the lane runner and
    # this attribution all used to spawn their own git. Asking here also FIXES the
    # dirty set before any lane command runs, so a lane writing into a tracked file
    # cannot enlarge the set this verdict blames on somebody else.
    git = GitFacts(root)
    dirty = set(git.status_names())
    untracked = _untracked_in_scope(root, cfg)
    baseline, basis = _verify_basis(root, store, args, git)
    judged = _judged_marks(root, saved, baseline, cfg.ratchet_file)
    _guard_ratchet_stamp(judged.marks, cfg.ratchet_file, *_seed_hint(store, args, baseline, git))
    _emit_baseline(root, store, baseline, args.emit_baseline)

    # Corpus and cache_hits are coverage's report line, not verdict inputs.
    run = _scored_run(root, cfg, list(cfg.lanes), reuse_artifacts=args.reuse_artifacts,
                      reuse_unchanged=args.reuse_unchanged, git=git)
    commit, scored, provenance = run.commit, run.scored, run.provenance
    tool_versions, fresh_failures = run.tool_versions, run.test_failures
    if run.lane_errors:
        raise ToolError(f"verify cannot conclude with failed lanes: {'; '.join(run.lane_errors)}")
    _refuse_unreadable_junits(cfg.lanes, provenance)

    ranges = changed_ranges(diff_since(root, basis))
    _warn_untracked_in_scope(untracked)
    ratchet = judged.marks.entries
    key_version = _check_ratchet_identity(judged.marks.text or "", root, cfg.ratchet_file,
                                          scored, store, entries=ratchet)

    behind = _RunsBehind(store, git, baseline)
    found = lane_results.baseline_failures(baseline, provenance, behind)
    verdict = evaluate(fresh=scored, changed_ranges=ranges, ratchet=ratchet,
                       baseline_failures=set(found.carried), fresh_failures=fresh_failures,
                       target=cfg.target, scope_targets=cfg.scope_targets, dirty_paths=dirty,
                       test_files=_test_files(root, fresh_failures))
    verdict = _maybe_flake_retry(root, cfg, provenance, verdict)
    unjudged = _warn_baseline_gaps(found, baseline, provenance, verdict.new_failures)
    _warn_suite_shrink(baseline, provenance, behind)
    _warn_unseen_failures(cfg.lanes, provenance)
    # diff_uncovered walks the changed ranges, so an empty diff is [] whatever
    # the artifacts say — and reading every lane's artifact to spell that [] is
    # the whole cost of the post-commit verify on an unchanged tree.
    uncovered = diff_uncovered(ranges, missing_by_path(root, cfg, folded=run.dead_lines),
                               scored) if ranges else []
    _warn_diff_uncovered(uncovered)
    unmarked = unmarked_over_ceiling(scored, ratchet, cfg.target, cfg.scope_targets)
    _warn_standing_debt(unmarked)
    verdict = with_diff_coverage(verdict, uncovered, cfg.diff_uncovered_max, dirty)
    verdict = with_unread(verdict, run.corpus.unread, set(ranges) | dirty, dirty)
    _warn_diff_cover_breach(verdict, cfg.diff_uncovered_max)
    run_id = store.write_run(commit=commit, tool_versions=tool_versions, rows=scored,
                             lanes=_stored_lanes(provenance, verdict.retried_passes), kind="verify",
                             sources=run.sources)
    verdict = _apply_verify_override(store, run_id, root, cfg, verdict, args.override,
                                     key_version=key_version, identity_rows=scored,
                                     ratchet_input=saved)
    changes = _settle_verify(store, run_id, verdict, saved, ratchet, scored,
                             cfg, args=args, commit=commit, key_version=key_version)
    _release_claims(store, git, cfg, scored)
    _emit_verify_findings(root, args, verdict, uncovered)

    _report_verify(args.json,
                   {**_verify_result(verdict, run_id, baseline, commit, ranges,
                                     uncovered, cfg.diff_uncovered_max, len(unmarked)),
                    **_receipt(tool_versions, saved, judged, changes),
                    "lanes_without_results": lane_results.without_results(provenance),
                    "lanes_without_baseline_results": unjudged,
                    "unreadable_names": _unreadable_json(run.corpus.unreadable),
                    "untracked_in_scope": untracked},
                   verdict, cfg.ratchet_file)
    _refuse_override(verdict, args.override)
    return _verify_exit_code(verdict)


def _flake_retry(root: Path, cfg, provenance: dict, new_failures: set) -> set:
    """Rerun just the newly-failed ids in lanes that declare retest_command.
    An id leaves the survivors only when every lane that failed it reran it
    and the rerun passed: a lane without retest_command keeps its failures,
    whatever another lane's rerun said about the same id."""
    passed, kept = set(), set()
    for lane in cfg.lanes:
        lane_new = lane_results.recorded_failures(provenance, lane.name) & new_failures
        cleared = _rerun_passes(root, lane, lane_new)
        passed |= cleared
        kept |= lane_new - cleared
    return new_failures - (passed - kept)


def _rerun_passes(root: Path, lane, tests: set) -> set:
    """The ids this lane's rerun passed; none when it failed nothing new or
    declares no retest_command."""
    from ..lanes import retest_lane

    if not tests or not lane.retest_command:
        return set()
    return retest_lane(root, lane, tests)


def _maybe_flake_retry(root: Path, cfg, provenance: dict, verdict):
    from ..verify import settle_flake_retry

    if not verdict.new_failures:
        return verdict
    survivors = _flake_retry(root, cfg, provenance, set(verdict.new_failures))
    if len(survivors) == len(verdict.new_failures):
        return verdict
    print(f"flake retry: {len(verdict.new_failures) - len(survivors)} of "
          f"{len(verdict.new_failures)} new failures passed on rerun", file=sys.stderr)
    return settle_flake_retry(verdict, survivors)


def _warn_suite_shrink(baseline: dict, provenance: dict, behind=tuple) -> None:
    """Suite decay passes a pass/fail check silently; say it out loud.

    A lane the baseline counted no tests for is compared with the newest run
    `behind` it that counted them, and the line names that run. A trusted run
    with no count (its junit was gone under `--reuse-artifacts`, or the lane
    declared no `results_artifact` then) once left the next verify comparing
    nothing, so a suite that fell from 20 tests to 2 passed without a word.
    """
    for name, prov in provenance.items():
        source = lane_results.counted_record(baseline, behind, name)
        for line in _suite_size_lines(name, source, baseline, prov):
            print(f"warning: {line}", file=sys.stderr)


def _suite_size_lines(name: str, source: dict | None, baseline: dict, prov: dict) -> list[str]:
    """How the suite's size moved since the run that last counted it, or why
    that cannot be said.

    No run counted the lane: nothing to compare. This run wrote no junit (the
    lane declares no `results_artifact` at the commit under test, or
    `--reuse-artifacts` read one it could not check; a declared file missing
    after a real run is a lane failure and never reaches here): one line naming
    the gap. Reading that absent count as zero once turned every such run into
    a KeyError after the lane had run.
    """
    if source is None:
        return []
    then, now = lane_results.results_of(source, name), lane_results.read_results(prov)
    noun, note = _count_source(source, baseline)
    if now.tests is None:
        return [f"lane {name!r} wrote no test counts this run (no results_artifact was parsed), "
                f"so {noun}'s {then.tests} tests cannot be compared{note}"]
    lines = (_fewer_tests_line(name, then.tests, now.tests),
             _more_skips_line(name, then.skipped, now.skipped))
    return [f"{line} than {noun}{note}" for line in lines if line]


def _count_source(source: dict, baseline: dict) -> tuple[str, str]:
    """What a suite-size line compares with: the baseline, or the older run
    that counted the lane when the baseline did not."""
    if source is baseline:
        return "the baseline", ""
    return (f"run {source['id']}",
            f" (baseline run {baseline['id']} recorded no test count for it)")


def _fewer_tests_line(name: str, base_n: int, fresh_n: int) -> str | None:
    if fresh_n < base_n:
        return f"lane {name!r} runs {base_n - fresh_n} fewer tests"
    return None


def _more_skips_line(name: str, base_n: int | None, fresh_n: int | None) -> str | None:
    if base_n is not None and fresh_n is not None and fresh_n > base_n:
        return f"lane {name!r} skips {fresh_n - base_n} more tests"
    return None


def _owning_scope(path: str, scope_paths: dict[str, tuple[str, ...]]) -> str | None:
    """The scope matching deepest, so a nested scope wins over the parent that also contains it.

    universe owns the rule. Respelling it here answered `a` where the scored
    corpus answered `b` for the same nested path, and `brief` then took a
    function's lane and test command from one scope and its ceiling from the
    other. Extension-blind, because a test file's language need not be one any
    scope declares.
    """
    return owning_scope(path, path_matchers(scope_paths))


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


def _group_files_by_scope(files, scope_paths: dict, templates: dict,
                          root: Path = Path("."), cwd: Path | None = None) -> dict[str, list[str]]:
    """Route each requested file to its owning scope; unowned or untemplated is a config error.

    Every spelling of a path arrives here as the one the scopes are declared in,
    because `owning_scope` matches on a prefix and `./src/a.py` shares none with
    `src`; a file said from `cwd` below the root is placed under the root first."""
    by_scope: dict[str, list[str]] = {}
    for raw in files:
        path = _repo_relative(raw, root, cwd)
        owner = _owning_scope(path, scope_paths) or _route_unowned(path, templates)
        if owner not in templates:
            raise ConfigError(f"no [crapkit.scoped_tests] template for scope {owner!r}")
        by_scope.setdefault(owner, []).append(path)
    return by_scope






def cmd_test_scoped(args: argparse.Namespace) -> int:
    import os
    from ..procs import own_processes, prepare_template, run_owned

    root = _command_root(args.repo)
    cfg = _load_repo_config(root)
    templates = dict(cfg.scoped_tests)
    by_scope = _group_files_by_scope(args.files, cfg.scope_paths, templates, root,
                                     cwd=_stand(args.repo))

    with own_processes(()) as owner:
        for scope, files in sorted(by_scope.items()):
            command, additions = prepare_template(templates[scope], {'files': files})
            proc = run_owned(command, cwd=root, env={**os.environ, **additions}, owner=owner)
            if proc.returncode != 0:
                print(f"crapkit: scoped tests for {scope!r} failed (runner exit {proc.returncode})", file=sys.stderr)
                return 1  # the runner's own code would collide with crapkit's 3/5/6/7/8
    return 0


def _note_stale_staged(root: Path, flagged_paths: set, shown: str = "") -> None:
    """A developer who fixed the file but forgot `git add` gets told exactly that.

    The difference is git's to decide, through its own filters: a byte compare
    of the blob against the file called every CRLF checkout stale and sent the
    reader hunting for a staging problem that did not exist.
    """
    from ..gitio import unstaged_paths

    for path in sorted(flagged_paths & unstaged_paths(root)):
        print(f"  note: {shown}{path} differs from the working tree - the STAGED blob is "
              "what commits; re-stage with `git add` if you already fixed it.")


def _clearing_spellings() -> str:
    """How to clear CRAPKIT_OVERRIDE_REASON in the shell the operator is in.

    `unset` is a POSIX builtin, and the receipt prescribed it everywhere. On
    Windows the command errors, the variable stays set, and the next commit is
    granted a full override for a brand new violating function without anyone
    typing a reason. Windows gets all three spellings because `SHELL_IS_CMD`
    knows the platform and not the shell the operator typed into, and the hook
    cannot tell either: git for Windows sets MSYSTEM and SHELL for it whether
    PowerShell or Git Bash started the commit.
    """
    if config.SHELL_IS_CMD:
        return ("`unset CRAPKIT_OVERRIDE_REASON` in Git Bash, "
                "`$env:CRAPKIT_OVERRIDE_REASON = $null` in PowerShell, "
                "`set CRAPKIT_OVERRIDE_REASON=` in cmd.exe")
    return "`unset CRAPKIT_OVERRIDE_REASON`"


def _print_clear_the_reason() -> None:
    """The second half is not decoration: a variable a CI job or a launcher
    exported is still set for the next commit however this shell clears it, and
    that is the case where the grant repeats invisibly."""
    print(f"crapkit: clear CRAPKIT_OVERRIDE_REASON now ({_clearing_spellings()}) - "
          "while set it grants again on every commit.")
    print("crapkit: a CI job or a launcher that exported it is not cleared by any command "
          "here - clear it where it was set.")


def _granted_crap(cfg, violation) -> float:
    """The mark the hook's grant writes: the CRAP the function's scope scores
    with no coverage behind it. A staged blob carries no coverage, so a scope a
    lane measures marks the untested CRAP, and a cc-only scope marks ccn, as
    `score` scores it there."""
    from ..score import flagged_crap

    scope = owning_scope(violation.path, path_matchers({s.name: s.paths for s in cfg.scopes}))
    flag = "cc-only" if scope in cfg.coverage_optional_scopes else "untested"
    return flagged_crap(violation.ccn, 0.0, flag)


def _grant_env_override(root: Path, cfg, violations, reason: str, records=()) -> None:
    """The audited hook override: alert line, ratchet debt (staged into the
    pending commit), and a snapshot record — all three or nothing."""
    from ..gitio import head_commit, stage_path
    from ..override import record_override
    from ..ratchet import metric_version
    from ..ratchetfile import RatchetFile
    from ..verify import GateViolation
    from ._shared import _check_ratchet_identity

    db_path = root / ".crapkit" / "crap.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SnapshotStore(db_path)
    saved = RatchetFile.read(root / cfg.ratchet_file)
    key_version = _check_ratchet_identity(saved.text or "", root, cfg.ratchet_file, records, store)
    run_id = store.write_run(commit=head_commit(root), tool_versions={}, rows=[],
                             lanes={"_hook_override": {"staged": True}}, kind="hook")
    gate = [GateViolation(v.path, v.long_name, v.start, v.ccn, 0.0,
                          _granted_crap(cfg, v), "decompose", False, v.key_name)
            for v in violations]
    record_override(store=store, run_id=run_id, root=root, ratchet_file=cfg.ratchet_file,
                    alert_command=cfg.alert_command, violations=gate, reason=reason,
                    raise_marks=False, key_version=key_version, identity_rows=records,
                    ratchet_input=saved, metric=metric_version())
    stage_path(root, cfg.ratchet_file)  # the debt must be IN the commit, not dangling
    print(f"crapkit: override granted with full audit ({reason}).")
    _print_clear_the_reason()


def _warn_unscoped_staged(unscoped: list) -> None:
    """Staged source no scope claims. A staged file ABOVE the crapkit root is
    never named here by design: the gate reads a diff relative to the root, and
    a file outside the root is outside the universe crapkit can score."""
    if unscoped:
        print(f"crapkit gate: {len(unscoped)} staged file(s) belong to no scope and were "
              f"not gated: {', '.join(unscoped)} - add a [[scope]] claiming them "
              "(see docs/configuration.md)", file=sys.stderr)


def _split_marked(violations: list, entries: list) -> tuple[list, list]:
    """Staged violations split into (gated, exempt) on ratchet-mark EXISTENCE.

    Existence, not the `crap > mark` rule `rescore --gate` uses: the gate judges
    staged blobs, a blob carries no coverage, and without coverage there is no
    CRAP to compare against a mark. So the question the hook can answer is the
    only one it asks — did the repo already sign for this function?

    Without this exemption, a comment inside a marked function can refuse the
    commit while `rescore --gate` on the same tree passes. `verify` keeps the
    numeric check and catches a mark that actually rose.
    """
    from ..keys import stated_key

    marked = {(e.path, e.long_name) for e in entries}
    gated, exempt = [], []
    for v in violations:
        (exempt if stated_key(v) in marked else gated).append(v)
    return gated, exempt


def _note_marked_staged(exempt: list, noun: str = "staged") -> None:
    """One line, never a list. The count says the exemption fired; the marks
    themselves are in the committed TSV, and naming them at every commit would
    reprint debt the repo reads through `crapkit ratchet report`."""
    if exempt:
        print(f"crapkit gate: {len(exempt)} {noun} function(s) carry a ratchet mark and "
              "were not gated - `crapkit verify` fails a mark that rises", file=sys.stderr)


def _gated_violations(root: Path, cfg, violations: list, records=(), noun: str = "staged") -> list:
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
    _ratchet_key_version(root, cfg, records, entries=entries)
    gated, exempt = _split_marked(violations, entries)
    _note_marked_staged(exempt, noun)
    return gated


def _staged_gate(root: Path, cfg, base: str | None = None, *, whole: bool = False):
    """The gate's verdict, with both git reads started before lizard is imported.

    Neither answer is needed until the import is paid for and the two do not
    depend on each other, so the spawns run underneath it. No git ANSWER is read
    until the analyzer is in hand: a machine without lizard still exits 5 having
    said nothing about the commit.
    """
    from ..gitio import staged_reads

    with staged_reads(root, base) as reads:
        _analysis_tools()  # importing crapkit.hook reaches lizard too, so it waits its turn
        from ..hook import gate_staged

        gate = gate_staged(root, cfg, reads, whole=whole)
    if gate.whole:
        print("crapkit gate: nothing is staged and no commit is running, so every tracked "
              "file was judged", file=sys.stderr)
    return gate


def _in_a_commit() -> bool:
    """git sets GIT_INDEX_FILE for the hooks `git commit` runs; `pre-commit run
    --all-files` in CI and a command typed at a shell run without it."""
    import os

    return "GIT_INDEX_FILE" in os.environ


def cmd_hook_precommit(args: argparse.Namespace) -> int:
    """The commit gate, in every crapkit root that owns what the commit holds.
    Exit 6 when a staged function is over its ceiling, or when a staged file
    could not be read at all: zero records from a file nothing read are not
    zero functions over the ceiling, and the override has no function to
    record as debt for it."""
    base = getattr(args, "base", None)
    roots = _hook_roots(args.repo, base)
    if not roots:
        print(f"crapkit gate: no staged file sits under a crapkit.toml at or below "
              f"{Path.cwd()}, so nothing was gated; `{_self()} init` adopts a directory "
              "for the gate", file=sys.stderr)
        return 0
    return max(_hook_gate(root, shown, base) for root, shown in roots)


def _hook_roots(repo: str | None, base: str | None) -> list[tuple[Path, str]]:
    """The roots this gate runs in, each with the prefix its printed paths take.

    `--repo`, or a crapkit.toml at or above the working directory, names the one
    root, as for every command (ADR 0002). git runs the hook at the top, so a
    monorepo whose crapkit.toml sits in packages/api has none there, and the
    gate refused every commit, a docs-only one included. It now runs in each
    root below that owns a staged file and names paths from where git stands.
    """
    from ..rootfind import find_root

    cwd = Path.cwd().resolve()
    if repo is not None or find_root(cwd) is not None:
        return [(_command_root(repo), "")]
    roots = _roots_below(cwd, base)
    for root in roots:
        print(f"crapkit: using crapkit.toml at {root}", file=sys.stderr)
    return [(root, f"{root.relative_to(cwd).as_posix()}/") for root in roots]


def _roots_below(top: Path, base: str | None) -> list[Path]:
    """The roots below `top` that own the paths the gate judges, nearest
    crapkit.toml winning. A directory outside any repository has no staged
    file to place, and keeps the no-configuration refusal it always got."""
    from ..errors import GitError
    from ..rootfind import find_root

    try:
        paths = _owned_paths(top, base)
    except GitError:
        raise ConfigError(no_config(top)) from None
    return sorted({find_root((top / path).parent) for path in paths} - {None})


def _owned_paths(top: Path, base: str | None) -> list[str]:
    """What places the roots: the staged files inside a commit, and every tracked
    crapkit.toml when a `--base` diff or the tracked-file check is what runs."""
    from ..gitio import staged_names, tracked_configs

    if base is not None:
        return tracked_configs(top)
    staged = staged_names(top)
    if staged or _in_a_commit():
        return staged
    return tracked_configs(top)


def _hook_gate(root: Path, shown: str, base: str | None) -> int:
    """One root's verdict. `shown` prefixes every path it prints: "" at the
    command's own root, `packages/api/` for a root found below the top."""
    from ._shared import _print_unread

    cfg = _load_repo_config(root)
    gate = _staged_gate(root, cfg, base, whole=_may_judge_tracked(base))
    _say_left_out(gate.unreadable)
    _warn_unscoped_staged([shown + path for path in gate.unscoped])
    _print_unread({shown + path: why for path, why in gate.unread.items()}, _judged(gate))
    code = _judge_staged(root, cfg, gate, shown)
    return 6 if gate.unread else code


def _may_judge_tracked(base: str | None) -> bool:
    """An empty staged diff judges every tracked file only outside a commit and
    with no `--base`, whose own diff is the question asked."""
    return base is None and not _in_a_commit()


def _judged(gate) -> str:
    return "tracked" if gate.whole else "staged"


def _env_override_reason() -> str:
    import os

    return os.environ.get("CRAPKIT_OVERRIDE_REASON", "").strip()


def _hook_override_refusal(unread: dict) -> str | None:
    """The line verify --override prints for the same unread files, or None
    when every staged file was read."""
    from ..verify import Verdict, with_unread

    return _override_refusal(with_unread(Verdict(False, [], [], [], []), unread, set(unread), set()))


def _judge_staged(root: Path, cfg, gate, shown: str = "") -> int:
    violations = _gated_violations(root, cfg, gate.violations, gate.records, _judged(gate))
    refusal = _hook_override_refusal(gate.unread) if _env_override_reason() else None
    if violations:
        _print_staged_violations(root, cfg, gate, violations, shown)
    if refusal:
        # Before any side effect: the grant writes and stages the marks file,
        # raises the alert and stores a run, and the unread file refuses the
        # commit whatever the grant signed.
        print(f"crapkit: {refusal}")
        return 6
    return _grant_or_refuse(root, cfg, violations, gate.records, gate.whole)


def _print_staged_violations(root: Path, cfg, gate, violations: list, shown: str) -> None:
    """The breaches, then, inside a commit, each one whose fix was never staged."""
    _print_breaches(violations, cfg.target, shown, _judged(gate))
    if not gate.whole:
        _note_stale_staged(root, {v.path for v in violations}, shown)


def _print_breaches(violations: list, target: int, shown: str, judged: str) -> None:
    print(f"crapkit gate: {len(violations)} {judged} function(s) exceed the complexity ceiling of {target}:")
    for v in violations:
        print(f"  ccn {v.ccn:>3}  {shown}{v.path}:{v.start}  {v.long_name}")


def _refuse_tracked() -> int:
    """No commit to refuse or grant: the breach is already committed."""
    print("decompose them and commit the split (coverage cannot save a function above the target).")
    return 6


def _grant_or_refuse(root: Path, cfg, violations: list, records, whole: bool = False) -> int:
    """CRAPKIT_OVERRIDE_REASON is not a bypass: it routes through the full
    three-record audit and the gate holds unless all three land. Outside a
    commit nothing is granted: the breach is already committed."""
    if not violations:
        return 0
    if whole:
        return _refuse_tracked()
    reason = _env_override_reason()
    if reason:
        _grant_env_override(root, cfg, violations, reason, records)
        return 0
    print("decompose before committing (coverage cannot save a function above the target).")
    return 6
