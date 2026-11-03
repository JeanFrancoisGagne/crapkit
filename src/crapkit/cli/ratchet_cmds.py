"""The `ratchet` subcommand's five actions and the burn-down report behind
`ratchet report`: seed new debt, prune marks whose code left (renames followed
first), merge two marks files as a git merge driver, move marks or re-key one
function's mark at their recorded values, and report ages and repayment from the
marks file's history."""
from __future__ import annotations

import argparse
from dataclasses import replace
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from ..errors import ConfigError, CrapkitError
from ..invocation import _self
from ..named import first_few
from ..store import SnapshotStore, anywhere
from ._shared import (_command_root, _load_ratchet_or_die, _load_repo_config, _open_store,
                      _print_json, _ratchet_or_die, _repo_relative, _stand, behind_head)

if TYPE_CHECKING:
    from ..ratchet import MetricStamp, ScopeStamps, StampMerge


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


def _ratchet_merge(files: list) -> int:
    from ..ratchet import merge_ratchets
    from ..ratchetfile import RatchetFile

    if len(files) != 3:
        raise ConfigError("ratchet merge takes exactly three files: BASE OURS THEIRS (git %O %A %B)")
    # The one reader: OURS is the working copy a shell may have saved with a
    # BOM, which read strictly hid its stamp (`ours is [unstamped]`, exit 3),
    # or as UTF-16, which OURS stays when it is written back.
    saved = [RatchetFile.read(Path(f), required=True) for f in files]
    texts, stamps = _mergeable_texts(saved)
    sides = [_ratchet_or_die(t, f) for t, f in zip(texts, files)]
    merged = merge_ratchets(*sides)
    saved[1].publish(_merged_text(saved, merged, stamps.stamp))
    print(f"ratchet merge: {len(merged)} mark(s)")
    _print_split_moves(sides)
    _print_stamp_notes(stamps.came_from, sides)
    return 0


def _print_split_moves(sides: list) -> None:
    """One stderr line per mark OURS and THEIRS moved to different keys. The
    merge keeps it under both, since the driver cannot tell which key a
    function holds, and the next prune drops the other."""
    from ..ratchet import split_moves

    for keys in split_moves(*sides):
        named = " and ".join(f'{path} "{name}"' for path, name in keys)
        print(f"ratchet merge: OURS and THEIRS moved one mark to different keys, so it stays under both: "
              f"{named}; the next {_self()} ratchet prune drops the one no function holds", file=sys.stderr)


def _merged_text(saved: list, merged: list, stamp: MetricStamp) -> str:
    """OURS's new text: the merged marks under the merged stamp. Merging adds no
    number, so a stamp OURS already records stays as its lines read. A stamp the
    merge took in part from THEIRS is one OURS does not record, so OURS is written
    as a file that records none yet, which takes it. THEIRS's rows reach OURS, so a
    byte its read replaced refuses the write; BASE only tells the changes apart."""
    from ..ratchet import read_key_version

    ours, theirs = saved[1], saved[2]
    if stamp == ours.metric_stamp:
        return ours.kept(merged, read_with=(theirs,))
    return replace(ours, text=None).kept(merged, keys=read_key_version(ours.text or ""),
                                         new_file_stamp=stamp, read_with=(theirs,))


def _print_stamp_notes(came_from: tuple, sides: list) -> None:
    """One stderr line per scope whose stamp came from one side while the other
    side changed marks. The driver cannot tell which scope a mark sits in, and a
    mark that side tightened under the old rules now sits under the new stamp."""
    from ..ratchet import changed_marks

    changed = {"OURS": changed_marks(sides[0], sides[1]), "THEIRS": changed_marks(sides[0], sides[2])}
    for scope, side in came_from:
        other = "THEIRS" if side == "OURS" else "OURS"
        if changed[other]:
            print(f"ratchet merge: scope {scope}'s stamp came from {side} and {other} changed "
                  f"{changed[other]} mark(s); if verify names {scope}, run {_self()} ratchet seed "
                  f"--scope {scope}", file=sys.stderr)


def _mergeable_texts(saved: list) -> tuple[list[str], StampMerge]:
    """The three sides' texts once they share one key format, and the stamp their merge records.

    A blank BASE is no common ancestor. Two branches that each created the file
    (two first seeds) meet in an add/add merge, and git hands the driver an empty
    %O. Read as a file, it had key version 0 and refused the merge as a legacy
    mapping. Out of the checks, it merges OURS and THEIRS as a union, each shared
    key at the lower mark, and BASE records no stamp."""
    from ..ratchet import merge_stamps, read_stamp

    texts = [side.text or "" for side in saved]
    try:
        stamps = merge_stamps(*(read_stamp(text) for text in texts))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    _merge_key_version(texts[1:] if saved[0].blank else texts)
    return texts, stamps


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


def _ratchet_move(root: Path, cfg, files: list, cwd: Path | None, function: list | None = None) -> int:
    """`cwd` is where the user typed the paths (`_stand`), None under --repo.
    It has no default: leaving it out read every path root-relative from any
    directory, and nothing said so. FUNCTION is `--function PATH OLD NEW`, the
    form that re-keys one mark instead of re-pathing marks."""
    from ..ratchet import move_marks
    from ..ratchetfile import RatchetFile

    if function is not None:
        return _ratchet_move_function(root, cfg, files, function, cwd)
    if len(files) != 2:
        raise ConfigError("ratchet move takes exactly two paths, OLD NEW, or --function PATH OLD NEW "
                          "to re-key one function's mark")
    old, new = (_move_path(raw, root, cwd) for raw in files)
    saved = RatchetFile.read(root / cfg.ratchet_file)
    entries, moved = move_marks(saved.entries, old, new)
    if not moved:
        raise ConfigError(f"ratchet move: no mark under {old} in {cfg.ratchet_file} "
                          "(a directory must end in '/')")
    saved.publish(saved.kept(entries))  # values never change, so both stamps stay
    print(f"{cfg.ratchet_file}: moved {moved} mark(s) from {old} to {new}")
    return 0


_FUNCTION_MOVE = "ratchet move --function"


def _ratchet_move_function(root: Path, cfg, files: list, function: list, cwd: Path | None) -> int:
    """`ratchet move --function PATH OLD NEW`: the mark OLD names in PATH, re-keyed
    to the function NEW names in the working tree's PATH, at its recorded value.

    It reads PATH and the marks file only, so it works right after an edit no
    run measured: one lizard parse and no store. The key goes through
    `rekey_marks`, the one writer of a mark's key."""
    from ..ratchetfile import RatchetFile

    if files:
        raise ConfigError("ratchet move takes OLD NEW or --function PATH OLD NEW, not both")
    raw, old_name, new_name = function
    path = _function_path(raw, root, cwd)
    saved = RatchetFile.read(root / cfg.ratchet_file)
    old = _marked_key(path, old_name, saved.entries)
    new = _function_key(path, new_name, _parsed(root, path))
    saved.publish(saved.kept(_rekeyed(saved.entries, old, new)))  # the value moves as written
    print(f"{cfg.ratchet_file}: re-keyed 1 mark in {path} from {old[1]} to {new[1]}")
    return 0


def _function_path(raw: str, root: Path, cwd: Path | None) -> str:
    """PATH read as every `ratchet move` path is, refused when it names a
    directory: a mark's key is one function in one file."""
    path = _move_path(raw, root, cwd)
    if path.endswith("/"):
        raise ConfigError(f"{_FUNCTION_MOVE}: {path} ends in '/', which names a directory; the "
                          "function form re-keys a mark in one file, so name the file, or drop "
                          "--function to move every mark under the directory")
    return path


def _quoted_names(names) -> str:
    return ", ".join(f'"{name}"' for name in names)


def _marked_key(path: str, name: str, entries: list) -> tuple[str, str]:
    """The one mark OLD names among PATH's marks, or the refusal that lists them."""
    from ..keys import MarkIndex

    marked = sorted(key for key in MarkIndex(entries).keys() if key[0] == path)
    hits = _marks_named(path, marked, name)
    if len(hits) != 1:
        raise ConfigError(_old_refusal(path, name, marked, hits))
    return hits[0]


def _marks_named(path: str, marked: list, name: str) -> list:
    """The marks NAME names: the mark under its whole key name, else each mark
    whose function NAME names as keys reads a NAME, where `#N` picks the Nth
    twin and a name with no `#N` names every twin."""
    if (path, name) in marked:
        return [(path, name)]
    return [key for key in marked if _names_mark(key[1], name)]


def _names_mark(key_name: str, name: str) -> bool:
    from ..keys import named_by, split_ordinal

    wanted, ordinal = split_ordinal(name)
    long_name, held = split_ordinal(key_name)
    return named_by(long_name, wanted) and (wanted == name or held == ordinal)


def _old_refusal(path: str, name: str, marked: list, hits: list) -> str:
    if hits:
        return (f"{_FUNCTION_MOVE}: {name} names {len(hits)} marks in {path}: "
                f"{_quoted_names(key for _, key in hits)}; name one of them as OLD")
    listed = (f"its marks are {_quoted_names(key for _, key in marked)}; name one of them as OLD"
              if marked else f"{path} holds no mark; name the file whose mark moves as PATH")
    return f"{_FUNCTION_MOVE}: no mark named {name} in {path}; {listed}"


def _parsed(root: Path, path: str) -> list:
    """PATH's functions as the working tree holds them, from one silent lizard
    parse: the command prints its one line and no twin note.

    `keys.select` ranks a bare name's twins by CRAP, and a parse scores
    nothing, so every row carries none and ranks as unscored."""
    from types import SimpleNamespace

    from ..analyze import _source_records, decode_source

    try:
        raw = (root / path).read_bytes()
    except OSError as exc:
        raise ConfigError(f"{_FUNCTION_MOVE}: cannot read {path} ({exc.strerror or exc}); NEW names "
                          f"a function in the working tree's {path}") from exc
    return [SimpleNamespace(**record._asdict(), crap=None)
            for record in _source_records(path, decode_source(raw))]


def _function_key(path: str, name: str, rows: list) -> tuple[str, str]:
    """The key of the one function NEW names in PATH, or the refusal that lists
    the handles: of PATH's functions when NEW names none, of the ones it names
    when it names several."""
    from ..keys import handles, key_names, key_of, lookup

    found = handles(rows)
    named = _named_rows(rows, name)
    picked = sorted({found[lookup(row)] for row in named})
    if len(picked) != 1:
        raise ConfigError(_new_refusal(path, name, picked, sorted(set(found.values()))))
    return key_of(key_names(rows), named[0])


def _named_rows(rows: list, name: str) -> list:
    """The functions NEW names: the function whose whole key name NEW is, as
    OLD reads one, so the first twin's key that keys' refusal prints names that
    twin; else `keys.select`'s pick, widened to every twin of it when NEW is a
    name with no `#N`. select's worst twin is a ranking, and a re-key has to
    name one function."""
    from ..keys import split_ordinal

    keyed = _rows_keyed(rows, lambda pair: pair[1] == name)
    if keyed:
        return keyed
    picked = _selected(rows, name)
    if name.isdigit() or split_ordinal(name)[0] != name:
        return _rows_keyed(rows, picked.__contains__)
    return _twins_of(rows, picked)


def _selected(rows: list, name: str) -> set:
    """`keys.select`'s (long name, key name) pairs; its refusal of a start line
    several functions open on, which lists their handles, exits 3 here."""
    from ..keys import select

    try:
        return set(select(rows, name))
    except CrapkitError as exc:
        raise ConfigError(f"{_FUNCTION_MOVE}: {exc}") from exc


def _rows_keyed(rows: list, wanted) -> list:
    """The rows whose (long name, key name) pair WANTED accepts."""
    from ..keys import key_names, lookup

    names = key_names(rows)
    return [row for row in rows if wanted((row.long_name, names[lookup(row)]))]


def _twins_of(rows: list, picked: set) -> list:
    long_names = {long_name for long_name, _ in picked}
    return [row for row in rows if row.long_name in long_names]


def _new_refusal(path: str, name: str, picked: list, every: list) -> str:
    if picked:
        return (f"{_FUNCTION_MOVE}: {name} names {len(picked)} functions in {path}: "
                f"{', '.join(picked)}; name one of them as NEW")
    return (f"{_FUNCTION_MOVE}: {name} names no function in {path}; its functions are "
            f"{', '.join(every) or 'none'}; name one of them as NEW")


def _rekeyed(entries: list, old: tuple[str, str], new: tuple[str, str]) -> list:
    """The marks with OLD's mark under NEW, refused when NEW's key holds a mark."""
    from ..ratchet import rekey_marks

    try:
        return rekey_marks(entries, [(old, new)])[0]
    except ValueError as exc:
        raise ConfigError(f"{_FUNCTION_MOVE}: {exc}; name a function that holds no mark as NEW, or "
                          "leave the old mark for `crapkit ratchet prune` once a run no longer "
                          "holds its function") from exc


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
    from ..ratchet_report import crap_by_key

    return crap_by_key(_load_ratchet_or_die(root / ratchet_file, ratchet_file))


def _report_basis(root: Path, ratchet_file: str) -> tuple[list, dict | None]:
    """The revisions the report replays and the marks it reads as open.

    A marks file that is missing or holds only blank lines is not a repo that
    repaid every mark: the report reads the marks the newest commit that held
    any held as open (working None, the committed state), and the commit that
    deleted or emptied the file repays none (held_history). verify reads the
    same marks only while its baseline run comes from before that commit.
    Otherwise HEAD's revision is the committed state, so the report counts as
    uncommitted only the marks the working tree and HEAD disagree on."""
    from ..marks_history import held_history, marks_history
    from ..ratchetfile import RatchetFile

    if RatchetFile.read(root / ratchet_file).blank:
        return held_history(root, ratchet_file), None
    return marks_history(root, ratchet_file), _working_marks(root, ratchet_file)


def _warn_marks_stand_in(root: Path, ratchet_file: str, report: dict, working) -> None:
    """One line when the open marks came from history, not from the file.

    verify's stand-in searches from its baseline run's commit to HEAD, so a
    coverage run on the deleting commit or a later one leaves verify no marks
    while the report still reads them as open. The line says when verify
    reads them; making the two agree is a change to verify, not to this line."""
    if working is not None or not report["open"]:
        return
    state, act, done = (("empty", "emptying", "emptied") if (root / ratchet_file).exists()
                        else ("missing", "deleting", "deleted"))
    print(f"warning: {ratchet_file} is {state}, so the report reads the {report['open']} "
          f"mark(s) its history last committed as open, and {act} the file repays none. "
          "verify judges against them only while its baseline run measured a commit from "
          f"before the file was {done}. `git log -- {ratchet_file}` shows the commits that "
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
    from ..ratchet_report import held_event, mark_events, report_from_events

    shallow = shallow_checkout(root)
    _refuse_a_cut_history(cfg, enforce, shallow)
    revisions, working = _report_basis(root, cfg.ratchet_file)
    report = report_from_events(mark_events(revisions) + held_event(revisions), working=working)
    violations = _policy_findings(cfg, report, enforce)
    _warn_history(shallow)
    _warn_marks_stand_in(root, cfg.ratchet_file, report, working)
    if as_json:
        _print_json({**report, "policy_violations": violations, "shallow": shallow})
    else:
        _print_ratchet_report(report, violations or [], cfg.ratchet_file)
    return 1 if violations else 0


def cmd_ratchet(args: argparse.Namespace) -> int:
    scope = getattr(args, "scope", None)  # a Namespace built by hand may predate --scope
    requested = _requested_run(args, scope)
    function = _function_form(args)
    if args.action == "merge":  # a git merge driver runs with no crapkit.toml in sight
        return _ratchet_merge(args.files)
    root = _command_root(args.repo)
    cfg = _load_repo_config(root)
    if args.action == "report":
        return _ratchet_report(root, cfg, args.json, args.enforce)
    if args.action == "move":  # a hand-declared rename needs no run to follow
        return _ratchet_move(root, cfg, args.files, _stand(args.repo), function)
    return _ratchet_from_run(root, cfg, args.action, requested, _known_scope(cfg, scope))


def _function_form(args: argparse.Namespace) -> list | None:
    """`--function PATH OLD NEW`, which only move takes. A Namespace built by
    hand may predate the flag."""
    function = getattr(args, "function", None)
    if function is not None and args.action != "move":
        raise ConfigError(f"ratchet {args.action} re-keys no mark, so it takes no --function; "
                          "only move does")
    return function


def _requested_run(args: argparse.Namespace, scope: str | None = None) -> int | None:
    """`--baseline ID`, which only the two actions that read a run take, and
    `--scope NAME`, which only seed takes.

    Ignoring either elsewhere would let `ratchet report --baseline 3` read as a
    report about run 3.
    """
    if args.baseline is not None and args.action not in ("seed", "prune"):
        raise ConfigError(f"ratchet {args.action} reads no run, so it takes no --baseline")
    if scope is not None and args.action != "seed":
        raise ConfigError(f"ratchet {args.action} signs no scope's stamp, so it takes no --scope; "
                          "only seed does")
    return args.baseline


def _known_scope(cfg, scope: str | None) -> str | None:
    """`--scope NAME`, refused when the config declares no scope by that name."""
    names = [declared.name for declared in cfg.scopes]
    if scope is not None and scope not in names:
        raise ConfigError(f"ratchet seed --scope {scope}: crapkit.toml declares no scope {scope!r}; "
                          f"its scopes are {', '.join(names)}")
    return scope


def _ratchet_from_run(root: Path, cfg, action: str, requested: int | None,
                      scope: str | None = None) -> int:
    """seed and prune: both work from the run verify would compare against,
    or from the run `--baseline` names.

    seed signs that run's numbers, so each scope takes the stamp the run
    recorded for it; `scope` keeps the seed to one scope's marks and line.
    prune adds no number, so the recorded stamp stays; a file prune creates
    holds no mark and takes the running per-scope stamp, which relabels nothing.
    """
    from ..gitio import GitFacts
    from ..keys import require_unambiguous
    from ..ratchet import running_stamp
    from ..ratchetfile import RatchetFile
    from ._shared import _check_ratchet_identity

    store = _open_store(root)
    work = _latest_full_run(store, requested, behind=behind_head(GitFacts(root)))
    latest = work.run
    fresh = store.read_scored(latest["id"])
    require_unambiguous(fresh, run_id=latest["id"], advice=_identity_advice(work, action))
    saved = RatchetFile.read(root / cfg.ratchet_file)
    _refuse_newer_marks(saved, work, action, running_stamp(cfg))
    marks = saved.entries
    key_version = _check_ratchet_identity(saved.text or "", root, cfg.ratchet_file, fresh, store,
                                          entries=marks, moves_marks=True)
    if action == "seed":
        entries, note, text = _seed(saved, _SeedRead(work, fresh, key_version), cfg, scope)
    else:
        entries, note = _pruned(root, store, marks, fresh)
        text = saved.kept(entries, keys=key_version, new_file_stamp=running_stamp(cfg))
    _publish_checked(saved, text, entries, fresh, latest["tool_versions"])
    # A scoped seed refused a run that measured its scope under other rules.
    metric_note = "" if scope else _metric_note(work, action, cfg, created=saved.text is None)
    print(f"{cfg.ratchet_file}: {note} - {len(entries)} mark(s) vs run {latest['id']} "
          f"({latest['commit'][:11]}){_skip_note(work.skipped, work.newer)}{metric_note}")
    _print_seed_next(action, cfg.ratchet_file, metric_note)
    return 0


# What seed or prune would do to marks from a run an older metric measured.
_BACKWARDS = {"seed": "this seed would restamp them under the older metric, and verify under the "
                      "newer one would refuse them",
              "prune": "this prune would drop every mark whose function the older reader names "
                       "differently, as if its code were gone"}


def _refuse_newer_marks(saved, work: _WorkRun, action: str, running: MetricStamp) -> None:
    """Refuse to rewrite marks a newer crapkit recorded than the one that
    measured the run. seed restamped the file under the run's older metric,
    and prune judged which marks were gone by the older reader's names. Two
    ways to get there: this install is older than the marks (`running`, its
    per-scope stamp, records a lower number: upgrade it), or a failed verify
    pins seed and prune to a run an older release measured (read a newer run)."""
    from ..ratchet import run_stamp

    if _newer_than(saved, running):
        raise ConfigError(_newer_than_install(saved, action, running))
    measured = run_stamp(work.run["tool_versions"])
    if _newer_than(saved, measured):
        raise ConfigError(f"ratchet {action} refused: {saved.path.name} was recorded under "
                          f"[{saved.metric_stamp.render()}] and run {work.run['id']} under the older "
                          f"[{measured.render()}]; "
                          f"{_BACKWARDS[action]}; {_way_off(work.newer)}")


def _newer_than(saved, measured: MetricStamp) -> bool:
    """The file records a higher number than `measured` in a scope `measured`
    holds, as verify weighs two stamps: a seed would sign that scope backwards."""
    from ..ratchet import ScopeStamps, stamp_conflict

    scopes = frozenset(measured.scopes) if isinstance(measured, ScopeStamps) else frozenset()
    conflict = stamp_conflict(saved.metric_stamp, measured, scopes)
    return bool(conflict and conflict.newer)


def _newer_than_install(saved, action: str, running: MetricStamp) -> str:
    from ..ratchet import upgrade_remedy

    return (f"ratchet {action} refused: {saved.path.name} was recorded under "
            f"[{saved.metric_stamp.render()}] and this crapkit measures "
            f"[{running.render()}] - "
            f"{upgrade_remedy(['crapkit'])}; {_BACKWARDS[action]}. A team going back to this release "
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


class _SeedRead(NamedTuple):
    """What seed read before it signs: the run, its scored rows and the key format proven."""
    work: _WorkRun
    fresh: list
    key_version: int


def _seed(saved, read: _SeedRead, cfg, scope: str | None) -> tuple[list, str, str]:
    """seed's marks, the counts its line prints, and the text it writes. A
    scope whose stamp moved in a way `ratchet.SEED_RAISES` lets rise takes the
    run's values; every other mark only falls."""
    entries, note = _seeded(saved.entries, read.fresh, cfg, scope, _rising(saved, read.work.run, cfg))
    _refuse_unkeyable_twins(cfg.ratchet_file, read.work, saved.entries, entries, read.fresh,
                            read.key_version)
    run_stamps = _signed_stamps(saved, read.work, cfg, scope)
    return entries, note, saved.reseeded(entries, run_stamps, scope=scope, keys=read.key_version)


def _rising(saved, run: dict, cfg) -> frozenset[str]:
    """The scopes whose marks this seed lets rise, by how each marked scope's
    stamp moved from the file's to the run's. A run with no per-scope stamp
    raises nothing: `_signed_stamps` refuses it once the twins are judged."""
    from ..ratchet import ScopeStamps, rising_scopes, run_stamp, stamp_conflict
    from .verifying import marked_scopes

    stamps = run_stamp(run["tool_versions"])
    if not isinstance(stamps, ScopeStamps):
        return frozenset()
    return rising_scopes(stamp_conflict(saved.metric_stamp, stamps, marked_scopes(saved.entries, cfg)))


def _signed_stamps(saved, work: _WorkRun, cfg, scope: str | None):
    """The stamp seed signs: the run's, for the scopes the config declares. A
    scoped seed signs one scope's line under the file's lizard, so the file must
    record this crapkit's lizard and the run must measure the scope as this crapkit does."""
    from ..ratchet import ScopeStamps, running_stamp

    stamps = _seed_metric(work.run, scope)
    if scope is not None:
        running = running_stamp(cfg)
        _refuse_kept_lizard(saved, work, running, scope)
        _refuse_restamping(work, stamps, running, scope)
    names = {declared.name for declared in cfg.scopes}
    return ScopeStamps(stamps.lizard, {name: stamp for name, stamp in stamps.scopes.items() if name in names})


def _refuse_kept_lizard(saved, work: _WorkRun, running, scope: str) -> None:
    """Refuse `seed --scope` on a file whose lizard this crapkit does not run.
    The scoped seed keeps the file's lizard, so verify would refuse every scope
    again; the plain seed signs all of them under the run's lizard."""
    from ..ratchet import rescoped

    kept = rescoped(saved.metric_stamp, running, scope).lizard
    if kept == running.lizard:
        return
    baseline = f" --baseline {work.run['id']}" if work.named else ""
    raise ConfigError(f"ratchet seed --scope {scope} refused: {saved.path.name} records lizard {kept} "
                      f"and this crapkit runs lizard {running.lizard}; one lizard version signs every "
                      f"scope and a scoped seed keeps the file's, so verify would refuse the marks "
                      f"again; run `{_self()} ratchet seed{baseline}`, which signs every scope")


def _refuse_restamping(work: _WorkRun, stamps, running, scope: str) -> None:
    """Refuse `seed --scope` from a run that measured the scope under other
    rules: the line it signs would leave verify refusing the scope again."""
    from ..ratchet import scope_record

    if stamps.lizard == running.lizard and stamps.scopes[scope] == running.scopes[scope]:
        return
    raise ConfigError(f"ratchet seed --scope {scope} refused: run {work.run['id']} measured scope "
                      f"{scope} under [{scope_record(stamps, scope)}], not this crapkit's "
                      f"[{scope_record(running, scope)}], so verify would refuse the marks it "
                      f"signs; seed from a run this crapkit measured: "
                      f"{_way_off(_measured_here(work.newer, running))}")


def _seeded(prior: list, fresh: list, cfg, scope: str | None, rising: frozenset) -> tuple[list, str]:
    """seed's marks and its counts: `scope` keeps it to the paths the config
    places there, and the marks in `rising` scopes take the run's values."""
    from ..ratchet import raised_marks, seed_ratchet
    from ..universe import owning_scope, scope_matchers

    matchers = scope_matchers(cfg.scopes)
    admits = None if scope is None else (lambda path: owning_scope(path, matchers) == scope)
    rises = (lambda path: owning_scope(path, matchers) in rising) if rising else None
    entries, added, tightened = seed_ratchet(prior, fresh, target=cfg.target,
                                             scope_targets=cfg.scope_targets, scope=admits, raises=rises)
    head = "" if scope is None else f"scope {scope}: "
    return entries, f"{head}added {added}, tightened {tightened}{_raised_note(raised_marks(prior, entries))}"


def _raised_note(raised: list) -> str:
    """`, raised R (first <path> <key name> <old> -> <new>)`; "" when no mark rose."""
    if not raised:
        return ""
    entry, old = raised[0]
    return f", raised {len(raised)} (first {entry.path} {entry.long_name} {old:.4f} -> {entry.crap:.4f})"


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
    from ..keys import mark_key
    from ..ratchet import unseen_marks

    run_id = work.run["id"]
    unseen = [mark_key(entry) for entry in unseen_marks(prior, fresh)]
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


def _seed_metric(run: dict, scope: str | None = None) -> ScopeStamps:
    """The stamp seed signs with: the per-scope stamp its run was measured under.

    A run that recorded none, or none for `scope`, cannot vouch for that
    scope's rules, and stamping the running ones is how old numbers passed
    for new ones.
    """
    from ..ratchet import run_stamp

    recorded = run["tool_versions"].get("scope_stamps", {})
    if recorded and (scope is None or scope in recorded):
        return run_stamp(run["tool_versions"])
    raise ConfigError(_no_scope_stamp(run, scope))


def _no_scope_stamp(run: dict, scope: str | None) -> str:
    what = "per scope" if scope is None else f"for scope {scope}"
    return (f"ratchet seed: run {run['id']} recorded no metric {what} (its analysis, lizard and "
            f"coverage rules), so the marks it measured cannot be stamped; run `{_self()} coverage` "
            "and seed again")


def _metric_note(work: _WorkRun, action: str, cfg, *, created: bool) -> str:
    """Said only when the run seed or prune read measured a scope under other
    rules than this crapkit runs there, naming each such scope."""
    from ..ratchet import run_stamp, running_stamp

    measured, running = run_stamp(work.run["tool_versions"]), running_stamp(cfg)
    off = _off_scopes(measured, running)
    if not off:
        return ""
    return (f"; run {work.run['id']} {_measured_under(measured, running, off)}"
            f"{_stamp_consequence(work, action, running, created)}")


def _off_scopes(measured, running) -> list[str]:
    """The scopes `measured` records otherwise than `running`, by verify's comparison."""
    from ..ratchet import stamp_conflict

    if not measured:
        return list(running.scopes)
    conflict = stamp_conflict(measured, running, frozenset(running.scopes))
    return sorted(conflict.scopes) if conflict and conflict.refuses else []


def _measured_under(measured, running, off: list[str]) -> str:
    """`measured scope web under [X], not this crapkit's [Y]`, one clause per scope."""
    from ..ratchet import scope_record

    if not measured:
        return f"was measured under an unrecorded metric, not this crapkit's rules for scope(s) {', '.join(off)}"
    return "measured " + ", and ".join(f"scope {name} under [{scope_record(measured, name)}], not this "
                                       f"crapkit's [{scope_record(running, name)}]" for name in off)


def _stamp_consequence(work: _WorkRun, action: str, running, created: bool) -> str:
    """What the older run means for the stamp the write left.

    A file prune creates recorded no stamp to keep, so there is nothing to say.
    """
    if action == "seed":
        return f", so verify refuses these marks until {_restamping_seed(work, running)}"
    return "" if created else ", and the marks keep their recorded stamp"


def _restamping_seed(work: _WorkRun, running) -> str:
    """The seed that replaces the stamps this one signed.

    A fresh coverage run is the run the next plain seed reads only when nothing
    holds seed where it is. Behind a failed verify, or under `--baseline`, the
    next plain seed reads this run again and signs the same old stamps, so the
    clause names a run this crapkit measured to pass instead.
    """
    if work.blocker is None and not work.named:
        return f"a fresh `{_self()} coverage` and another seed"
    return f"a seed from a run this crapkit measured: {_way_off(_measured_here(work.newer, running))}"


def _measured_here(run: dict | None, running) -> dict | None:
    """`run` when it measured every scope as `running` does, so a seed from it signs the running rules."""
    from ..ratchet import run_stamp

    if run is not None and run_stamp(run["tool_versions"]) == running:
        return run
    return None


def _publish_checked(saved, text: str, entries: list, fresh: list, tool_versions: dict) -> None:
    """Refuse to write `text`, which holds `entries`, when its marks cannot be
    keyed: the stamp of the run that measured them and the text's own stamp
    each prove the expression reader for every JavaScript-family mark's
    language."""
    from ..ratchet import check_reader_version, checked_key_version, run_stamp

    try:
        check_reader_version(entries, run_stamp(tool_versions))
        checked_key_version(text, fresh, entries=entries)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    saved.publish(text)
