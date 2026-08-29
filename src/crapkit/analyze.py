"""Analysis shell: run lizard once over explicit file lists and read both ccn columns off it.

Lizard is always fed explicit files, never directories: its own walker descends
nested node_modules (measured hang on the first consumer repo).
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ._pygdefer import deferred_pygments

with deferred_pygments():  # lizard's Erlang reader would load pygments here
    import lizard

    from .lizardpowershell import register as _register_powershell
    from .lizardrust import register as _register_rust
    from .lizardshell import register as _register_shell

from .cache import partition_by_cache, updated_cache
from .errors import ToolError
from .lizardcognitive import LizardExtension as _Cognitive
from .merge import FunctionRecord
from .packet import bare_name

# lizard picks a reader by extension off a hardcoded list, and none of these is
# on it: `.rs` resolves to a reader that counts no `match` arm (lizard #494),
# and `.sh` and `.ps1` resolve to nothing at all, which lizard answers with
# CLikeReader rather than a failure. All three belong HERE, at the module scope
# of the module a ProcessPoolExecutor child imports, or spawned workers measure
# with the readers lizard shipped and report plausible wrong numbers.
#
# lizardshell and lizardpowershell already register themselves on import and
# lizardrust deliberately does not (rebinding a name in another package's
# namespace is not something an import should do quietly). Calling all three
# keeps the wiring readable in one place and costs nothing: each is idempotent.
_register_rust()
_register_shell()
_register_powershell()

_POOL_THRESHOLD = 16

# Bump whenever analysis semantics change (merge rules, extension set, record
# extraction): the fingerprint must invalidate cached records produced by older
# logic even when file content and tool versions are identical.
ANALYSIS_VERSION = 7  # 7: a Rust `match` is a cognitive condition (+1 and the
#                          nesting it sits in), so every cached .rs record
#                          carries a cognitive score measured without it. Rust
#                          is the only language whose stored values move; ccn is
#                          untouched in every language, Rust included.
#                       6: five more C-family extension sets and .ps1/.psm1 are
#                          admitted, a C++ rvalue reference is no longer a
#                          cognitive condition, and source bytes decode utf-8
#                          then cp1252 instead of by machine locale

# The three tokens lizard's modified rule reacts to. Membership is checked before
# anything else runs, so the common token pays one frozenset lookup.
_SWITCH_TOKENS = frozenset({"switch", "match", "case"})


def _switch_delta(token: str, reader) -> int:
    """+1 for a switch-like block opener, -1 for one of its arms.

    `match` and `case` are soft keywords in Python: the same spelling is an
    identifier elsewhere, so the reader's own flags decide, exactly as lizard's
    modified extension decides.
    """
    if token == "case":
        return -int("case" in reader.conditions or getattr(reader, "_keyword_case", False))
    if token == "switch":
        return 1
    return int(getattr(reader, "_keyword_match", False))


class _ModifiedDelta:
    """ccn_mod minus ccn_std, counted inside the standard pass.

    lizard's modified extension does one thing: add_condition(+1) on a
    switch/match opener and add_condition(-1) on each arm. It filters no tokens
    and reads nothing else, so the two columns differ by that sum and by nothing
    else, which made the second full tokenization of every file pure waste.
    """

    FUNCTION_INFO = {"modified_delta": {"caption": " Mod "}}

    def __call__(self, tokens, reader):
        context = reader.context
        for token in tokens:
            if token in _SWITCH_TOKENS:
                fn = context.current_function
                delta = _switch_delta(token, reader)
                fn.modified_delta = getattr(fn, "modified_delta", 0) + delta
            yield token


def _chain(cognitive_index: int) -> list:
    """lizard's standard extensions with cognitive spliced in at one index.

    Index 0 puts cognitive ahead of lizard's own `preprocessing`, which is where
    it has to sit for Python: `preprocessing` strips the whitespace tokens the
    python indent rules read, and behind it a 6-branch function scores 6 instead
    of 10. The delta comes last either way, where the modified pass used to sit.
    """
    extensions = lizard.get_extensions(["ND"])
    extensions.insert(cognitive_index, _Cognitive())
    return extensions + [_ModifiedDelta()]


# Two chains, built once per process each, not once per file: 14k files paid 14k
# chain builds. Every extension keeps its state in the generator frame __call__
# opens, so one chain serves every file of its kind.
_EXTENSIONS = _chain(0)
_PREPROCESSED_EXTENSIONS = _chain(1)

# lizard's SwiftReplaceLabel.preprocess RETURNS a list where the other seven
# preprocessors YIELD: it runs list() over its input, so every extension AHEAD of
# `preprocessing` is drained to exhaustion before lizard has split the file into
# functions. An extension at index 0 counts the whole file against one
# placeholder FunctionInfo, and every real function comes out at 0. SwiftReader
# and KotlinReader are the only two of lizard's 27 readers that inherit that
# preprocessor, so only these suffixes take the second chain.
_DRAINED_READER_SUFFIXES = (".swift", ".kt", ".kts")


def _extensions_for(rel_path: str) -> list:
    if rel_path.lower().endswith(_DRAINED_READER_SUFFIXES):
        return _PREPROCESSED_EXTENSIONS
    return _EXTENSIONS


def _record(rel_path: str, fn) -> FunctionRecord:
    std = fn.cyclomatic_complexity
    mod = std + (getattr(fn, "modified_delta", 0) or 0)
    return FunctionRecord(
        path=rel_path,
        long_name=fn.long_name,
        start=fn.start_line,
        end=fn.end_line,
        ccn_std=std,
        ccn_mod=mod,
        ccn=min(std, mod),
        nloc=fn.nloc,
        params=len(fn.parameters),
        nesting=getattr(fn, "max_nesting_depth", 0) or 0,
        cognitive=getattr(fn, "cognitive_complexity", 0) or 0,
    )


# How many colliding names one warning prints before it stops. A generated file
# can hold hundreds; the point is that the file needs looking at, not the list.
_NAMES_SHOWN = 5


def _colliding_names(records: list[FunctionRecord]) -> list[str]:
    """Names this file gives to more than one function, in first-seen order.

    Anonymous functions are exempt. lizard calls every one of them
    `(anonymous)`, so a file with two arrow callbacks collides by construction
    and a warning would name nothing anyone could act on; `packet.handles`
    answers that collision with the `(anonymous)#N` ordinal instead.
    """
    seen: set[str] = set()
    colliding: dict[str, None] = {}
    for record in records:
        if bare_name(record.long_name) and record.long_name in seen:
            colliding[record.long_name] = None
        seen.add(record.long_name)
    return list(colliding)


def _warn_on_collisions(rel_path: str, records: list[FunctionRecord]) -> None:
    """One stderr line for a file whose records cannot all reach the ratchet.

    A mark is keyed on (path, long_name), and so is the row a run writes, so the
    last function under a colliding name is the only one marked and the only one
    gated. C makes this ordinary: both arms of an `#ifdef` fork are textually
    present, so a platform shim defines the same function twice in one file.
    Python makes it ordinary too, because a method's long_name carries no class.
    Neither is fixable here — the ratchet cannot key on a span, which drifts with
    every edit — so the loss is announced rather than silent.
    """
    names = _colliding_names(records)
    if not names:
        return
    print(f"crapkit: {rel_path} defines {_listed(names)} more than once; the ratchet keys "
          f"on (path, long_name) and keeps the last, so the earlier ones are neither "
          f"marked nor gated", file=sys.stderr)


def _listed(names: list[str]) -> str:
    if len(names) <= _NAMES_SHOWN:
        return ", ".join(names)
    return f"{', '.join(names[:_NAMES_SHOWN])} and {len(names) - _NAMES_SHOWN} more name(s)"


def _file_records(rel_path: str, functions) -> list[FunctionRecord]:
    records = [_record(rel_path, fn) for fn in functions]
    _warn_on_collisions(rel_path, records)
    return records


# --- how a source file's bytes become text -------------------------------------
#
# lizard opens a source file with `io.open(path, 'r')` and no encoding, so the
# MACHINE'S LOCALE decides what a repo scores. Measured on one function,
# `function Write-Café`, written once as UTF-8 and once as cp1252, read on a
# cp1252 interpreter and on a UTF-8 one:
#
#     source     cp1252 reader        utf-8 reader
#     utf-8      no function at all   Write-Café
#     cp1252     Write-Café           Write-Caf
#
# The empty cell is not a rounding error: `é` arrives as `Ã` plus `©`, the `©`
# is no word character, and the declaration stops being one. The ratchet keys on
# path::long_name, so those are three different rows for one commit, and a
# Windows developer and a Linux CI cannot see each other's baseline.
#
# utf-8 first, cp1252 second, replacement for the five bytes cp1252 leaves
# undefined. That is the whole rule and it is fixed rather than environmental:
# all four cells above read `Write-Café`. The fallback is cp1252 rather than
# latin-1 because Windows PowerShell 5.1 writes cp1252, and because latin-1
# decodes every byte and so can never say it was wrong.
#
# UTF-16 is NOT handled. `Out-File` and the ISE write it, and such a file
# decodes here as NUL-separated cp1252 text that reports no function; it
# reported none before this change either, so nothing regressed and the narrow
# rule stays narrow.


def _characters(raw: bytes) -> str:
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8):]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def decode_source(raw: bytes) -> str:
    """Source bytes as text, decoded by content rather than by machine locale.

    The single rule for every path into lizard: a file on disk, and a staged
    blob the pre-commit gate never writes down. Those two must agree or the
    gate judges different content than the inventory scores.

    Line endings are normalized the way `io.open(path, 'r')` normalized them,
    because that is what lizard did and every recorded line number and NLOC in
    every cache depends on it: a lone `\\r` left in the stream is one more
    whitespace token, not one more line.
    """
    return _characters(raw).replace("\r\n", "\n").replace("\r", "\n")


def read_source(path: str) -> str:
    return decode_source(Path(path).read_bytes())


def _install_decoder() -> None:
    """Point lizard's own file read at `read_source`. Idempotent.

    A rebind rather than a replacement for `FileAnalyzer.__call__`, so lizard
    keeps the IOError branch that answers a file deleted mid-run with an empty
    result instead of a traceback, and keeps reading each file exactly once.
    `lizard.py` binds `auto_read` into its own module namespace at import, and
    `FileAnalyzer.__call__` resolves it there on every call.

    Raises when that name is gone, which is what a lizard release that reads
    source some other way would look like. Loud beats an attribute nobody
    reads and a decode that silently went back to the locale's.
    """
    if not hasattr(lizard, "auto_read"):
        raise RuntimeError(
            f"crapkit.analyze._install_decoder() found no lizard.auto_read to "
            f"rebind: lizard {lizard.version} reads source some other way. "
            f"Rewrite this against the new mechanism; leaving it undone makes "
            f"every non-ASCII file score by the machine's locale.")
    lizard.auto_read = read_source


_install_decoder()


def analyze_one(args: tuple[str, str]) -> tuple[str, list[FunctionRecord]]:
    abs_path, rel_path = args
    try:
        analysis = lizard.FileAnalyzer(_extensions_for(rel_path))(abs_path)
        return rel_path, _file_records(rel_path, analysis.function_list)
    except Exception as exc:  # loud, with the file named
        raise ToolError(f"lizard failed on {rel_path}: {exc}") from exc


def analyze_source(rel_path: str, code: str) -> list[FunctionRecord]:
    """The records analyze_one would produce for a file holding `code`.

    analyze_source_code is what FileAnalyzer.__call__ runs once it has read the
    file, so nothing about the analysis depends on whether the source arrived
    from the disk or from a git blob the caller already holds; rel_path picks
    the language, and with it the extension chain, exactly as the path on disk did.
    """
    try:
        analyzer = lizard.FileAnalyzer(_extensions_for(rel_path))
        analysis = analyzer.analyze_source_code(rel_path, code)
        return _file_records(rel_path, analysis.function_list)
    except Exception as exc:  # loud, with the file named
        raise ToolError(f"lizard failed on {rel_path}: {exc}") from exc


def content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint() -> str:
    from . import __version__
    return f"crapkit={__version__};analysis={ANALYSIS_VERSION};lizard={lizard.version}"


def _drained_records(entries: dict) -> dict[str, list[FunctionRecord]]:
    """Turn parsed rows into records, freeing each row list as it is consumed.

    A comprehension over `entries` would hold the whole parsed list-of-lists
    alongside the records built from it; on a large corpus that doubling is tens
    of MB for no reason. Draining leaves the caller's parsed dict empty, which is
    fine: nothing reads it afterwards.
    """
    records: dict[str, list[FunctionRecord]] = {}
    while entries:
        h, rows = entries.popitem()
        records[h] = [FunctionRecord(*vals) for vals in rows]
    return records


def load_cache(path: Path) -> dict:
    """A cache is disposable: corrupt or truncated content reads as cold, never as a crash.

    save_cache is not atomic, so a killed process can leave a torn file; the
    cache keys on raw bytes, so cross-machine line-ending settings just miss.
    """
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return {"fp": raw.get("fp"), "entries": _drained_records(raw.get("entries", {}))}
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return {}


def save_cache(path: Path, cache: dict, *, prior: dict | None = None) -> None:
    """Rewrite the cache file, unless `prior` says the bytes would not move.

    Callers hand back what load_cache gave them. A fully-warm run rebuilds an
    entry map equal to the one already on disk, and serializing it is the most
    expensive thing such a run does; equal maps serialize identically because
    the dump sorts its keys.
    """
    if cache == prior:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        _write_entries(fh, cache["entries"])
        fh.write(f", {json.dumps('fp')}: {json.dumps(cache['fp'])}}}")


def _write_entries(fh, entries: dict) -> None:
    """The `entries` object, one entry serialized at a time.

    Byte-for-byte what json.dumps(document, sort_keys=True) wrote: keys sorted,
    the two-character separators json uses by default, and `entries` ahead of
    `fp` because sorted keys put it there. Building the document first meant the
    rebuilt lists, the whole 18.6 MB string and its encoding were all live at
    once; this way one entry is.
    """
    fh.write('{"entries": {')
    lead = ""
    for h in sorted(entries):
        fh.write(f"{lead}{json.dumps(h)}: {json.dumps([list(r) for r in entries[h]])}")
        lead = ", "
    fh.write("}")


_STAMPS_NAME = "stat-stamps.json"

# A stamp is only recorded once the file has held still this long. Windows moves
# a file time on a ~15 ms clock tick, so two writes inside one tick can land on
# one mtime; a file written moments ago is not evidence that it is unchanged.
_STAMP_SETTLE_NS = 2_000_000_000


def _stamps_path(root: Path) -> Path:
    return root / ".crapkit" / _STAMPS_NAME


def _load_stamps(path: Path) -> dict:
    """rel path -> [mtime_ns, size, content hash], or nothing at all.

    An index of what the last run saw. Disposable exactly like the analysis
    cache: unreadable, torn, or written by another format reads as no index,
    which costs a full hashing pass and never a wrong answer.
    """
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return raw["stamps"] if raw.get("v") == 1 else {}
    except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError, AttributeError):
        return {}


def _save_stamps(path: Path, stamps: dict, prior: dict) -> None:
    if stamps == prior:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": 1, "stamps": stamps}, sort_keys=True), encoding="utf-8")
    except OSError:
        pass  # an index nobody can write is a slower run, never a failed one


def _stat_of(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _unmoved(stat: tuple[int, int] | None, prior) -> bool:
    """True when this file's stat is exactly what it was when we hashed it."""
    return bool(prior) and stat is not None and stat[0] == prior[0] and stat[1] == prior[1]


def _stamp(fresh: dict, rel: str, stat: tuple[int, int] | None, digest: str, now: int) -> None:
    if stat is not None and now - stat[0] >= _STAMP_SETTLE_NS:
        fresh[rel] = [stat[0], stat[1], digest]


def _hash_paths(root: Path, rel_paths: list[str], stamps: dict) -> tuple[dict[str, str], dict]:
    """Content hash per path, plus the stat index the next run should keep.

    The cache keys are content hashes and stay content hashes; (mtime_ns, size)
    only decides whether a hash has to be recomputed. A file that has not moved
    since the run that hashed it keeps that hash without being opened, which is
    the difference between reading 14k files and stat-ing them.

    Its one blind spot: content rewritten to the same length under a deliberately
    restored mtime. Any real write moves the mtime, and a write too close to the
    last one is refused a stamp, so the next genuine change corrects it.
    """
    now = time.time_ns()
    hashes: dict[str, str] = {}
    fresh: dict = {}
    for rel in rel_paths:
        stat = _stat_of(root / rel)
        prior = stamps.get(rel)
        hashes[rel] = prior[2] if _unmoved(stat, prior) else content_hash(root / rel)
        _stamp(fresh, rel, stat, hashes[rel], now)
    return hashes, fresh


def _kept_stamps(prior: dict, fresh: dict, visited: set) -> dict:
    """Paths this run looked at are described by this run alone: a file too
    recently written to stamp must not keep an older run's stamp. Paths it never
    looked at keep theirs, or a rescore of four files would blind the next
    inventory of fourteen thousand."""
    kept = {rel: stamp for rel, stamp in prior.items() if rel not in visited}
    kept.update(fresh)
    return kept


def _restamped(hits: dict[str, list[FunctionRecord]]) -> dict[str, list[FunctionRecord]]:
    # A cache entry keys on content only; re-stamp the path so a moved file cannot
    # carry its old location into the snapshot. Almost nothing moves between two
    # runs, and rebuilding 140k namedtuples to write back the path they already
    # hold is the most expensive thing a fully-warm run does.
    return {path: _rows_for(path, rows) for path, rows in hits.items()}


def _rows_for(path: str, rows: list[FunctionRecord]) -> list[FunctionRecord]:
    """One entry's rows all carry one path, because analyze_one stamps every
    record it emits with the single path it was handed. The first row answers
    for all of them."""
    if rows and rows[0].path == path:
        return rows
    return [r._replace(path=path) for r in rows]


_MEMORY_BUDGET_ENV = "CRAPKIT_ANALYSIS_MEMORY_MB"

# Measured peak RSS of one analysis worker over the consumer repo: 24 workers reached
# 807 MB of tree RSS, the biggest child 47 MB, the median child 35 MB.
_WORKER_PEAK_MB = 35


def _memory_budget_mb() -> int | None:
    """The budget in MB, or None when it is unset or not a positive whole number.

    A mistyped knob must not silently serialize a run: unreadable reads as absent.
    """
    raw = os.environ.get(_MEMORY_BUDGET_ENV, "").strip()
    if not raw.isdigit() or raw == "0":
        return None
    return int(raw)


def _memory_bounded(workers: int | None) -> int | None:
    """The worker count, capped by CRAPKIT_ANALYSIS_MEMORY_MB when it is set.

    Off by default: no budget means one worker per core, as before. Cold over
    the consumer repo, 24 workers peak at 807 MB in 11.8 s and 16 at 565 MB in 14.2 s, so
    a box short of memory can buy 242 MB back for 2.4 s. Never returns zero
    workers: a budget smaller than one worker still gets one.
    """
    budget = _memory_budget_mb()
    if budget is None:
        return workers
    return min(workers or os.cpu_count() or 1, max(1, budget // _WORKER_PEAK_MB))


def analyze_jobs(
    jobs: list[tuple[str, str]],
    *,
    workers: int | None = None,
    pool_threshold: int = _POOL_THRESHOLD,
    chunksize: int = 32,
) -> dict[str, list[FunctionRecord]]:
    """Run lizard over (abs_path, rel_path) jobs, pooled once there are enough.

    The two knobs exist because the inventory and the pre-commit hook sit at
    different scales: an inventory feeds thousands of files and wants fat
    chunks, a hook feeds a commit's worth and needs each job dealt to a
    different worker (a chunksize above the job count leaves one worker doing
    all of them, serially, after paying for the pool).
    """
    fresh: dict[str, list[FunctionRecord]] = {}
    if len(jobs) >= pool_threshold:
        with ProcessPoolExecutor(max_workers=_memory_bounded(workers)) as pool:
            for rel_path, records in pool.map(analyze_one, jobs, chunksize=chunksize):
                fresh[rel_path] = records
        return fresh
    for job in jobs:
        rel_path, records = analyze_one(job)
        fresh[rel_path] = records
    return fresh


def analyze_files(
    root: Path, rel_paths: list[str], *, cache: dict, workers: int | None = None
) -> tuple[dict[str, list[FunctionRecord]], int, dict]:
    fp = fingerprint()
    stamps_path = _stamps_path(root)
    prior_stamps = _load_stamps(stamps_path)
    hashes, fresh_stamps = _hash_paths(root, rel_paths, prior_stamps)
    kept = _kept_stamps(prior_stamps, fresh_stamps, set(rel_paths))
    _save_stamps(stamps_path, kept, prior_stamps)
    hits, misses = partition_by_cache(hashes, cache, fingerprint=fp)
    hits = _restamped(hits)

    fresh = analyze_jobs([(str(root / rel), rel) for rel in misses], workers=workers)

    all_records = {**hits, **fresh}
    new_cache = updated_cache(hashes, all_records, fingerprint=fp)
    return all_records, len(hits), new_cache
