"""The `ratchet` subcommand's five actions and the burn-down report behind
`ratchet report`: seed new debt, prune marks whose code left (renames followed
first), merge two marks files as a git merge driver, move marks at their
recorded values, and report ages and repayment from the marks file's history."""
from __future__ import annotations

import argparse
from pathlib import Path

from ..errors import ConfigError, CrapkitError
from ..store import SnapshotStore
from ._shared import _load_ratchet_or_die, _load_repo_config, _open_store, _print_json


def _is_failed_verify(run: dict) -> bool:
    return run["kind"] == "verify" and run["verdict_ok"] is False


def _skipped_failed_verifies(runs: list[dict], chosen_id: int) -> list[dict]:
    """The failed verifies newer than the run this seed or prune settled on."""
    return [r for r in runs if r["id"] > chosen_id and _is_failed_verify(r)]


def _latest_full_run(store: SnapshotStore) -> tuple[dict, list[dict]]:
    """The newest TRUSTED run to work from, and the failed verifies passed over.

    `verify`'s own rule, shared: a failed verify never serves as a baseline
    because it can carry the scores of a red tree, and the failure is visible in
    the same run. Seeding from one signs debt at values verify itself will not
    accept as a comparison point. Before this, seed took the newest run whose
    kind was coverage or verify and never read the verdict (#16).
    """
    from ..store import is_trusted

    runs = store.list_runs()
    trusted = [r for r in runs if is_trusted(r)]
    if not trusted:
        raise CrapkitError("no trusted full run to work from — run `crapkit coverage` first "
                           "(failed verifies and hook runs never serve as baselines)")
    return trusted[-1], _skipped_failed_verifies(runs, trusted[-1]["id"])


def _skip_note(skipped: list[dict]) -> str:
    """Why the line names an older run than the newest one in the store."""
    if not skipped:
        return ""
    ids = ", ".join(str(r["id"]) for r in skipped)
    return f", skipped failed verify {'runs' if len(skipped) > 1 else 'run'} {ids}"


def _merge_stamp(texts: list[str]) -> str:
    """The stamp the merged file keeps; the two sides must share one.

    Reconciling marks across metrics means picking a minimum between numbers
    produced by different rules, which is not a comparison at all.
    """
    from ..ratchet import read_stamp

    ours, theirs = read_stamp(texts[1]), read_stamp(texts[2])
    if ours != theirs:
        raise ConfigError(
            f"ratchet merge refused: ours is [{ours or 'unstamped'}] and theirs is "
            f"[{theirs or 'unstamped'}] — marks from different metric versions cannot "
            "merge; re-baseline one side with `crapkit ratchet seed`")
    return ours


def _ratchet_merge(files: list) -> int:
    from ..ratchet import dump_ratchet, load_ratchet, merge_ratchets

    if len(files) != 3:
        raise ConfigError("ratchet merge takes exactly three files: BASE OURS THEIRS (git %O %A %B)")
    texts = [Path(f).read_text(encoding="utf-8") for f in files]
    stamp = _merge_stamp(texts)
    merged = merge_ratchets(*(load_ratchet(t) for t in texts))
    Path(files[1]).write_text(dump_ratchet(merged, stamp=stamp), encoding="utf-8", newline="\n")
    print(f"ratchet merge: {len(merged)} mark(s)")
    return 0


def _ratchet_move(root: Path, cfg, files: list) -> int:
    from ..ratchet import dump_ratchet, move_marks

    if len(files) != 2:
        raise ConfigError("ratchet move takes exactly two paths: OLD NEW")
    ratchet_path = root / cfg.ratchet_file
    entries, moved = move_marks(_load_ratchet_or_die(ratchet_path, cfg.ratchet_file),
                                files[0], files[1])
    if not moved:
        raise ConfigError(f"ratchet move: no mark under {files[0]} in {cfg.ratchet_file} "
                          "(a directory must end in '/')")
    ratchet_path.write_text(dump_ratchet(entries), encoding="utf-8", newline="\n")
    print(f"{cfg.ratchet_file}: moved {moved} mark(s) from {files[0]} to {files[1]}")
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
    from ..ratchet import dump_ratchet, seed_ratchet

    if args.action == "merge":  # a git merge driver runs with no crapkit.toml in sight
        return _ratchet_merge(args.files)
    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    if args.action == "report":
        return _ratchet_report(root, cfg, args.json, args.enforce)
    if args.action == "move":  # a hand-declared rename needs no run to follow
        return _ratchet_move(root, cfg, args.files)
    store = _open_store(root)
    latest, skipped = _latest_full_run(store)
    fresh = store.read_scored(latest["id"])
    ratchet_path = root / cfg.ratchet_file
    prior = _load_ratchet_or_die(ratchet_path, cfg.ratchet_file)
    if args.action == "seed":
        entries, added, tightened = seed_ratchet(prior, fresh, target=cfg.target,
                                                 scope_targets=cfg.scope_targets)
        note = f"added {added}, tightened {tightened}"
    else:
        entries, note = _pruned(root, store, prior, fresh)
    ratchet_path.write_text(dump_ratchet(entries), encoding="utf-8", newline="\n")
    print(f"{cfg.ratchet_file}: {note} — {len(entries)} mark(s) vs run {latest['id']} "
          f"({latest['commit'][:11]}){_skip_note(skipped)}")
    return 0
