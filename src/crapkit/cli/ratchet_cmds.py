"""The `ratchet` subcommand's five actions and the burn-down report behind
`ratchet report`: seed new debt, prune marks whose code left (renames followed
first), merge two marks files as a git merge driver, move marks at their
recorded values, and report ages and repayment from the marks file's history."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import NamedTuple

from ..errors import ConfigError, CrapkitError
from ..invocation import _self
from ..named import first_few
from ..store import SnapshotStore, anywhere
from ._shared import (_command_root, _load_ratchet_or_die, _load_repo_config, _open_store,
                      _print_json, _ratchet_or_die, _repo_relative, _stand, behind_head)


def _is_failed_verify(run: dict) -> bool:
    return run["kind"] == "verify" and run["verdict_ok"] is False


def _skipped_failed_verifies(runs: list[dict], chosen_id: int) -> list[dict]:
    """Every failed verify newer than the run this seed or prune settled on.

    Run id is the order, so "newer" is the id comparison. All of them, not the
    nearest: when the pick walks back past two failures, naming one of them
    tells half the reason the line carries an older run id.
    """
    return [r for r in runs if r["id"] > chosen_id and _is_failed_verify(r)]


def _no_trusted_run() -> str:
    return (f"no trusted full run to work from - run `{_self()} coverage` first "
            "(failed verifies and hook runs never serve as baselines)")


def _no_full_run(pick, runs: list[dict]) -> str:
    """Why there is nothing to seed or prune against: no trusted run at all, or
    a failure standing in front of every one there is, or of the next one.

    A store whose only run is a failed verify gets the second line, not "run
    coverage first": the coverage run would stand behind that failure and earn
    the second line anyway."""
    from ..store import outstanding_failure

    blocker = pick.blocker or outstanding_failure(runs)
    if blocker is None:
        return _nothing_behind_head(runs)
    return (f"no run to work from: verify run {blocker['id']} FAILED with "
            f"{blocker['findings']} finding(s), nothing older is left to work from, "
            f"and a fresh `{_self()} coverage` would only be refused the same way - "
            "fix the findings and let a verify pass")


def _nothing_behind_head(runs: list[dict]) -> str:
    """No trusted run at all, or none at or behind HEAD: every one there is was
    measured on another branch, or on commits a rebase or an amend replaced."""
    from ..store import is_trusted

    trusted = [r for r in runs if is_trusted(r)]
    if not trusted:
        return _no_trusted_run()
    newest = trusted[-1]
    return (f"no trusted run at or behind HEAD to work from: the newest, run {newest['id']} @ "
            f"{newest['commit'][:11]}, was measured on history HEAD does not contain (another "
            f"branch, or commits a rebase or an amend replaced) - run `{_self()} coverage` "
            "on this branch first")


class _WorkRun(NamedTuple):
    """The run seed or prune reads, and how it came to be that one.

    `skipped` holds the failed verifies newer than `run` that verify's rule
    walked back past, and `newer` the newest trusted run above `run`: the one
    `--baseline` reads instead. `named` says `--baseline` chose the run, which
    skips nothing. `blocker` is the failed verify that keeps verify's rule on
    `run`, the one verify's taint warning names; None when nothing pins it.
    """
    run: dict
    skipped: list[dict]
    newer: dict | None
    named: bool
    blocker: dict | None


def _latest_full_run(store: SnapshotStore, requested: int | None = None, *,
                     behind=anywhere) -> _WorkRun:
    """The run seed and prune work from, and the failed verifies passed over.

    `pick_baseline` — verify's own choice, not a weaker rule that agrees with it
    most of the time. Trust is not enough on its own: a coverage run taken after
    a failed verify IS trusted, and seeding off it signs marks at values verify
    refuses as a comparison point, which is how the failure's findings stop
    being touched. Reading trust alone was that bug; reading neither was #16.
    `behind` keeps it to runs at or behind HEAD, as verify's pick is: a run on
    another branch signed marks for code this branch does not hold.

    `requested` is `--baseline ID`, admitted by the rule `verify --baseline`
    runs. Without it a failed verify in front of every newer run pinned seed
    to an old run with no way off it but a passing verify (#75).
    """
    from ..store import admit_baseline

    runs = store.list_runs()
    if requested is not None:
        run = admit_baseline(runs, requested, none_trusted=_no_trusted_run())
        return _WorkRun(run, [], _newer_trusted(runs, run["id"], behind), True, None)
    pick = _usable_pick(runs, behind)
    run = pick.run
    skipped = _skipped_failed_verifies(runs, run["id"])
    return _WorkRun(run, skipped, _newer_trusted(runs, run["id"], behind), False,
                    _pinning_verify(pick, skipped))


def _newer_trusted(runs: list[dict], run_id: int, behind) -> dict | None:
    """The newest trusted run above `run_id` and behind HEAD, or None when there is none."""
    from ..store import is_trusted

    return next((r for r in reversed(runs)
                 if r["id"] > run_id and is_trusted(r) and behind(r)), None)


def _usable_pick(runs: list[dict], behind):
    """verify's pick, refused when it holds no run to work from."""
    from ..store import pick_baseline

    pick = pick_baseline(runs, behind)
    if pick.run is None:
        raise CrapkitError(_no_full_run(pick, runs))
    return pick


def _pinning_verify(pick, skipped: list[dict]) -> dict | None:
    """The failed verify that keeps seed on `pick.run`.

    The pick's own blocker when it passed a trusted run over, so seed names the
    failure verify's taint warning names: naming the first skipped failure sent
    the reader to findings a later failure had replaced. With no trusted run
    after the failures, the pick records none, and the newest failure is the
    one no verify has answered (a passing verify is trusted, so none came after).
    """
    if pick.blocker is not None:
        return pick.blocker
    return skipped[-1] if skipped else None


def _skip_note(skipped: list[dict], newer: dict | None = None) -> str:
    """Why the line names an older run than the newest one in the store."""
    if not skipped:
        return ""
    ids = ", ".join(str(r["id"]) for r in skipped)
    label = "runs" if len(skipped) > 1 else "run"
    return f", skipped failed verify {label} {ids}{_newer_note(newer)}"


def _newer_note(newer: dict | None) -> str:
    """The newer run the fallback passed over, and the flag that reads it.

    Without it the line showed an older run id and no way to reach the newer
    one, which is half of how #75 left no command that worked.
    """
    if newer is None:
        return ""
    return f" and the newer run {newer['id']} (pass `--baseline {newer['id']}` to read it)"


def _merge_stamp(texts: list[str]) -> None:
    """Refuse two sides that do not share one metric stamp; the merge keeps it.

    Reconciling marks across metrics means picking a minimum between numbers
    produced by different rules, which is not a comparison at all.
    """
    from ..ratchet import read_stamp

    ours, theirs = read_stamp(texts[1]), read_stamp(texts[2])
    if ours != theirs:
        raise ConfigError(
            f"ratchet merge refused: ours is [{ours or 'unstamped'}] and theirs is "
            f"[{theirs or 'unstamped'}] - marks from different metric versions cannot "
            f"merge; {_merge_remedy(ours, theirs)}")


def _merge_remedy(ours: str, theirs: str) -> str:
    """Re-seed under the newer side's metric. "re-baseline one side" said neither
    which side nor under which crapkit, and a seed under the older release
    stamps its own older metric, so the next merge refused again."""
    from ..ratchet import coverage_then_seed, newer_tools

    if newer_tools(theirs, ours):
        side, stamp = "theirs", theirs
    elif newer_tools(ours, theirs):
        side, stamp = "ours", ours
    else:
        return coverage_then_seed("re-baseline one side")
    return (f"{side} is newer, so with a crapkit that measures [{stamp}], "
            f"{coverage_then_seed('re-baseline the merged marks')}")


def _ratchet_merge(files: list) -> int:
    from ..ratchet import merge_ratchets
    from ..ratchetfile import RatchetFile

    if len(files) != 3:
        raise ConfigError("ratchet merge takes exactly three files: BASE OURS THEIRS (git %O %A %B)")
    # The one reader: OURS is the working copy a shell may have saved with a
    # BOM, which read strictly hid its stamp (`ours is [unstamped]`, exit 3),
    # or as UTF-16, which OURS stays when it is written back.
    saved = [RatchetFile.read(Path(f), required=True) for f in files]
    texts = _mergeable_texts(saved)
    merged = merge_ratchets(*(_ratchet_or_die(t, f) for t, f in zip(texts, files)))
    # Merging adds no number: OURS keeps the stamps all three sides share.
    # THEIRS's rows reach OURS, so a byte its read replaced refuses the write;
    # BASE only tells the two changes apart, and a name it misread keys nothing.
    saved[1].publish(saved[1].kept(merged, read_with=(saved[2],)))
    print(f"ratchet merge: {len(merged)} mark(s)")
    return 0


def _mergeable_texts(saved: list) -> list[str]:
    """The three sides' texts, once they share one metric stamp and one key format.

    A blank BASE is no common ancestor. Two branches that each created the file
    (two first seeds) meet in an add/add merge, and git hands the driver an empty
    %O. Read as a file, it had key version 0 and refused the merge as a legacy
    mapping. Out of the checks, it merges OURS and THEIRS as a union, each shared
    key at the lower mark."""
    texts = [side.text or "" for side in saved]
    _merge_stamp(texts)
    _merge_key_version(texts[1:] if saved[0].blank else texts)
    return texts


def _merge_key_version(texts: list[str]) -> None:
    from ..ratchet import read_key_version

    try:
        versions = {read_key_version(text) for text in texts}
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if len(versions) != 1:
        raise ConfigError("ratchet key identity versions differ; reconcile the legacy "
                          "function mapping before merging; OURS was left unchanged")


def _move_path(raw: str, root: Path, cwd: Path | None) -> str:
    """One `ratchet move` argument as the repo-relative path marks are keyed by.

    Read like every other path argument (ADR 0002), so `./web/a.py`, a Windows
    backslash path and a path typed below the root all name the key a scored
    row carries. The rebase normalizes away the trailing slash that makes OLD
    a directory, so it goes back on.
    """
    path = _repo_relative(raw, root, cwd)
    return path + "/" if raw.endswith(("/", os.sep)) else path


def _ratchet_move(root: Path, cfg, files: list, cwd: Path | None) -> int:
    """`cwd` is where the user typed the paths (`_stand`), None under --repo.
    It has no default: leaving it out read every path root-relative from any
    directory, and nothing said so."""
    from ..ratchet import move_marks
    from ..ratchetfile import RatchetFile

    if len(files) != 2:
        raise ConfigError("ratchet move takes exactly two paths: OLD NEW")
    old, new = (_move_path(raw, root, cwd) for raw in files)
    saved = RatchetFile.read(root / cfg.ratchet_file)
    entries, moved = move_marks(saved.entries, old, new)
    if not moved:
        raise ConfigError(f"ratchet move: no mark under {old} in {cfg.ratchet_file} "
                          "(a directory must end in '/')")
    saved.publish(saved.kept(entries))  # values never change, so both stamps stay
    print(f"{cfg.ratchet_file}: moved {moved} mark(s) from {old} to {new}")
    return 0


class _Renames(NamedTuple):
    """The renames prune can see, and the run their diff starts from.

    `missing` is the store's first run when this clone does not hold its
    commit; `anchor` is then the oldest run whose commit it does hold, or None
    when it holds none.
    """
    pairs: dict[str, str]
    anchor: dict | None
    missing: dict | None = None


def _prune_renames(root: Path, runs: list[dict]) -> _Renames:
    """Renames a mark could have lived through, as one tree-to-tree diff.

    Anchored at the store's FIRST run: a mark can only have been seeded from a
    run, so no mark era starts before it, and rename detection compares two trees
    rather than walking history, so the widest window costs the same as a narrow
    one and cannot invent a pairing. A clone that lacks that commit (a rebase
    and gc, a squash-merged branch gc collected, a depth-1 CI clone) diffs from
    the oldest run it holds instead, and `_refuse_unseen_renames` refuses the
    marks that window cannot answer for. Any other git failure is named: it used
    to read as "no renames", and a renamed file's marks dropped as repaid debt.
    """
    from ..errors import GitError
    from ..gitio import renamed_paths

    first = runs[0]
    try:
        return _Renames(renamed_paths(root, first["commit"]), first)
    except GitError:
        if not _git_work_tree(root):
            print(f"note: {root} is not a git work tree, so prune followed no renames and dropped "
                  "the marks of every file that left, renamed or not; to keep a renamed file's "
                  f"marks, run `{_self()} ratchet prune` in the git checkout instead",
                  file=sys.stderr)
            return _Renames({}, None)
        if _held(root, first):
            raise
    return _held_renames(root, runs)


def _held(root: Path, run: dict) -> bool:
    """Whether this clone holds the run's commit. When git cannot answer, prune
    refuses: read as "not held", the failure sent the reader after a fetch."""
    from ..errors import GitError
    from ..gitio import has_commit

    try:
        return has_commit(root, run["commit"])
    except GitError as exc:
        raise GitError(f"ratchet prune: git cannot say whether run {run['id']}'s commit "
                       f"{run['commit'][:11]} is in this clone ({exc}); fix what git reports, "
                       f"then run `{_self()} ratchet prune` again; nothing was written") from exc


def _git_work_tree(root: Path) -> bool:
    """Whether git manages `root` at all. A directory outside every repository
    holds no history a file could have been renamed in."""
    from ..errors import GitError
    from ..gitio import worktree_root

    try:
        worktree_root(root)
    except GitError:
        return False
    return True


def _held_renames(root: Path, runs: list[dict]) -> _Renames:
    """The renames since the oldest run whose commit this clone holds."""
    from ..gitio import renamed_paths

    anchor = _oldest_held(root, runs)
    if anchor is None:
        return _Renames({}, None, runs[0])
    return _Renames(renamed_paths(root, anchor["commit"]), anchor, runs[0])


def _oldest_held(root: Path, runs: list[dict]) -> dict | None:
    """The oldest of `runs` whose commit this clone holds, asking git once per
    commit: runs share commits, and a shallow CI clone lacks most of them."""
    asked: dict[str, bool] = {}
    for run in runs:
        commit = run["commit"]
        if commit not in asked:
            asked[commit] = _held(root, run)
        if asked[commit]:
            return run
    return None


def _pruned(root: Path, store: SnapshotStore, prior: list, fresh: list) -> tuple[list, str]:
    """Prune, renames first: a file git moved is a relocated mark, not repaid debt."""
    from ..ratchet import follow_renames, prune_ratchet

    renames = _prune_renames(root, store.list_runs())
    followed, moved = follow_renames(prior, fresh, renames.pairs)
    entries, dropped = prune_ratchet(followed, fresh)
    _refuse_unseen_renames(root, renames, _left_the_corpus(followed, entries, fresh))
    _note_window(renames)
    return entries, (f"pruned {dropped}, followed {moved} rename(s)"
                     f"{_followed_names(prior, followed, renames.pairs)}")


def _left_the_corpus(followed: list, kept: list, fresh: list) -> list[str]:
    """The paths of dropped marks that hold no row in the run: only a file that
    left could have been renamed. A path that still scores cannot be a rename's
    source, so its dropped marks are code that left, whatever the anchor."""
    scored, kept_marks = {row.path for row in fresh}, set(kept)
    return sorted({e.path for e in followed if e not in kept_marks and e.path not in scored})


def _refuse_unseen_renames(root: Path, renames: _Renames, left: list[str]) -> None:
    """Exit 4, before anything is written, when the first run's commit is gone
    and a dropped mark's file may have been renamed where no held commit shows.

    A file still in the checkout was not renamed. A file the anchor's tree held
    and HEAD's does not shows in the diff from the anchor, so its rename was
    seen or there was none. What is left left before the oldest held run.
    """
    from ..errors import GitError

    if renames.missing is None or not left:
        return
    unseen = _unseen(root, renames.anchor, left)
    if unseen:
        raise GitError(_unseen_refusal(root, renames.missing, unseen))


def _unseen(root: Path, anchor: dict | None, left: list[str]) -> list[str]:
    from ..gitio import diff_names_since, ls_files

    known = set(ls_files(root))
    if anchor is not None:
        known.update(diff_names_since(root, anchor["commit"]))
    return [path for path in left if path not in known]


def _unseen_refusal(root: Path, missing: dict, unseen: list[str]) -> str:
    commit = missing["commit"]
    return (f"ratchet prune: run {missing['id']}'s commit {commit[:11]} is not in this clone, so "
            f"git cannot say whether {first_few(unseen)} was renamed or deleted, and prune would "
            f"drop the marks there as repaid debt; {_anchor_fetch(root, commit)}; nothing was written")


def _anchor_fetch(root: Path, commit: str) -> str:
    """The fetch that brings the anchor back, and the way on when none can."""
    from ..gitio import is_shallow

    if is_shallow(root):
        return ("this shallow clone does not hold it: set fetch-depth: 0 on the checkout or run "
                "git fetch --unshallow, then prune again")
    return (f"fetch it with `git fetch origin {commit}` and prune again, or, when no remote holds "
            f"it, move a renamed file's marks with `{_self()} ratchet move OLD NEW` and delete a "
            "deleted file's marks from the marks file by hand")


def _followed_names(prior: list, followed: list, pairs: dict[str, str]) -> str:
    """` (a.py -> b.py, ...)`: the renames the marks followed, up to three."""
    kept = set(followed)
    moved = sorted({f"{e.path} -> {pairs[e.path]}" for e in prior if e not in kept})
    return f" ({first_few(moved)})" if moved else ""


def _note_window(renames: _Renames) -> None:
    """One stderr line when the first run's commit is gone, naming the run the
    renames were read from instead. stderr, so the prune line keeps its shape."""
    if renames.missing is None:
        return
    gone = (f"note: run {renames.missing['id']}'s commit {renames.missing['commit'][:11]} "
            "is not in this clone")
    if renames.anchor is None:
        print(f"{gone}, and no run's commit is, so no rename was followed", file=sys.stderr)
        return
    print(f"{gone}, so renames were followed from run {renames.anchor['id']} "
          f"({renames.anchor['commit'][:11]}), the oldest run whose commit it holds",
          file=sys.stderr)


def _print_ratchet_report(report: dict, violations: list, ratchet_file: str) -> None:
    print(f"ratchet burn-down: {report['open']} open mark(s), {report['dropped_total']} repaid "
          f"({report['dropped_last_30d']} in the last 30d, {report['dropped_last_90d']} in 90d)")
    if report["uncommitted"]:
        print(f"  {report['uncommitted']} uncommitted mark(s) in {ratchet_file}: open reads the "
              "working tree, ages and repayment read committed history")
    for v in violations:
        print(f"  POLICY {v}")
    for e in report["oldest"][:10]:
        print(f"  {e['age_days']:>5}d  {e['path']}  {e['long_name']}")


def _working_marks(root: Path, ratchet_file: str) -> dict:
    """The marks on disk, keyed (path, key name) -> crap. Ages come from the
    file's git history, but which marks are OPEN is a question about now, and a
    seed prints "added 1" long before anybody commits the TSV."""
    entries = _load_ratchet_or_die(root / ratchet_file, ratchet_file)
    return {(e.path, e.long_name): e.crap for e in entries}


def _report_basis(root: Path, ratchet_file: str) -> tuple[list, dict | None]:
    """The history the report replays and the marks it reads as open.

    A marks file that is missing or holds only blank lines is not a repo that
    repaid every mark: verify judges it against the newest committed marks, so
    the report reads those as open (working None, the committed state) and the
    commit that deleted or emptied the file repays none (held_history)."""
    from ..marks_history import marks_history
    from ..ratchet_report import held_history
    from ..ratchetfile import RatchetFile

    patches = marks_history(root, ratchet_file)
    if RatchetFile.read(root / ratchet_file).blank:
        return held_history(patches), None
    return patches, _working_marks(root, ratchet_file)


def _warn_marks_stand_in(root: Path, ratchet_file: str, report: dict, working) -> None:
    """One line when the open marks came from history, not from the file."""
    if working is not None or not report["open"]:
        return
    state, act = ("empty", "emptying") if (root / ratchet_file).exists() else ("missing", "deleting")
    print(f"warning: {ratchet_file} is {state}, so the report reads the {report['open']} "
          "mark(s) its history last committed as open, the marks verify judges against, and "
          f"{act} the file repays none. `git log -- {ratchet_file}` shows the commits that "
          "held them", file=sys.stderr)


def _judges_policy(cfg, enforce: bool) -> bool:
    """--enforce with a debt knob in [crapkit]: the one case the report judges."""
    knobs = (cfg.debt_max_age_months, cfg.repayment_min_per_30d)
    return enforce and any(k is not None for k in knobs)


def _policy_findings(cfg, report: dict, enforce: bool) -> list | None:
    """The debt-policy findings, or None when no policy was evaluated.

    None, not []: without --enforce, or with no debt knobs in [crapkit] to judge
    by, nothing looked at the debt at all. [] then says "policy clean" about a
    policy that does not exist.
    """
    from ..ratchet_report import policy_violations

    if not _judges_policy(cfg, enforce):
        return None
    return policy_violations(report, cfg.debt_max_age_months, cfg.repayment_min_per_30d)


def _refuse_a_cut_history(cfg, enforce: bool, shallow: bool) -> None:
    """Exit 4 before judging the policy on a history the clone does not hold.

    Every age and every repayment is read off the ratchet file's commits. A
    depth-1 clone holds one, so every mark read 0 days old and nothing was ever
    repaid: an age limit passed that a full clone fails, and a repayment quota
    failed that a full clone passes.
    """
    from ..gitio import shallow_refusal

    if shallow and _judges_policy(cfg, enforce):
        raise shallow_refusal("ratchet report --enforce judges mark ages and repayments by "
                              f"the git history of {cfg.ratchet_file}")


def _warn_history(shallow: bool) -> None:
    """One line when the history the ages count is not the file's whole
    history: a shallow clone. A renamed marks file keeps its whole history
    (marks_history follows the rename), so it needs no line."""
    from ..gitio import shallow_warning

    if shallow:
        print(shallow_warning("mark ages and repayments"), file=sys.stderr)


def _ratchet_report(root: Path, cfg, as_json: bool, enforce: bool) -> int:
    from ..gitio import shallow_checkout
    from ..ratchet_report import mark_events, report_from_events

    shallow = shallow_checkout(root)
    _refuse_a_cut_history(cfg, enforce, shallow)
    patches, working = _report_basis(root, cfg.ratchet_file)
    report = report_from_events(mark_events(patches), working=working)
    violations = _policy_findings(cfg, report, enforce)
    _warn_history(shallow)
    _warn_marks_stand_in(root, cfg.ratchet_file, report, working)
    if as_json:
        _print_json({**report, "policy_violations": violations, "shallow": shallow})
    else:
        _print_ratchet_report(report, violations or [], cfg.ratchet_file)
    return 1 if violations else 0


def cmd_ratchet(args: argparse.Namespace) -> int:
    requested = _requested_run(args)
    if args.action == "merge":  # a git merge driver runs with no crapkit.toml in sight
        return _ratchet_merge(args.files)
    root = _command_root(args.repo)
    cfg = _load_repo_config(root)
    if args.action == "report":
        return _ratchet_report(root, cfg, args.json, args.enforce)
    if args.action == "move":  # a hand-declared rename needs no run to follow
        return _ratchet_move(root, cfg, args.files, _stand(args.repo))
    return _ratchet_from_run(root, cfg, args.action, requested)


def _requested_run(args: argparse.Namespace) -> int | None:
    """`--baseline ID`, which only the two actions that read a run take.

    Ignoring it elsewhere would let `ratchet report --baseline 3` read as a
    report about run 3.
    """
    if args.baseline is not None and args.action not in ("seed", "prune"):
        raise ConfigError(f"ratchet {args.action} reads no run, so it takes no --baseline")
    return args.baseline


def _ratchet_from_run(root: Path, cfg, action: str, requested: int | None) -> int:
    """seed and prune: both work from the run verify would compare against,
    or from the run `--baseline` names.

    seed signs that run's numbers, so the marks take the metric the run was
    measured under. prune adds no number, so the recorded stamp stays; a file
    prune creates holds no mark and takes the running metric, which relabels
    nothing.
    """
    from ..gitio import GitFacts
    from ..keys import require_unambiguous
    from ..ratchet import metric_version
    from ..ratchetfile import RatchetFile
    from ._shared import _check_ratchet_identity

    store = _open_store(root)
    work = _latest_full_run(store, requested, behind=behind_head(GitFacts(root)))
    latest = work.run
    fresh = store.read_scored(latest["id"])
    require_unambiguous(fresh, run_id=latest["id"], advice=_identity_advice(work, action))
    saved = RatchetFile.read(root / cfg.ratchet_file)
    _refuse_newer_marks(saved, work, action)
    marks = saved.entries
    key_version = _check_ratchet_identity(saved.text or "", root, cfg.ratchet_file, fresh, store,
                                          entries=marks, moves_marks=True)
    if action == "seed":
        entries, note = _seeded(marks, fresh, cfg)
        _refuse_unkeyable_twins(cfg.ratchet_file, work, marks, entries, fresh, key_version)
        text = saved.reseeded(entries, _seed_metric(latest), keys=key_version)
    else:
        entries, note = _pruned(root, store, marks, fresh)
        text = saved.kept(entries, keys=key_version, new_file_metric=metric_version())
    _publish_checked(saved, text, entries, fresh, latest["tool_versions"].get("analysis_version"))
    metric_note = _metric_note(work, action, created=saved.text is None)
    print(f"{cfg.ratchet_file}: {note} - {len(entries)} mark(s) vs run {latest['id']} "
          f"({latest['commit'][:11]}){_skip_note(work.skipped, work.newer)}{metric_note}")
    _print_seed_next(action, cfg.ratchet_file, metric_note)
    return 0


# What seed or prune would do to marks from a run an older metric measured.
_BACKWARDS = {"seed": "this seed would restamp them under the older metric, and verify under the "
                      "newer one would refuse them",
              "prune": "this prune would drop every mark whose function the older reader names "
                       "differently, as if its code were gone"}


def _refuse_newer_marks(saved, work: _WorkRun, action: str) -> None:
    """Refuse to rewrite marks a newer crapkit or lizard recorded than the one
    that measured the run. seed restamped the file under the run's older metric,
    and prune judged which marks were gone by the older reader's names. Two
    ways to get there: this install is older than the marks (upgrade it), or a
    failed verify pins seed and prune to a run an older release measured (read
    a newer run)."""
    from ..ratchet import metric_version, newer_tools, run_stamp

    recorded = saved.metric_stamp
    newer = newer_tools(recorded, metric_version())
    if newer:
        raise ConfigError(_newer_than_install(saved, action, newer))
    measured = run_stamp(work.run["tool_versions"])
    if newer_tools(recorded, measured):
        raise ConfigError(f"ratchet {action} refused: {saved.path.name} was recorded under "
                          f"[{recorded}] and run {work.run['id']} under the older [{measured}]; "
                          f"{_BACKWARDS[action]}; {_way_off(work.newer)}")


def _newer_than_install(saved, action: str, newer: list[str]) -> str:
    from ..ratchet import metric_version, upgrade_remedy

    return (f"ratchet {action} refused: {saved.path.name} was recorded under "
            f"[{saved.metric_stamp}] and this crapkit measures [{metric_version()}] - "
            f"{upgrade_remedy(newer)}; {_BACKWARDS[action]}. A team going back to this release "
            f"on purpose restores the {saved.path.name} it last wrote from git history")


def _print_seed_next(action: str, ratchet_file: str, metric_note: str) -> None:
    """The README's two steps after a seed. seed printed none, so a user who
    followed what crapkit printed never committed the marks or ran the verify
    that makes the first passing verdict.

    A seed that signed another crapkit's metric has already said verify refuses
    those marks and named the run that restamps them, so it adds nothing.
    """
    if action == "seed" and not metric_note:
        print(f"-> next: commit {ratchet_file}, then run `{_self()} verify`")


def _identity_advice(work: _WorkRun, action: str) -> str:
    """What moves seed or prune off a run whose same-line twins it cannot key.

    A fresh coverage run clears the refusal only when it becomes the run seed
    reads next. Behind a failed verify, or under `--baseline`, it does not, and
    the stock "refresh analysis" sent the reader to rerun coverage for nothing
    while a positioned run sat in the store (#75).
    """
    from ..keys import REFRESH_ADVICE

    reason = _pinned_by(work, action)
    if reason is None:
        return REFRESH_ADVICE
    return (f"{reason}, so a fresh `{_self()} coverage` alone changes nothing; "
            f"{_way_off(work.newer)}")


def _pinned_by(work: _WorkRun, action: str) -> str | None:
    """Why seed or prune read this run rather than the one coverage writes next."""
    run_id = work.run["id"]
    if work.named:
        return f"{action} reads run {run_id} because `--baseline {run_id}` names it"
    if work.blocker is not None:
        return (f"{action} reads run {run_id} because verify run {work.blocker['id']} "
                "FAILED after it and no verify has passed since")
    return None


def _way_off(newer: dict | None) -> str:
    """The command that reads another run: the newer one when the store holds it."""
    if newer is None:
        return f"run `{_self()} coverage` and pass the run it writes to `--baseline`"
    return f"pass `--baseline {newer['id']}` to read run {newer['id']}"


def _seeded(prior: list, fresh: list, cfg) -> tuple[list, str]:
    from ..ratchet import seed_ratchet

    entries, added, tightened = seed_ratchet(prior, fresh, target=cfg.target,
                                             scope_targets=cfg.scope_targets)
    return entries, f"added {added}, tightened {tightened}"


def _refuse_unkeyable_twins(name: str, work: _WorkRun, prior: list, entries: list, fresh: list,
                            key_version: int) -> None:
    """Refuse a seed that would add same-line twin marks to start-only keys.

    A file with no key stamp keeps the start-only format while any mark names a
    function the run lacks, and that format cannot say which of two functions on
    one line a mark belongs to. The publish check refused the file seed rendered
    and told the reader to reconcile the twin marks seed itself was adding: on a
    large consumer repo 170 groups, none of them in the file. Prune drops the
    unseen marks, and the next seed writes the positioned keys.
    """
    from ..ratchet import KEY_VERSION, marked_collisions

    twins = set() if key_version == KEY_VERSION else marked_collisions(entries, fresh)
    if twins:
        raise ConfigError(_prune_first(name, work, prior, fresh, twins))


def _prune_first(name: str, work: _WorkRun, prior: list, fresh: list, twins: set) -> str:
    from ..ratchet import unseen_marks

    run_id = work.run["id"]
    unseen = [(entry.path, entry.long_name) for entry in unseen_marks(prior, fresh)]
    flag = f" --baseline {run_id}" if work.named else ""
    return (f"{name}: {len(unseen)} mark(s) name functions run {run_id} does not hold, "
            f"first {_first_key(unseen)}, so the file keeps the start-only key format, which "
            f"cannot key the same-line twins in {len(twins)} group(s) this seed would mark, "
            f"first {_first_key(twins)}; run `{_self()} ratchet prune{flag}` first, then seed "
            "again: prune drops those marks and seed then writes the positioned keys")


def _first_key(keys) -> str:
    """The first of `keys` in marks-file order, as `path: name`."""
    path, key_name = min(keys)
    return f"{path}: {key_name}"


def _seed_metric(run: dict) -> str:
    """The stamp seed signs with: the metric its run was measured under.

    A run from before crapkit recorded one cannot vouch for any metric, and
    stamping the running one is how old numbers passed for new ones.
    """
    from ..ratchet import run_stamp

    metric = run_stamp(run["tool_versions"])
    if not metric:
        raise ConfigError(f"ratchet seed: run {run['id']} recorded no metric (analysis version "
                          "and lizard), so the marks it measured cannot be stamped; run "
                          f"`{_self()} coverage` and seed again")
    return metric


def _metric_note(work: _WorkRun, action: str, *, created: bool) -> str:
    """Said only when the run seed or prune read is not this crapkit's metric."""
    from ..ratchet import metric_version, run_stamp

    run = work.run
    measured, running = run_stamp(run["tool_versions"]), metric_version()
    if measured == running:
        return ""
    said = f"[{measured}]" if measured else "an unrecorded metric"
    return (f"; run {run['id']} was measured under {said}, not this crapkit's [{running}]"
            f"{_stamp_consequence(work, action, created)}")


def _stamp_consequence(work: _WorkRun, action: str, created: bool) -> str:
    """What the older run means for the stamp the write left.

    A file prune creates recorded no stamp to keep, so there is nothing to say.
    """
    if action == "seed":
        return f", so verify refuses these marks until {_restamping_seed(work)}"
    return "" if created else ", and the marks keep their recorded stamp"


def _restamping_seed(work: _WorkRun) -> str:
    """The seed that replaces the stamp this one signed.

    A fresh coverage run is the run the next plain seed reads only when nothing
    holds seed where it is. Behind a failed verify, or under `--baseline`, the
    next plain seed reads this run again and signs the same old stamp, so the
    clause names a run this crapkit measured to pass instead.
    """
    if work.blocker is None and not work.named:
        return f"a fresh `{_self()} coverage` and another seed"
    return f"a seed from a run this crapkit measured: {_way_off(_measured_here(work.newer))}"


def _measured_here(run: dict | None) -> dict | None:
    """`run` when this crapkit measured it, so a seed from it signs the running metric."""
    from ..ratchet import metric_version, run_stamp

    if run is not None and run_stamp(run["tool_versions"]) == metric_version():
        return run
    return None


def _publish_checked(saved, text: str, entries: list, fresh: list, analysis_version) -> None:
    """Refuse to write `text`, which holds `entries`, when its marks cannot be keyed."""
    from ..ratchet import check_reader_version, checked_key_version

    try:
        check_reader_version(entries, analysis_version)
        checked_key_version(text, fresh, entries=entries)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    saved.publish(text)
