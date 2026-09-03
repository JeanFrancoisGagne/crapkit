"""Pre-commit gate: min-CCN over the target on functions touched by the staged diff.

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

import tempfile
from pathlib import Path
from typing import NamedTuple

from .analyze import analyze_jobs, analyze_source, decode_source
from .config import Config
from .diffparse import changed_ranges
from .gitio import GitReads
from .keys import key_names, key_of
from .merge import FunctionRecord
from .universe import _source_extensions, assign_files, exclude_matcher, excluded

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


class StagedGate(NamedTuple):
    """The verdict on the staged blobs."""
    violations: list[Violation]
    unscoped: list[str] = []  # staged source files no scope claims: ungated, but never silently


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


def staged_records(blobs: dict[str, bytes]) -> dict[str, list]:
    """Records for the staged blobs, pooled once a commit touches enough files.

    Below the pool threshold lizard is handed the blob text directly: the bytes
    are already in memory from `git cat-file --batch`, and a temp tree only to
    read them back costs a write and a read per file. The pooled arm still
    materializes, because a worker process reads its own files.
    """
    if len(blobs) < _HOOK_POOL_THRESHOLD:
        return {rel: analyze_source(rel, decode_source(blob))
                for rel, blob in sorted(blobs.items())}
    with tempfile.TemporaryDirectory() as tmp:
        jobs = _materialized(Path(tmp), blobs)
        return analyze_jobs(jobs, workers=min(len(jobs), _HOOK_MAX_WORKERS),
                            pool_threshold=_HOOK_POOL_THRESHOLD, chunksize=1)


def file_ceilings(cfg, in_scope, checked_files) -> dict[str, int]:
    """The ccn ceiling each file is judged against: its scope's, read through
    `Config.ceiling_of`. `rescore --gate` decides on this same map, so a
    mid-session verdict and the commit's cannot disagree."""
    scope_of = {f: scope for scope, files in in_scope.items() for f in files}
    return {rel: cfg.ceiling_of(scope_of.get(rel, "")) for rel in checked_files}


def _file_violations(rel: str, records: list, ranges, ceiling: int) -> list[Violation]:
    """One file's breaches, each carrying the ratchet key it will be judged under.

    Keyed here because this is where the file's whole record list is in hand:
    the ordinal counts same-named functions in file order, and the breaching
    subset alone cannot say which twin a record is.
    """
    keys = key_names(records)
    return [Violation(rel, rec.long_name, rec.start, rec.ccn, key_of(keys, rec)[1])
            for rec in records if rec.ccn > ceiling and _touches(rec, ranges)]


def _touched_over_ceiling(records_by_path, ranges_by_path, checked_files, cfg, in_scope) -> list[Violation]:
    by_file = file_ceilings(cfg, in_scope, checked_files)
    violations = []
    for rel in checked_files:
        violations.extend(_file_violations(rel, records_by_path[rel], ranges_by_path[rel],
                                           by_file[rel]))
    violations.sort(key=lambda v: (-v.ccn, v.path, v.start))
    return violations


def _gate_blind_to(path: str, checked: set[str], exts: tuple, match) -> bool:
    """A staged file the gate cannot judge but a scope language claims by
    extension. Files the config EXCLUDES (test trees, exclude globs) are
    outside scopes on purpose and never a hole."""
    return path not in checked and path.endswith(exts) and not excluded(path, match)


def _unscoped_sources(staged: list[str], checked: set[str], cfg: Config) -> list[str]:
    """The first new top-level directory a repo grows commits ungated; this is
    how that hole stays visible instead of silent."""
    exts = tuple(e for scope in cfg.scopes for e in _source_extensions(scope.languages))
    match = exclude_matcher(cfg.exclude_globs)
    return sorted(f for f in staged if _gate_blind_to(f, checked, exts, match))


def gate_staged(root: Path, cfg: Config, reads=None) -> StagedGate:
    """`reads` is where the staged bytes come from: git processes the caller
    already started, or a spawn-on-demand pair when nobody did. The verdict is
    the same either way."""
    reads = reads or GitReads(root)
    ranges_by_path = changed_ranges(reads.staged_diff())
    if not ranges_by_path:
        return StagedGate([])
    in_scope = assign_files(sorted(ranges_by_path), cfg)
    checked_files = sorted({f for files in in_scope.values() for f in files})
    unscoped = _unscoped_sources(sorted(ranges_by_path), set(checked_files), cfg)
    if not checked_files:
        return StagedGate([], unscoped)
    records_by_path = staged_records(reads.staged_blobs(checked_files))
    return StagedGate(
        _touched_over_ceiling(records_by_path, ranges_by_path, checked_files, cfg, in_scope),
        unscoped
    )
