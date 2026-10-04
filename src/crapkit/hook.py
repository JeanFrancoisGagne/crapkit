"""Pre-commit gate: the staged functions, handed to the gate module to judge.

`gate_staged` reads the staged diff and blobs and returns each staged file a
scope takes as a `gate.ChangedFile`: its functions, each with what a blob can
say of its CRAP (`CrapBound`, ccn alone, no coverage), or why no reader could
read it, or, for a name that is not UTF-8, an `UnreadableName`. The CLI judges
the rows with `gate.judge` and maps the findings to the hook's exits.

Checks STAGED blobs, never the working tree, so unstaged noise cannot block a
clean commit and a dirty checkout cannot sneak past one.

The hook is a hot path measured in what a developer waits for at every `git
commit`, so it holds three rules the batch commands do not:

- One `git cat-file --batch` for all staged blobs, and each blob fetched once.
  Per-file `git show` spawns cost ~22ms each and made the floor scale with the
  commit size.
- No repo-wide analysis cache. Loading and rewriting it cost a flat 0.45s on an
  18 MB cache while the analysis it could save is milliseconds: a commit's worth
  of blobs is cheaper to analyze outright than to look up.
- A commit's worth of files is analyzed in this process, from the blobs already
  in memory: no worker pool below the crossover, and no temp tree to read back.
"""
from __future__ import annotations

import sys
import tempfile
from itertools import chain
from pathlib import Path
from typing import NamedTuple

from .analyze import analyze_jobs, analyze_sources, decode_source, unread_reasons
from .config import Config
from .diffparse import changed_ranges, reader_ranges
from .gate import WHOLE, ChangedFile, CrapBound, Function, Unread, UnreadableName
from .gitio import GitReads
from .gitpaths import readable
from .merge import FunctionRecord
from .score import flagged_crap
from .universe import _source_extensions, claimed_unreadable, exclude_matcher, excluded, scan_files

# A commit's worth of files, not an inventory's, and never more workers than a
# commit can keep busy: each worker re-imports lizard, which is the whole cost of
# a small pool. Measured here at 9 reps a point (serial vs pooled medians, ms):
# 4 files 17/120, 8 files 52/158, 12 files 112/175, 16 files 178/191, 20 files
# 197/184, 60 files 505/228. The arms only cross at 16, so that is the threshold;
# below it the pool spends ~100ms of spawn to save single-digit milliseconds.
_HOOK_POOL_THRESHOLD = 16
_HOOK_MAX_WORKERS = 8


class Violation(NamedTuple):
    path: str
    long_name: str
    start: int
    ccn: int
    # The ratchet key, carried from where the whole file's records were in hand:
    # the ordinal counts every function of that name in the file, and a gate
    # handed only the breaching ones would number the second twin as the first.
    key_name: str = ""
    # The ceiling the function was judged against: its scope's, which a scope
    # `target` can set below the repo's. The refusal printed the repo target, so
    # a ccn-6 breach of a target-5 scope read "exceed the complexity ceiling of
    # 6". None only on a Violation built by hand with no scope behind it.
    ceiling: int | None = None
    # The high end of the CRAP bound the gate judged it on, ccn^2 + ccn or ccn
    # in a cc-only scope: the mark an override grant writes.
    high: float | None = None


class StagedGate(NamedTuple):
    """The staged change as the gate module reads it, and what the hook says beside it."""
    changes: tuple[ChangedFile, ...] = ()  # each staged file a scope takes, for gate.judge
    unscoped: list[str] = []  # staged source files no scope claims: ungated, but never silently
    records: tuple = ()  # full staged identities, including siblings below the ceiling
    unreadable: tuple[str, ...] = ()  # staged names that are not UTF-8 and no scope takes
    whole: bool = False  # nothing was staged, so every tracked file was judged whole


def _touches(record: FunctionRecord, ranges: list[tuple[int, int]]) -> bool:
    return any(not (hi < record.start or lo > record.end) for lo, hi in ranges)


def _materialized(tmp: Path, blobs: dict[str, bytes]) -> list[tuple[str, str]]:
    """Write each staged blob under its own repo-relative path.

    The repo path, not the basename: src/a/index.ts and src/b/index.ts are one
    file at basename granularity, and analyzing one twice would gate the wrong
    content.
    """
    jobs = []
    for rel, blob in sorted(blobs.items()):
        staged = tmp / rel
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(blob)
        jobs.append((str(staged), rel))
    return jobs


def commit_sized(paths) -> bool:
    """A commit's worth of files: few enough to analyze in this process, with no
    pool and no cache, because lizard on each costs less than either would.
    `rescore` asks the same question of the files it names, and adds a byte
    budget of its own for large files."""
    return len(paths) < _HOOK_POOL_THRESHOLD


def staged_records(blobs: dict[str, bytes], *, worker_budget: int = 0) -> dict[str, list]:
    """Records for the staged blobs, pooled once a commit touches enough files.

    Below the pool threshold lizard is handed the blob text directly: the bytes
    are already in memory from `git cat-file --batch`, and a temp tree only to
    read them back costs a write and a read per file. The pooled arm still
    materializes, because a worker process reads its own files.
    """
    if commit_sized(blobs):
        return analyze_sources({rel: decode_source(blob) for rel, blob in sorted(blobs.items())})
    with tempfile.TemporaryDirectory() as tmp:
        jobs = _materialized(Path(tmp), blobs)
        return analyze_jobs(jobs, workers=min(len(jobs), _HOOK_MAX_WORKERS),
                            pool_threshold=_HOOK_POOL_THRESHOLD, chunksize=1, worker_budget=worker_budget)


def file_ceilings(cfg, in_scope, checked_files) -> dict[str, int]:
    """The ccn ceiling each file is judged against: its scope's, read through
    `Config.ceiling_of`. `rescore --gate` decides on this same map, so a
    mid-session verdict and the commit's cannot disagree."""
    scope_of = {f: scope for scope, files in in_scope.items() for f in files}
    return {rel: cfg.ceiling_of(scope_of.get(rel, "")) for rel in checked_files}


def _function(record: FunctionRecord, scope: str, flag: str) -> Function:
    """One staged function as the gate judges it. A blob carries no coverage, so
    its CRAP is at least ccn (full coverage) and at most what `score` gives it at
    coverage 0 under its scope's flag: ccn in a cc-only scope, ccn^2 + ccn
    elsewhere. The high end is what an override grant marks."""
    return Function(record.long_name, record.start, record.end,
                    CrapBound(record.ccn, flagged_crap(record.ccn, 0.0, flag)),
                    scope, record.occurrence, record)


def _changed_file(rel: str, records: list, spans, scope: str, cfg: Config, unread: dict) -> ChangedFile:
    """One staged file a scope takes. A file no reader could read is taken
    whole: a staged file nothing read is refused whatever lines the diff
    names."""
    if rel in unread:
        return ChangedFile(rel, WHOLE, Unread(rel, unread[rel]))
    flag = "cc-only" if scope in cfg.coverage_optional_scopes else "untested"
    return ChangedFile(rel, spans, tuple(_function(record, scope, flag) for record in records))


def _gate_blind_to(path: str, checked: set[str], exts: tuple, match) -> bool:
    """A staged file the gate cannot judge but a scope language claims by
    extension. Files the config EXCLUDES (test trees, exclude globs) are
    outside scopes on purpose and never a hole. An unreadable name is named
    left out instead, once."""
    return path not in checked and readable(path) and path.endswith(exts) and not excluded(path, match)


def _unscoped_sources(staged: list[str], checked: set[str], cfg: Config) -> list[str]:
    """The first new top-level directory a repo grows commits ungated; this is
    how that hole stays visible instead of silent."""
    exts = tuple(e for scope in cfg.scopes for e in _source_extensions(scope.languages))
    match = exclude_matcher(cfg.exclude_globs)
    return sorted(f for f in staged if _gate_blind_to(f, checked, exts, match))


def _scope_of(in_scope: dict) -> dict[str, str]:
    """Each file some scope claims, with that scope, sorted by path."""
    return dict(sorted((f, scope) for scope, files in in_scope.items() for f in files))


def gate_staged(root: Path, cfg: Config, reads=None, *, whole: bool = False) -> StagedGate:
    """`reads` is where the staged bytes come from: git processes the caller
    already started, or a spawn-on-demand pair when nobody did. The verdict is
    the same either way.

    `whole` lets an empty staged diff judge every tracked file whole instead of
    passing: the caller allows it outside a commit, where `pre-commit run
    --all-files` stages nothing and a staged-only gate passed any breach already
    committed. The blobs still come from the index, which then equals HEAD.
    """
    reads = reads or GitReads(root)
    staged = changed_ranges(reads.staged_diff())
    if staged or not whole:
        return _gate_ranges(cfg, reads, staged, staged)
    return _gate_ranges(cfg, reads, _whole_files(reads.tracked()), {})._replace(whole=True)


def _whole_files(paths: list[str]) -> dict[str, list[tuple[int, int]]]:
    """Every line of each file as its changed range, so every function is touched."""
    return {path: [(1, sys.maxsize)] for path in paths}


def _claimed_rows(claimed: list[tuple[str, str]], ranges_by_path: dict) -> tuple[ChangedFile, ...]:
    """Each staged name a scope takes that is not UTF-8, as the gate's input."""
    return tuple(ChangedFile(path, ranges_by_path[path], UnreadableName(path, scope)) for path, scope in claimed)


def _gate_ranges(cfg: Config, reads, ranges_by_path: dict, staged: dict) -> StagedGate:
    """The rows the gate judges for the files `ranges_by_path` names. A name
    that is not UTF-8 a scope takes comes first and alone, as the scan's
    refusal did: nothing else is read. Only files in `staged` can be named as
    unscoped: the note is about a staged hole."""
    if not ranges_by_path:
        return StagedGate()
    paths = sorted(ranges_by_path)
    claimed = claimed_unreadable(paths, cfg)
    if claimed:
        return StagedGate(_claimed_rows(claimed, ranges_by_path))
    universe = scan_files(paths, cfg)
    scope_of = _scope_of(universe.by_scope)
    unscoped = _unscoped_sources(sorted(staged), set(scope_of), cfg)
    if not scope_of:
        return StagedGate((), unscoped, unreadable=universe.unreadable)
    blobs = reads.staged_blobs(list(scope_of))
    records_by_path = staged_records(blobs, worker_budget=cfg.analysis_worker_budget)
    # The staged blob is the diff's new side: its bytes place git's lines.
    spans = reader_ranges(ranges_by_path, blobs.get)
    unread = unread_reasons(records_by_path)
    changes = tuple(_changed_file(rel, records_by_path[rel], spans[rel], scope, cfg, unread)
                    for rel, scope in scope_of.items())
    return StagedGate(changes, unscoped, tuple(chain.from_iterable(records_by_path.values())),
                      universe.unreadable)
