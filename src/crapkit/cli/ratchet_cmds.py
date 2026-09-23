"""The `ratchet` subcommand's five actions and the burn-down report behind
`ratchet report`: seed new debt, prune marks whose code left (renames followed
first), merge two marks files as a git merge driver, move marks at their
recorded values, and report ages and repayment from the marks file's history."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..errors import ConfigError, CrapkitError
from ..invocation import _self
from ..store import SnapshotStore
from ._shared import (_command_root, _load_ratchet_or_die, _load_repo_config, _open_store,
                      _print_json, _ratchet_or_die, _repo_relative, _stand, repo_text)


def _is_failed_verify(run: dict) -> bool:
    return run["kind"] == "verify" and run["verdict_ok"] is False


def _skipped_failed_verifies(runs: list[dict], chosen_id: int) -> list[dict]:
    """Every failed verify newer than the run this seed or prune settled on.

    Run id is the order, so "newer" is the id comparison. All of them, not the
    nearest: when the pick walks back past two failures, naming one of them
    tells half the reason the line carries an older run id.
    """
    return [r for r in runs if r["id"] > chosen_id and _is_failed_verify(r)]


def _no_full_run(pick) -> str:
    """Why there is nothing to seed or prune against: no trusted run at all, or
    a failure standing in front of every one there is."""
    if pick.blocker is None:
        return (f"no trusted full run to work from — run `{_self()} coverage` first "
                "(failed verifies and hook runs never serve as baselines)")
    return (f"no run to work from: verify run {pick.blocker['id']} FAILED with "
            f"{pick.blocker['findings']} finding(s), nothing older is left to work from, "
            f"and a fresh `{_self()} coverage` would only be refused the same way — "
            "fix the findings and let a verify pass")


def _latest_full_run(store: SnapshotStore) -> tuple[dict, list[dict]]:
    """The run seed and prune work from, and the failed verifies passed over.

    `pick_baseline` — verify's own choice, not a weaker rule that agrees with it
    most of the time. Trust is not enough on its own: a coverage run taken after
    a failed verify IS trusted, and seeding off it signs marks at values verify
    refuses as a comparison point, which is how the failure's findings stop
    being touched. Reading trust alone was that bug; reading neither was #16.
    """
    from ..store import pick_baseline

    runs = store.list_runs()
    pick = pick_baseline(runs)
    if pick.run is None:
        raise CrapkitError(_no_full_run(pick))
    return pick.run, _skipped_failed_verifies(runs, pick.run["id"])


def _skip_note(skipped: list[dict]) -> str:
    """Why the line names an older run than the newest one in the store."""
    if not skipped:
        return ""
    ids = ", ".join(str(r["id"]) for r in skipped)
    return f", skipped failed verify {'runs' if len(skipped) > 1 else 'run'} {ids}"


def _merge_stamp(texts: list[str]) -> None:
    """Refuse two sides that do not share one metric stamp; the merge keeps it.

    Reconciling marks across metrics means picking a minimum between numbers
    produced by different rules, which is not a comparison at all.
    """
    from ..ratchet import coverage_then_seed, read_stamp

    ours, theirs = read_stamp(texts[1]), read_stamp(texts[2])
    if ours != theirs:
        raise ConfigError(
            f"ratchet merge refused: ours is [{ours or 'unstamped'}] and theirs is "
            f"[{theirs or 'unstamped'}] — marks from different metric versions cannot "
            f"merge; {coverage_then_seed('re-baseline one side')}")


def _ratchet_merge(files: list) -> int:
    from ..ratchet import merge_ratchets
    from ..ratchetfile import RatchetFile

    if len(files) != 3:
        raise ConfigError("ratchet merge takes exactly three files: BASE OURS THEIRS (git %O %A %B)")
    # The one reader: OURS is the working copy a shell may have saved with a
    # BOM, which read strictly hid its stamp (`ours is [unstamped]`, exit 3).
    saved = [RatchetFile.read(Path(f), required=True) for f in files]
    texts = _mergeable_texts(saved)
    merged = merge_ratchets(*(_ratchet_or_die(t, f) for t, f in zip(texts, files)))
    # Merging adds no number: OURS keeps the stamps all three sides share.
    saved[1].publish(saved[1].kept(merged))
    print(f"ratchet merge: {len(merged)} mark(s)")
    return 0


def _mergeable_texts(saved: list) -> list[str]:
    """The three sides' texts, once they share one metric stamp and one key format."""
    texts = [side.text or "" for side in saved]
    _merge_stamp(texts)
    _merge_key_version(texts)
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


def _prune_renames(root: Path, store: SnapshotStore) -> dict[str, str]:
    """Renames a mark could have lived through, as one tree-to-tree diff.

    Anchored at the store's FIRST run: a mark can only have been seeded from a
    run, so no mark era starts before it, and rename detection compares two trees
    rather than walking history, so the widest window costs the same as a narrow
    one and cannot invent a pairing. An anchor a rebase rewrote away yields no
    renames and prune drops exactly as it did before.
    """
    from ..errors import GitError
    from ..gitio import renamed_paths

    runs = store.list_runs()
    if not runs:
        return {}
    try:
        return renamed_paths(root, runs[0]["commit"])
    except GitError:
        return {}


def _pruned(root: Path, store: SnapshotStore, prior: list, fresh: list) -> tuple[list, str]:
    """Prune, renames first: a file git moved is a relocated mark, not repaid debt."""
    from ..ratchet import follow_renames, prune_ratchet

    followed, moved = follow_renames(prior, fresh, _prune_renames(root, store))
    entries, dropped = prune_ratchet(followed, fresh)
    return entries, f"pruned {dropped}, followed {moved} rename(s)"


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


def _policy_findings(cfg, report: dict, enforce: bool) -> list | None:
    """The debt-policy findings, or None when no policy was evaluated.

    None, not []: without --enforce, or with no debt knobs in [crapkit] to judge
    by, nothing looked at the debt at all. [] then says "policy clean" about a
    policy that does not exist.
    """
    from ..ratchet_report import policy_violations

    knobs = (cfg.debt_max_age_months, cfg.repayment_min_per_30d)
    if not enforce or all(k is None for k in knobs):
        return None
    return policy_violations(report, *knobs)


def _ratchet_report(root: Path, cfg, as_json: bool, enforce: bool) -> int:
    from ..gitio import file_log_patches
    from ..ratchet_report import mark_events, report_from_events

    events = mark_events(file_log_patches(root, cfg.ratchet_file))
    report = report_from_events(events, working=_working_marks(root, cfg.ratchet_file))
    violations = _policy_findings(cfg, report, enforce)
    if as_json:
        _print_json({**report, "policy_violations": violations})
    else:
        _print_ratchet_report(report, violations or [], cfg.ratchet_file)
    return 1 if violations else 0


def cmd_ratchet(args: argparse.Namespace) -> int:
    if args.action == "merge":  # a git merge driver runs with no crapkit.toml in sight
        return _ratchet_merge(args.files)
    root = _command_root(args.repo)
    cfg = _load_repo_config(root)
    if args.action == "report":
        return _ratchet_report(root, cfg, args.json, args.enforce)
    if args.action == "move":  # a hand-declared rename needs no run to follow
        return _ratchet_move(root, cfg, args.files, _stand(args.repo))
    return _ratchet_from_run(root, cfg, args.action)


def _ratchet_from_run(root: Path, cfg, action: str) -> int:
    """seed and prune: both work from the run verify would compare against.

    seed signs that run's numbers, so the marks take the metric the run was
    measured under. prune adds no number, so the recorded stamp stays; a file
    prune creates holds no mark and takes the running metric, which relabels
    nothing.
    """
    from ..ratchet import metric_version
    from ..ratchetfile import RatchetFile
    from ._shared import _check_ratchet_identity

    store = _open_store(root)
    latest, skipped = _latest_full_run(store)
    fresh = store.read_scored(latest["id"])
    saved = RatchetFile.read(root / cfg.ratchet_file)
    key_version = _check_ratchet_identity(saved.text or "", root, cfg.ratchet_file, fresh, store)
    if action == "seed":
        entries, note = _seeded(saved.entries, fresh, cfg)
        text = saved.reseeded(entries, _seed_metric(latest), keys=key_version)
    else:
        entries, note = _pruned(root, store, saved.entries, fresh)
        text = saved.kept(entries, keys=key_version, new_file_metric=metric_version())
    _publish_checked(saved, text, entries, fresh, latest["tool_versions"].get("analysis_version"))
    metric_note = _metric_note(latest, action, created=saved.text is None)
    print(f"{cfg.ratchet_file}: {note} - {len(entries)} mark(s) vs run {latest['id']} "
          f"({latest['commit'][:11]}){_skip_note(skipped)}{metric_note}")
    return 0


def _seeded(prior: list, fresh: list, cfg) -> tuple[list, str]:
    from ..ratchet import seed_ratchet

    entries, added, tightened = seed_ratchet(prior, fresh, target=cfg.target,
                                             scope_targets=cfg.scope_targets)
    return entries, f"added {added}, tightened {tightened}"


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


def _metric_note(run: dict, action: str, *, created: bool) -> str:
    """Said only when the run seed or prune read is not this crapkit's metric."""
    from ..ratchet import metric_version, run_stamp

    measured, running = run_stamp(run["tool_versions"]), metric_version()
    if measured == running:
        return ""
    said = f"[{measured}]" if measured else "an unrecorded metric"
    return (f"; run {run['id']} was measured under {said}, not this crapkit's [{running}]"
            f"{_stamp_consequence(action, created)}")


def _stamp_consequence(action: str, created: bool) -> str:
    """What the older run means for the stamp the write left.

    A file prune creates recorded no stamp to keep, so there is nothing to say.
    """
    if action == "seed":
        return f", so verify refuses these marks until a fresh `{_self()} coverage` and another seed"
    return "" if created else ", and the marks keep their recorded stamp"


def _publish_checked(saved, text: str, entries: list, fresh: list, analysis_version) -> None:
    from ..ratchet import check_reader_version, checked_key_version

    try:
        check_reader_version(entries, analysis_version)
        checked_key_version(text, fresh)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    saved.publish(text)
