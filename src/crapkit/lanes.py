"""Lane runner shell: execute a configured coverage command, parse its artifact.

A lane command's exit code is recorded, not enforced: a suite with known
failures still writes a valid coverage artifact, and demanding green here
would make coverage unmeasurable on a brownfield repo. The artifact existing
and parsing is the contract.

Output STREAMS to .crapkit/lane-<name>.log while the command runs, so a long
lane is supervisable by tailing the file instead of staring at a silent
process. Timeouts and retries are lane config (timeout_seconds, retries).
"""
from __future__ import annotations

import hashlib
import os
import posixpath
import re
import socket
import sys
import time
import warnings
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import IO, NamedTuple

from .config import Lane
from .coverage_istanbul import FnCoverage
from .coverage_format import lane_format
from .errors import GitError, ToolError
from .gitio import GitFacts, untracked_files
from .gitpaths import readable, shown
from . import lane_results
from .lane_command import launch_spec, pytest_python
from .lane_freshness import (Freshness, Proof, ReuseVerdict, measurement_proof,  # noqa: F401
                             uncommitted_changes, unproved)
from .lane_outputs import declared_files, declared_outputs, owned, owners, put_back, retest_owner
from .lane_sources import lane_matchers, lane_record, settled
from .lane_stamps import (STAMPS_FILE, Stamps, file_sha256, read, read_stamps,  # noqa: F401
                          recorded_seconds, refusal_entry, stamp_for, unreadable_stamps,
                          write as write_stamps)
from .named import first_few
from .plaintext import strip_escapes
from .procs import CwdMissing, NoProgress, own_processes, run_bounded
from .repopath import Placing
from .repotext import lenient, os_bytes
from .universe import ScopeMatch, owning_scope


def _in_container() -> bool:
    return os.environ.get("CRAPKIT_INSIDE_CONTAINER") == "1" or Path("/.dockerenv").exists()


def _refuse_container_python(lane: Lane) -> None:
    if lane.parser == "coveragepy" and _in_container() and not lane.container_ok:
        raise ToolError(
            f"lane {lane.name!r} runs the python suite, which is host-only "
            f"(container runs OOM); set container_ok = true only if this environment truly differs")


def _lane_log_path(root: Path, lane: Lane) -> Path:
    log_path = root / ".crapkit" / f"lane-{lane.name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


_TAIL_BUDGET = 500
_CAUSE_LINES = 3
_CAUSE_WIDTH = 200

# Lines that name a cause rather than restate a count: pytest's `E   ` gutter, a
# traceback header, a bare exception line. A run that died in collection ends
# with a summary block of `ERROR path` lines saying WHICH files broke and never
# why, so a plain tail spends its whole budget on filenames while the reason
# scrolls off above it.
_DIAGNOSTIC = re.compile(r"\s*(E\s|Traceback \(most recent call last\)|\w*(Error|Exception): )")

# The banner `_log_header` writes before every attempt after the first. A whole
# line, so a log line that quotes those words mid-text is output and not a
# boundary.
_ATTEMPT_BANNER = re.compile(r"--- attempt \d+ ---")


def _log_lines(log_path: Path) -> list[str]:
    """The log as its readers quote it: escape codes removed. The lane child
    inherits FORCE_COLOR and PY_COLORS, and pytest then puts a colour code in
    front of `E   `, which hid the cause from `_DIAGNOSTIC` and carried raw
    escape bytes into the refusal. The file itself keeps its colour."""
    if not log_path.is_file():
        return []
    return strip_escapes(lenient(log_path.read_bytes())).strip().splitlines()


def _tail_lines(lines: list[str], budget: int) -> list[str]:
    """The last WHOLE lines that fit the budget. A tail cut on a byte count
    starts mid-line, and the reader cannot tell that fragment from the real
    start of the message: the report that prompted this opened on
    "short test summary info ====" and read as the first thing the run said.
    One line over budget on its own keeps its end behind an ellipsis, which at
    least says so."""
    kept: list[str] = []
    left = budget
    for line in reversed(lines):
        left -= len(line) + 1
        if left < 0:
            break
        kept.append(line)
    if kept:
        kept.reverse()
        return kept
    return ["..." + lines[-1][-budget:]] if lines else []


def _cut_cause(line: str) -> str:
    """One cause line inside the budget, cut from the LEFT and marked.

    What identifies an ImportError is the path at the end of it — the OTHER
    checkout's, in the report this came from — so a cut taken from the right
    drops the half worth hoisting. `_tail_lines` marks its own unavoidable cut
    for the same reason: an unmarked one reads as the whole message."""
    return line if len(line) <= _CAUSE_WIDTH else "..." + line[-(_CAUSE_WIDTH - 3):]


def _last_attempt(lines: list[str]) -> list[str]:
    """The final attempt's lines. A retried lane appends every attempt to one
    log, so a scan of the whole file can hoist the reason a superseded attempt
    died for and stand it in front of the last attempt's own output, with
    nothing marking the boundary. Attempt 1 writes no banner, so a log holding
    none is one attempt and comes back whole."""
    banners = [i for i, line in enumerate(lines) if _ATTEMPT_BANNER.fullmatch(line)]
    return lines[banners[-1] + 1:] if banners else lines


def _cause_lines(lines: list[str], tail: list[str]) -> list[str]:
    """The last lines of the final attempt that name a failure, when the tail
    carries none. Nothing when the tail already says why — repeating it would
    spend the message on the same words twice."""
    if any(_DIAGNOSTIC.match(line) for line in tail):
        return []
    named = [_cut_cause(line.strip()) for line in lines if _DIAGNOSTIC.match(line)]
    return named[-_CAUSE_LINES:]


def _log_tail(log_path: Path) -> str:
    """What the log says about its own failure: the reason lines first when the
    end of the log does not carry one, then the last whole lines of output. The
    ellipsis between them marks the output they skipped over."""
    lines = _log_lines(log_path)
    tail = _tail_lines(lines, _TAIL_BUDGET)
    cause = _cause_lines(_last_attempt(lines), tail)
    return "\n".join([*cause, "...", *tail] if cause else tail)


def _deadline(lane: Lane) -> float | None:
    """The lane's own timeout, or None for no crapkit-owned deadline at all.
    0 is the config default and means the suite decides when it is done."""
    return lane.timeout_seconds or None


def _no_progress(lane: Lane) -> float | None:
    """The lane's idle deadline, or None for no progress watch.

    The second half of the same guard: a total deadline has to be longer than
    the slowest honest run, so it cannot cut a suite that hangs early without
    also cutting the slow ones. This one measures the log instead, and 0, the
    default, leaves the total deadline as the only one.
    """
    return lane.no_progress_seconds or None


def _log_header(fh: IO[str], command: str, attempt: int) -> None:
    if attempt > 1:
        fh.write(f"\n--- attempt {attempt} ---\n")
    fh.write(f"$ {command}\n")
    fh.flush()


def _raise_stalled(fh: IO[str], lane: Lane, log_path: Path, attempt: int,
                   seconds: float) -> None:
    """A lane killed for silence, not for running long. The message says which:
    the command was still alive and had written nothing since the header.

    The seconds come from the killer, not from the config it read: one number,
    measured once, so the log and the error cannot drift from what was waited.
    """
    fh.write(f"\n[crapkit] no output for {seconds:g}s; killed\n")
    raise ToolError(f"lane {lane.name!r} wrote no output for {seconds:g}s "
                    f"(attempt {attempt}), so crapkit killed it; log: {log_path}")


def _stream_command(root: Path, lane: Lane, log_path: Path, attempt: int, owner=None) -> int:
    from .logs import command_log

    with command_log(log_path, max_bytes=lane.log_max_bytes, append=attempt > 1) as fh:
        _log_header(fh, lane.command, attempt)
        # Ownership stops descendants before the log drains. Its byte counter
        # measures progress even when the bounded files rotate.
        try:
            code = run_bounded(lane.command, _deadline(lane), stream=fh,
                               no_progress=_no_progress(lane), owner=owner,
                               **launch_spec(root, lane).popen_kwargs())
        except NoProgress as stalled:
            _raise_stalled(fh, lane, log_path, attempt, stalled.seconds)
        except CwdMissing as missing:
            raise _told_the_cwd_fix(fh, lane, missing) from missing
        except ToolError as failed:  # a failed start: the log says why, as it does for a kill
            fh.write(f"\n[crapkit] {failed}\n")
            raise
        if code is None:
            fh.write(f"\n[crapkit] timed out after {lane.timeout_seconds}s; killed\n")
            raise ToolError(f"lane {lane.name!r} timed out after {lane.timeout_seconds}s "
                            f"(attempt {attempt}); log: {log_path}")
        fh.write(f"\n(exit {code})\n")
    return code


def _told_the_cwd_fix(fh, lane: Lane, missing: CwdMissing) -> ToolError:
    """A lane whose cwd names no directory, with the fix: the lane's `cwd` in
    crapkit.toml, as the loader read it, or the directory itself."""
    told = ToolError(f"{missing}; fix cwd = {lane.cwd!r} for this lane in crapkit.toml, "
                     "or create that directory")
    fh.write(f"\n[crapkit] {told}\n")
    return told


def _attempt_once(root: Path, lane: Lane, log_path: Path, attempt: int, owner=None) -> int | None:
    """One attempt; None means it timed out but another attempt remains."""
    try:
        return _stream_command(root, lane, log_path, attempt, owner)
    except ToolError:
        if attempt > lane.retries:
            raise
        return None


def _pytest_cov_home(lane: Lane) -> str:
    """Which environment the package has to land in, as concretely as the lane
    command allows.

    A repo whose lane runs its own venv got `pip install pytest-cov`, which
    lands in whatever venv the reader's shell has active; the reporter ran it
    verbatim, it installed fine, and the next `crapkit coverage` failed
    identically. So the hint binds the install to the interpreter the lane
    names. When the lane names no interpreter, it says which environment and
    stops there: an install line built around a word that has no `-m` flag costs
    the reader a second, unrelated failure before they are back where they were.
    """
    word = pytest_python(lane.command)
    if not word:
        return "the environment the lane's suite runs in"
    return f"the environment `{word}` runs in (`{word} -m pip install pytest-cov`)"


def _missing_plugin_hint(tail: str, lane: Lane) -> str:
    """The one failure signature a new user cannot decode: pytest rejecting
    --cov points at crapkit's config when the real gap is the pytest-cov package.

    Which environment it is missing from is the other half of the hint, and it
    used to name none.
    """
    if "unrecognized arguments" not in tail or "--cov" not in tail:
        return ""
    return (f" - the --cov flags come from the pytest-cov package, which has to be "
            f"installed in {_pytest_cov_home(lane)}, not in the shell's active venv")


def _shard_hint(root: Path, lane: Lane) -> str:
    """What the `.coverage.*` files beside a failed lane are, and what to do
    with them.

    coverage.py in parallel mode writes one shard per process and combines them
    only when the run ends, so a suite that was killed leaves every measurement
    it took on disk and no JSON. One reporter combined them by hand and got a
    usable artifact; crapkit reported the missing JSON and never mentioned the
    shards, which sit a directory above the path the message names.

    A hint, never the combine itself. Shards from an interrupted suite merge
    into a report that looks exactly like a whole run, which is the illusion the
    crashed-worker check exists to refuse; whether this half-run is worth
    scoring is the operator's call, and `--reuse-artifacts` is where they say so.

    `coverage combine` is coverage.py's command, so only a coveragepy lane gets
    the recipe: a JS lane sharing the root with a python one finds the python
    lane's shards and would be handed advice that cannot work for it.

    The `-o` target is printed relative to the shard directory, because that is
    where the operator is told to stand. `artifact` is repo-relative, so a lane
    with a `cwd` that pasted the key verbatim wrote the JSON one directory below
    the path crapkit reads, and the next run refused it again.
    """
    if lane.parser != "coveragepy":
        return ""
    shard_dir = launch_spec(root, lane).cwd
    shards = sorted(shard_dir.glob(".coverage.*"))
    if not shards:
        return ""
    unreadable = _unreadable_shard_name(shard_dir, shards[0])
    if unreadable:
        return unreadable
    target = Path(os.path.relpath(root / lane.artifact, shard_dir)).as_posix()
    noun, verb = ("shard", "sits") if len(shards) == 1 else ("shards", "sit")
    return (f"; {len(shards)} coverage {noun} ({shards[0].name}, ...) {verb} in "
            f"{shard_dir}, which is what a killed parallel run leaves behind: "
            f"`coverage combine && coverage json -o {target}` there, then a "
            "re-run with --reuse-artifacts, scores what that suite did measure")


def _unreadable_shard_name(shard_dir: Path, shard: Path) -> str:
    """Why coverage.py left shards and no report, when a name it stores holds
    bytes that are not UTF-8: the directory it measured under, or the host name
    it puts in each shard's name. coverage.py keeps every measured path as UTF-8
    text, so its combine fails there, and `coverage combine` by hand fails the
    same way; the rename is the fix, not the killed-run recipe."""
    if not readable(str(shard_dir)):
        named = f"the directory {shown(str(shard_dir))}"
    elif not readable(shard.name):
        named = f"this host's name, which coverage.py puts in each shard's name ({shown(shard.name)})"
    else:
        return ""
    return (f"; coverage.py cannot combine the shards it left in {shown(str(shard_dir))}: {named} "
            "holds bytes that are not UTF-8, and coverage.py stores every path as UTF-8. "
            "Rename it to UTF-8 and run the lane again")


def _no_artifact_head(root: Path, lane: Lane, stale: list[str], reuse: bool = False) -> str:
    """What the lane failed to do, in the words the disk supports.

    An empty `.crapkit/` and a leftover file are the same failure — this run
    wrote nothing — and they read completely differently to whoever has to fix
    it. Told "produced no artifact at .crapkit/cov/py.json" about a path that
    holds a report, a reader concludes crapkit cannot see the file, so a lane
    whose artifact IS the leftover says which run the file belongs to instead.

    When the artifact is not on disk at all, that path leads: it is where the
    reader has to look, and the recover skill triages on "produced no artifact
    at <path>". The leftover results file rides behind it as a second clause.

    On reuse there was no run: the file is what the LAST attempt left, and the
    sentence names the flag that reached it, so the reader knows which door
    they came through and that a rewrite of the file opens it again.
    """
    if not stale:
        return f"produced no artifact at {lane.artifact}"
    leftover, predates, is_ = _leftover_words(stale)
    if reuse:
        return (f"wrote no artifact on its last attempt - {leftover} {predates} it and {is_} "
                "the previous run's, which --reuse-artifacts will not score")
    if (root / lane.artifact).is_file():
        return f"wrote no artifact this run - {leftover} {predates} it and {is_} the previous run's"
    return f"produced no artifact at {lane.artifact}, and {leftover} {is_} the previous run's"


def _leftover_words(stale: list[str]) -> tuple[str, str, str]:
    """`the a.json on disk` with `predates` and `is`, or `the a.json and b.xml
    on disk` with `predate` and `are`: a lane leaves its results file behind
    beside its artifact, and one verb for two files read as a typo."""
    if len(stale) == 1:
        return f"the {stale[0]} on disk", "predates", "is"
    return f"the {', '.join(stale[:-1])} and {stale[-1]} on disk", "predate", "are"


class UnwrittenArtifact(ToolError):
    """The refusal a lane draws when its attempt wrote no artifact, or left a
    declared file unwritten. `refused` is the sha256 of each leftover put back
    in place, keyed by its declared path: what the fold persists as the
    refusal and what reuse reads back. Empty when nothing was left behind."""

    def __init__(self, message: str, refused: dict[str, str]) -> None:
        super().__init__(message)
        self.refused = refused


def _raise_no_artifact(root: Path, lane: Lane, log_path: Path, exit_code: int | None,
                       refused: dict[str, str] | None = None, *, reuse: bool = False) -> None:
    """Name the retained log before quoting its last 500 characters.

    The current file and optional .1 backup hold the newest command output.

    `refused` is the leftover files with the sha256 each holds; the message
    names them, the error carries them."""
    refused = refused or {}
    detail = f" (command exit {exit_code})" if exit_code is not None else ""
    tail = _log_tail(log_path)
    hint = f"; last output: {tail}" if tail else ""
    raise UnwrittenArtifact(
        f"lane {lane.name!r} {_no_artifact_head(root, lane, list(refused), reuse)}{detail}"
        f"; lane log: {shown(str(log_path))}{hint}{_missing_plugin_hint(tail, lane)}"
        f"{_shard_hint(root, lane)}", refused)


def _run_attempts(root: Path, lane: Lane, owner=None) -> int | None:
    """The exit code of the attempt that wrote every declared file, or the
    refusal naming what the last attempt left unwritten.

    Existence was the whole check until 0.4.12, so a lane failed loud exactly
    once — on the first run, against an empty `.crapkit/` — and scored the
    PREVIOUS run's file on every run after that. A vitest lane without
    reportOnFailure and a pytest run that dies in collection both land here, and
    what comes out is a confident grade off a measurement nothing took. The
    declared files sit aside while the attempts run (lane_outputs), so a
    command that only touches the old report wrote nothing, and one that
    rewrites the same bytes wrote them. A file that is not there at all is the
    refusal crapkit already had, and a missing results_artifact has its own
    sentence one layer up.
    """
    log_path = _lane_log_path(root, lane)
    with owned(root, lane.name, declared_files(lane)) as outputs:
        exit_code = _attempts(root, lane, log_path, owner, outputs)
    if _complete(root, lane, exit_code, outputs.leftovers):
        return exit_code
    _raise_no_artifact(root, lane, log_path, exit_code, outputs.leftovers)
    return None  # unreachable; keeps the signature honest


def _attempts(root: Path, lane: Lane, log_path: Path, owner, outputs) -> int | None:
    exit_code: int | None = None
    for attempt in range(1, lane.retries + 2):
        outputs.clear()
        exit_code = _attempt_once(root, lane, log_path, attempt, owner)
        if exit_code is not None and not outputs.unwritten() and outputs.written(lane.artifact):
            return exit_code
    return exit_code


def _complete(root: Path, lane: Lane, exit_code: int | None, leftovers: dict) -> bool:
    """The last attempt finished, wrote the artifact, and no leftover went back."""
    return exit_code is not None and not leftovers and (root / lane.artifact).is_file()


class _Before(NamedTuple):
    """What a lane run reads off the tree before it starts: the proof, the
    content record of the files under its scopes (None when git cannot give
    one), and the untracked files already there."""
    measured: Proof
    sources: dict | None
    untracked: frozenset


class _Trace(NamedTuple):
    """What the run left: the proof taken before it, the content record it can
    vouch for (None when git gave none), and the untracked files the run itself
    wrote (`byproducts`)."""
    measured: Proof
    sources: dict | None
    byproducts: frozenset


def _before_run(lane: Lane, fresh: Freshness) -> _Before:
    outputs = fresh.outputs(lane)
    return _Before(measurement_proof(fresh.root, lane, outputs),
                   lane_record(fresh.root, lane, fresh.scope_paths, outputs), _untracked(fresh.root))


def _after_run(lane: Lane, fresh: Freshness, coverage: dict, before: _Before) -> _Trace:
    """The record as the run left the files under its scopes and every in-tree
    file its artifact measured."""
    measured = [path for path in coverage if not _escapes_repo(path)]
    after = lane_record(fresh.root, lane, fresh.scope_paths, fresh.outputs(lane), measured)
    kept = None if before.sources is None or after is None else settled(before.sources, after)
    return _Trace(before.measured, kept,
                  _byproducts(fresh.root, lane, before.untracked, fresh.stamps))


def _untracked(root: Path) -> frozenset[str]:
    try:
        return frozenset(untracked_files(root))
    except GitError:
        return frozenset()


def _byproducts(root: Path, lane: Lane, before: frozenset, stamps: Stamps) -> frozenset[str]:
    """The untracked files this run wrote, plus the ones earlier runs of the
    lane wrote that are still there. A pytest-cov lane leaves `.coverage` at the
    root and a python lane with bytecode on leaves `__pycache__` under its
    scopes; a repo whose .gitignore holds only `.crapkit/`, which is what
    `crapkit init` writes, never had a clean tree to prove reuse on again."""
    now = _untracked(root) - declared_outputs(root, lane)
    earlier = stamps.byproducts(lane.artifact)
    return (now - before) | (now & earlier)


def _stamp_entry(git: GitFacts, lane: Lane, seconds: float, provenance: dict,
                 trace: _Trace, fresh: Freshness) -> dict:
    """What produced this artifact: the commit reuse judges staleness against,
    plus the wall seconds the parallel scheduler starts the slowest lane on.
    `proof` is the measurement key when it held from start to finish, else "",
    and then `unproved` says why; it is named apart from `Lane.inputs`, which
    holds paths, not a hash. `proof_parts` keeps the named parts the key hashes,
    so a later rerun can say which of them moved. `blobs` holds the git blob id of
    each file under the lane's scopes as the run left it: what its line numbers
    point into (lane_sources), left out when git could not give them.
    `byproducts` names the untracked files the run wrote, which no later proof
    counts as a change.

    Empty in a non-git sandbox (unit tests), which records nothing. Lanes hand
    their stamp back rather than writing it, so N of them running at once cannot
    lose each other's entry through a read-modify-write of one shared file.
    """
    try:
        commit = git.head_commit()
    except GitError:
        return {}
    return {"commit": commit, "lane": lane.name, "seconds": round(seconds, 1),
            **_held_proof(lane, trace, fresh), "artifacts": _artifact_digests(lane, provenance),
            "byproducts": sorted(trace.byproducts), **_blobs_field(trace)}


def _held_proof(lane: Lane, trace: _Trace, fresh: Freshness) -> dict:
    """The proof taken before the run and its parts when it still held after,
    else why it did not: the changes it was measured over, or what moved."""
    after = measurement_proof(fresh.root, lane, fresh.outputs(lane) | trace.byproducts)
    if trace.measured.key and trace.measured.key == after.key:
        return {"proof": after.key, "proof_parts": after.parts}
    return {"proof": "", "proof_parts": {}, "unproved": unproved(trace.measured, after)}


def _blobs_field(trace: _Trace) -> dict:
    return {} if trace.sources is None else {"blobs": trace.sources}


def _artifact_digests(lane: Lane, provenance: dict) -> dict:
    digests = {lane.artifact: provenance["artifact_sha256"]}
    if lane.results_artifact:
        digests[lane.results_artifact] = provenance["results_artifact_sha256"]
    return digests


def refusal_stamp(root: Path, lane: Lane, error: object) -> dict[str, dict]:
    """The stamp that records the lane's artifact as the one its last attempt
    failed to write, keyed by artifact path like every stamp, or {} when
    `error` carries no such refusal.

    The refusal is the one `_run_attempts` raised, not a second reading of the
    disk: the attempt already knew which file it put back and what it held. A
    lane refused before it ran (the container guard) and a lane that failed
    after writing its artifact (a file that does not parse, one from another
    tree) raise something else and record nothing, which keeps
    `--reuse-artifacts` the way through the guard and leaves reuse to judge the
    written file on its own terms. A refusal about the results artifact alone
    records nothing either: the stamp is the coverage file's.
    """
    refused = getattr(error, "refused", {}).get(lane.artifact)
    return refusal_entry(read(root), lane, refused) if refused else {}


def lane_order(root: Path, lanes: list[Lane], stamps: Stamps | None = None) -> list[Lane]:
    """Longest recorded lane first: with lanes running concurrently the makespan
    is the slowest lane, so starting it last wastes exactly its own duration.
    Sorting is stable, so unrecorded lanes and ties keep declaration order.
    A recorded duration only ever changes WHICH lane starts first — results are
    merged in declaration order regardless, so it cannot move a score."""
    stamps = stamps if stamps is not None else read(root)
    return sorted(lanes, key=lambda lane: -(stamps.seconds(lane) or 0.0))


def _warn_stale_artifact(git, lane: Lane, scope_paths: dict | None,
                         fresh: Freshness | None = None) -> None:
    """On reuse: say which files under this lane's scopes moved since the
    artifact measured them, or that git could not say, and the rerun that
    measures them."""
    from .invocation import _self

    fresh = fresh if fresh is not None else Freshness(git.root, (lane,), scope_paths, git=git)
    drift = fresh.warning(lane)
    if drift:
        print(f"crapkit: lane {lane.name!r} reuses {lane.artifact}; {drift}, so its coverage "
              f"may be stale; rerun the lane (`{_self()} coverage --lane {lane.name}`) to "
              "measure the tree as it is", file=sys.stderr)


def _facts(root: Path, git: GitFacts | None) -> GitFacts:
    """A whole command shares one context; a lone caller gets its own."""
    return git if git is not None else GitFacts(root)


def lane_reuse_verdict(root: Path, lane: Lane) -> ReuseVerdict:
    """Whether automatic reuse is proved for this lane, and the first condition
    that fails when it is not (lane_freshness)."""
    return Freshness(root, (lane,)).reuse(lane)


def lane_reuse_commit(root: Path, lane: Lane) -> str:
    """The commit the lane's artifact was built at when automatic reuse is
    proved, or "". `lane_reuse_verdict` says why not."""
    return lane_reuse_verdict(root, lane).commit


def lane_sources_unchanged(root: Path, lane: Lane, scope_paths: dict, git=None) -> bool:
    """Deprecated since 0.8.1 and removed in 0.9: whether every file under the
    lane's scopes holds the bytes its artifact measured. Read
    `lane_freshness.Freshness(root, lanes, scope_paths).lines(lane)` instead,
    which says why when they do not."""
    warnings.warn("crapkit.lanes.lane_sources_unchanged is deprecated and goes in 0.9; read "
                  "crapkit.lane_freshness.Freshness(...).lines(lane) instead",
                  DeprecationWarning, stacklevel=2)
    return not Freshness(root, (lane,), scope_paths, git=git).lines(lane)


def staleness_reads(root: Path, lanes, scope_paths: dict, git=None):
    """Deprecated since 0.8.1 and removed in 0.9: a context whose value
    `lane_sources_unchanged` takes as `git`, the caller's own GitFacts when it
    passes one. `lane_freshness.Freshness(root, lanes, scope_paths)` reads the
    stamp file once for every lane and starts the git reads it needs itself."""
    warnings.warn("crapkit.lanes.staleness_reads is deprecated and goes in 0.9; use "
                  "crapkit.lane_freshness.Freshness(root, lanes, scope_paths) as a context instead",
                  DeprecationWarning, stacklevel=2)
    return nullcontext(_facts(root, git))


def _read_and_parse(lane: Lane, root: Path,
                    artifact_path: Path, dead_lines=None) -> tuple[dict[str, list[FnCoverage]], str]:
    """This lane's coverage, plus the sha256 of the artifact's own bytes.

    The reader takes the PATH, not the text: a whole-document parse needs the
    bytes and their UTF-8 decode both live before the first function is
    attributed, which is two copies of an artifact that runs to hundreds of MB.
    Streaming holds one chunk and one file's coverage instead, and hashes the
    bytes on the way past, so the recorded digest costs no second read.

    The lane's format adapter reads it, and yields uncovered lines from the same
    walk. An optional collector combines them for this command without keeping
    separate lane maps or global state.
    """
    per_file, dead, digest = lane_format(lane).read(lane, root, artifact_path)
    if dead_lines is not None:
        dead_lines.add(artifact_path, dead, digest)
    return per_file, digest


# A path the runner did not write relative to this checkout: absolute, drive
# lettered, or climbing out of the tree. Both parsers rebase a file INSIDE the
# repo to a repo-relative path, so one that is not either came from elsewhere or
# was spelled absolutely by a runner told to spell it that way. Which of the two
# is decided against the root, below; the shape alone does not say.
_DRIVE = re.compile(r"[A-Za-z]:[\\/]")


def _is_absolute(path: str) -> bool:
    """Absolute in either spelling: a POSIX root, or a drive letter."""
    return path.startswith("/") or _DRIVE.match(path) is not None


def _escapes_repo(path: str) -> bool:
    return _is_absolute(path) or path.startswith("../")


def _unreached_paths(lane: Lane, matchers: tuple[ScopeMatch, ...],
                     coverage: dict) -> tuple[str, ...]:
    """The paths this lane's scopes declare when NOTHING the artifact measured
    reaches any of them, else (). Empty too when the lane's scopes declare no
    path at all: nothing to compare against is not evidence.

    `owning_scope` is the reach test, so "could a scope claim this path?" can
    never drift from the rule that assigns files to scopes. It is asked only of
    keys the runner wrote relative to this checkout (`_relative_keys`).

    The DECLARED path, not the matcher's directory prefix: a scope may name an
    individual file, and the prefix half of that is `src/faro/core.py/`, a path
    that exists neither in the config the reader is about to open nor on disk.
    """
    relative = _relative_keys(lane, coverage)
    if not matchers or any(owning_scope(path, matchers) for path in relative):
        return ()
    return tuple(dict.fromkeys(m.path for m in matchers))


def _relative_keys(lane: Lane, coverage: dict):
    """The measured keys the runner wrote relative to this checkout, the only
    ones a scope can claim. The coverage.py reader glues path_prefix onto every
    key, and `backend/` + `/other/checkout/a.py` is a path under a `backend`
    scope, as any key is under a root scope; asked of such keys, the check
    found the scope reached and every function in it scored untested with
    exit 0. A key that escapes the repo goes to the refusals instead."""
    as_reported = lane_format(lane).as_reported
    return (key for key in coverage if not _escapes_repo(as_reported(lane, key)))


def _escaped_paths(lane: Lane, coverage: dict) -> list[str]:
    """The measured files the runner did not write relative to this checkout,
    spelled the way the artifact spells them. The format's own inverse takes
    back only what its reader added: path_prefix on a coveragepy key, nothing
    on an istanbul one, which never reads the key."""
    as_reported = lane_format(lane).as_reported
    reported = (as_reported(lane, path) for path in coverage)
    return sorted(path for path in reported if _escapes_repo(path))


def _split_escaped(root: Path, escaped: list[str]) -> tuple[list[str], list[str]]:
    """(paths from another tree, absolute paths that land under this root).

    An absolute path is placed by the rule the istanbul reader rebases its keys
    with (repopath.Placing), so a junction, a symlink, a lower-case drive, a
    `\\\\?\\` prefix or the admin share naming this checkout lands in it, and the
    reader and this check cannot disagree about a key. `../` stays elsewhere
    on purpose: it is relative to the runner's working directory, which the
    artifact never records, so there is nothing to place it against."""
    placing = Placing(root)
    elsewhere: list[str] = []
    within: list[str] = []
    for path in escaped:
        lands = _is_absolute(path) and placing(path) is not None
        (within if lands else elsewhere).append(path)
    return elsewhere, within


def _zero_overlap(lane: Lane, coverage: dict, declared) -> str:
    """The finding both verdicts open on, written once: what the artifact
    measured, and that none of it is in scope. The two messages part company
    after it, and a sentence kept in two places is a sentence that drifts."""
    return (f"lane {lane.name!r} measured {len(coverage)} file(s), none of them under the "
            f"paths its scopes declare ({first_few(sorted(_scope_as_written(p) for p in declared))})")


def _scope_as_written(scope_path: str) -> str:
    """A declared scope path as crapkit.toml writes it: the root is '' once
    read, and printed bare it named nothing, `declare ()`."""
    return scope_path or "."


def _wrong_tree_message(lane: Lane, coverage: dict, declared, outside: list[str]) -> str:
    return (f"{_zero_overlap(lane, coverage, declared)}, and {len(outside)} of them "
            f"outside this checkout entirely - {lane.artifact} describes a different tree, "
            f"so joining it would score every function in those scopes untested; it reports "
            f"paths like {first_few(sorted(outside))}. {lane_format(lane).WRONG_TREE_FIX}")


def _absolute_message(lane: Lane, coverage: dict, declared, inside: list[str]) -> str:
    return (f"{_zero_overlap(lane, coverage, declared)}, and {len(inside)} of them written "
            f"as absolute paths that DO sit under this checkout - {lane.artifact} measured "
            f"this tree and spelled it absolutely, and the join is on root-relative paths, "
            f"so it still matches nothing and every function in those scopes would score "
            f"untested; it reports paths like {first_few(sorted(inside))}. {lane_format(lane).ABSOLUTE_FIX}")


def _unmeasured_message(lane: Lane, coverage: dict, declared, meant: str) -> str:
    reports = f"; it measured {first_few(sorted(coverage))}" if coverage else ""
    return (f"{_zero_overlap(lane, coverage, declared)}, so every function in those "
            f"scopes will score untested{reports} - either nothing in them is exercised yet, "
            f"{_unmeasured_reading(lane, meant)}")


def _unmeasured_reading(lane: Lane, meant: str) -> str:
    """The other reading. A lane that sets path_prefix was told it needed one,
    while the prefix it set was what keyed every measured file outside its
    scopes; the value crapkit read is the one to check, and `meant` says which
    value keys a file the runner named under those scopes."""
    if lane.path_prefix:
        return (f"or path_prefix {lane.path_prefix!r}, which crapkit.toml sets for this lane, "
                f"does not rebase the runner's paths onto those scopes; {meant}")
    return lane_format(lane).UNMEASURED_READING


# How many of the runner's keys the search for the meant path_prefix tries: it
# runs only when the warning fires, and one hit is the answer.
_MEANT_PROBES = 20
_NOTHING_MEANT = ("no file the runner named is under those scopes with or without it, so set "
                  "path_prefix to the directory the runner's paths are relative to")


def _prefix_meant(lane: Lane, coverage: dict, matchers, root: Path) -> str:
    """The path_prefix a lane that sets one was meant to hold: none, or a path
    its scopes declare, under which a key the runner wrote names a file on disk
    that those scopes claim. Asked of a few keys, and only for such a lane."""
    if not lane.path_prefix:
        return ""
    found = next((pair for pair in _prefix_candidates(lane, coverage, matchers)
                  if _claimed_file(root, *pair, matchers)), None)
    return _NOTHING_MEANT if found is None else _meant(*found)


def _prefix_candidates(lane: Lane, coverage: dict, matchers) -> list[tuple[str, str]]:
    """(prefix, key the runner wrote): no prefix first, then each declared path."""
    as_reported = lane_format(lane).as_reported
    written = sorted(as_reported(lane, key) for key in coverage)[:_MEANT_PROBES]
    return [(prefix, key) for prefix in _declared_prefixes(matchers) for key in written]


def _declared_prefixes(matchers) -> list[str]:
    """No prefix, then each path the lane's scopes declare, the root excepted:
    under a root scope every key is claimed already."""
    return ["", *dict.fromkeys(m.path for m in matchers if m.path != ".")]


def _claimed_file(root: Path, prefix: str, key: str, matchers) -> bool:
    path = posixpath.join(prefix, key) if prefix else key
    return owning_scope(path, matchers) is not None and (root / path).is_file()


def _meant(prefix: str, key: str) -> str:
    if not prefix:
        return (f"without path_prefix the runner's {key} is a file those scopes claim, so drop "
                "path_prefix from this lane")
    return (f"path_prefix = {prefix!r} would key the runner's {key} as {prefix}/{key}, a file "
            "those scopes claim")


def _judge_artifact_scope(lane: Lane, coverage: dict, scope_paths: dict | None,
                          root: Path) -> None:
    """Say something when a lane's artifact reaches none of the scopes it claims.

    Coverage joins on path and nothing else, so such an artifact contributes
    exactly nothing and every function in those scopes reads `untested` — a
    confident "N untested, grade F" assembled out of a tooling mistake, which
    is worse than the exit 5 a missing artifact earns because it looks like an
    answer. Two worktrees of one branch reach it quietly: a venv whose editable
    install points at the other checkout makes coverage.py measure that tree.

    Three verdicts, because zero overlap has three readings and the paths tell
    them apart, against the root. Measured files OUTSIDE the root can only be
    another tree, and that fails the lane. Absolute paths that resolve UNDER it
    are this tree with the runner spelling every path absolutely: the join is
    root-relative, so it matches nothing either, and that fails the lane too —
    with the runner's own knob named, because the venv advice above is not the
    cause and path_prefix only prepends. In-tree relative paths that simply miss
    the scopes are the greenfield shape as well — a suite that imports none of
    the scoped source yet, which SHOULD score untested — so that one warns and
    scores on.

    A mixed artifact is another tree. A path from somewhere else can only have
    come from somewhere else, and the absolute in-tree ones are what the same
    wrong run reports about the files it did reach.
    """
    matchers = lane_matchers(lane, scope_paths or {})
    declared = _unreached_paths(lane, matchers, coverage)
    if not declared:
        return
    elsewhere, inside = _split_escaped(root, _escaped_paths(lane, coverage))
    if elsewhere:
        raise ToolError(_wrong_tree_message(lane, coverage, declared, elsewhere))
    if inside:
        raise ToolError(_absolute_message(lane, coverage, declared, inside))
    meant = _prefix_meant(lane, coverage, matchers, root)
    print(f"crapkit: {_unmeasured_message(lane, coverage, declared, meant)}", file=sys.stderr)


def _results_summary(root: Path, lane: Lane) -> tuple[set[str], dict, str]:
    """The lane junit's failing ids and counts, or a ToolError saying why the
    report cannot be read. The message names the report and not the lane: one
    caller, two paths out of it, and each supplies the lane itself. The re-raise
    reaches the runner, which prefixes the lane name; `_warn_unreadable_results`
    names the lane in its own sentence."""
    from .junitparse import suite_summary

    results_path = root / lane.results_artifact
    if not results_path.is_file():
        raise ToolError(f"results_artifact {lane.results_artifact} is missing")
    raw = results_path.read_bytes()
    failed, counts = suite_summary(raw)
    return failed, counts, hashlib.sha256(raw).hexdigest()


def _warn_unreadable_results(lane: Lane, reason: ToolError) -> None:
    """The path first: the reader's next move is opening that file."""
    print(f"crapkit: lane {lane.name!r} reused {lane.results_artifact} and cannot check "
          f"it: {reason}; the crashed-worker and no-new-failures checks cannot run for "
          "this lane", file=sys.stderr)


def _results_provenance(root: Path, lane: Lane, *, reuse_artifact: bool = False) -> dict:
    """Counts and failures from the lane's junit; {} when reuse read a report
    that is not there or says the run never finished.

    A run refuses both: a missing results file would make the no-NEW-failures
    check pass vacuously, and a report with no testcases is a suite that stopped
    before it measured anything. `--reuse-artifacts` is the operator saying run
    nothing and read what is on disk, and the coverage JSON beside that junit
    can be a salvage of a killed run, hand-combined from its shards. Refusing it
    there sent one reporter to the only other exit, deleting results_artifact
    from the config, which gives up both checks on every future run. Warning
    instead lands the lane on the no-counts path verify already documents.
    """
    try:
        failed, counts, digest = _results_summary(root, lane)
    except ToolError as reason:
        if not reuse_artifact:
            raise
        _warn_unreadable_results(lane, reason)
        return {}
    return {"failures": sorted(failed),
            "tests_total": counts["tests"], "tests_skipped": counts["skipped"],
            "results_artifact_sha256": digest}


def retest_lane(root: Path, lane: Lane, tests: set[str]) -> set[str]:
    with measurement_owner(root, (lane,)) as owner:
        return _retest_owned(root, lane, tests, owner)


def _retest_owned(root: Path, lane: Lane, tests: set[str], owner) -> set[str]:
    """Run the lane's retest_command on just these ids; return the ones that
    PASS the rerun (the flakes). Any doubt — no artifact, a crash, a timeout —
    keeps everything failed."""
    from .logs import command_log

    command, additions = _retest_template(lane.retest_command, tests)
    kwargs = launch_spec(root, lane).popen_kwargs(additions)
    log_path = _lane_log_path(root, lane)
    with owned(root, retest_owner(lane), (lane.results_artifact,)) as outputs:
        with command_log(log_path, max_bytes=lane.log_max_bytes, append=True) as fh:
            fh.write(f"\n--- flake retest ---\n$ {command}\n")
            fh.flush()
            code = _retest_exit(command, lane, fh, owner, kwargs)
        passes = _retested_passes(root, lane) if outputs.written(lane.results_artifact) else set()
    return tests & passes if code in (0, 1) else set()


def _retest_exit(command: str, lane: Lane, fh, owner, kwargs: dict) -> int | None:
    """The retest's exit code, or None when it stalled and was killed."""
    try:
        return run_bounded(command, _deadline(lane), stream=fh,
                           no_progress=_no_progress(lane), owner=owner, **kwargs)
    except NoProgress:
        return None


def _retest_template(template: str, tests: set[str]) -> tuple[str, dict[str, str]]:
    from .procs import prepare_template

    ordered = sorted(tests)
    files = sorted({test.split("::", 1)[0] for test in ordered})
    names = "|".join(re.escape(test.split("::", 1)[1]) for test in ordered if "::" in test)
    return prepare_template(template, {"tests": ordered, "files": files, "names": [names]})


def _retested_passes(root: Path, lane: Lane) -> set[str]:
    """The tests the report the retest wrote says passed; none when it cannot
    be read."""
    from .junitparse import passed_test_ids

    try:
        return passed_test_ids((root / lane.results_artifact).read_bytes())
    except (OSError, ToolError):
        return set()


class LaneOutcome(NamedTuple):
    """One lane's result. `stamp` is what the caller must persist (empty when the
    lane reused an artifact, or when there is no git repo to stamp against)."""
    coverage: dict[str, list[FnCoverage]]
    provenance: dict
    stamp: dict


def _run_or_reuse(lane: Lane, fresh: Freshness, reuse_artifact: bool,
                  owner=None) -> tuple[int | None, float]:
    """Reuse warns and costs nothing; a real run returns its exit code and wall seconds.

    The container guard belongs on this side of the branch. It names an OOM a
    python suite hits under a container memory cap, and `--reuse-artifacts`
    launches no suite: it parses a file already on disk, which costs no memory
    the guard is about. Sitting a line above this call, it refused the crapkit
    image reading host-built artifacts, and its message said something untrue
    about what the lane was going to do.
    """
    if reuse_artifact:
        _refuse_unwritten_artifact(fresh.root, lane, fresh.stamps)
        _warn_stale_artifact(fresh.git, lane, fresh.scope_paths, fresh)
        return None, 0.0
    _refuse_container_python(lane)
    started = time.monotonic()
    return _run_attempts(fresh.root, lane, owner), time.monotonic() - started


def _refuse_unwritten_artifact(root: Path, lane: Lane, stamps: Stamps) -> None:
    """On reuse: the refusal the failed attempt raised, again, while the file
    on disk is still the one that attempt left. Reuse read that file back and
    scored it, so a dead lane's old numbers became the trusted baseline. A file
    that is gone falls through to `_artifact_path`, whose sentence is the one
    the recover skill triages on. A stamp record crapkit cannot read may have
    held that refusal, so it refuses too, unless the store's copy answers."""
    refusal = stamps.refusal(lane.artifact)
    if refusal.kind == "leftover":
        _raise_no_artifact(root, lane, _lane_log_path(root, lane), None,
                           {lane.artifact: file_sha256(root / lane.artifact)}, reuse=True)
    if refusal.kind == "unknown":
        _refuse_unreadable_stamp(lane, refusal)


def _refuse_unreadable_stamp(lane: Lane, refusal) -> None:
    """The record that would say whether the file on disk is a failed
    attempt's leftover cannot be read, so reuse cannot score it."""
    from .invocation import _self

    raise ToolError(
        f"lane {lane.name!r}: {refusal.cause(lane.artifact)}; rerun the lane "
        f"(`{_self()} coverage --lane {lane.name}`), or delete {STAMPS_FILE} to reuse the file "
        "as it stands")


def _artifact_path(root: Path, lane: Lane) -> Path:
    """The artifact, or the same refusal a run that wrote none raises.

    Reuse is what reaches here — the run path refuses inside _run_attempts — and
    it was the one lane refusal that named no log, on the reading that a reused
    artifact had no run behind it. The previous run's log is usually sitting
    there with the story, and a reader told to look for `lane log:` on every
    refusal has nowhere to go when one omits it.
    """
    path = root / lane.artifact
    if not path.is_file():
        _raise_no_artifact(root, lane, _lane_log_path(root, lane), None)
    return path


def run_lane(root: Path, lane: Lane, *, reuse_artifact: bool = False,
             scope_paths: dict | None = None, git: GitFacts | None = None,
             dead_lines=None, owner=None, freshness: Freshness | None = None) -> LaneOutcome:
    """One lane run, or one reuse of its artifact. `freshness` is the command's
    own (one stamp read for every lane); a lone caller gets one of its own,
    read once the lane's outputs are held."""
    ownership = nullcontext(owner) if owner is not None else measurement_owner(root, (lane,))
    with ownership as held:
        fresh = freshness or Freshness(root, (lane,), scope_paths, git=_facts(root, git))
        return _run_owned_lane(lane, fresh, reuse_artifact, dead_lines, held)


def _run_owned_lane(lane: Lane, fresh: Freshness, reuse_artifact: bool, dead_lines,
                    owner) -> LaneOutcome:
    root = fresh.root
    before = None if reuse_artifact else _before_run(lane, fresh)
    exit_code, seconds = _run_or_reuse(lane, fresh, reuse_artifact, owner)
    coverage, digest = _read_and_parse(lane, root, _artifact_path(root, lane), dead_lines)
    _judge_artifact_scope(lane, coverage, fresh.scope_paths, root)
    provenance = {
        "artifact_sha256": digest,
        "exit_code": exit_code,
        "parser": lane.parser,
        "scopes": list(lane.scopes),
    }
    if lane.results_artifact:
        provenance.update(_results_provenance(root, lane, reuse_artifact=reuse_artifact))
    stamp = {} if before is None else _stamp_entry(
        _facts(root, fresh.git), lane, seconds, provenance,
        _after_run(lane, fresh, coverage, before), fresh)
    return LaneOutcome(coverage, provenance, stamp)


def _output_lock(path: Path) -> Path:
    """Coordinate local outputs outside directories their runners may replace."""
    # The bytes the OS names them by: a checkout under a Latin-1 directory, or a
    # host named in one, has no UTF-8 spelling, and encoding one raised here.
    key = os_bytes(os.path.normcase(str(path.resolve())))
    host = hashlib.sha256(os_bytes(socket.gethostname())).hexdigest()[:16]
    directory = Path.home() / ".cache" / "crapkit" / "measurements" / host
    return directory / ("measurement-" + hashlib.sha256(key).hexdigest() + ".lock")


@contextmanager
def measurement_owner(root: Path, lanes):
    """Own resolved outputs, plus this checkout's shared log and stamp state.

    While it is held no attempt at these lanes runs anywhere, so a declared file
    under .crapkit/aside/ was left by an attempt that never finished (a kill, a
    CI timeout): it goes back before anything reads the lane's paths. Without
    that, --reuse-artifacts found no artifact and the next attempt's exit
    removed the copy."""
    if not lanes:
        yield None
        return
    outputs = {root / name for lane in lanes for name in declared_files(lane)}
    paths = {_output_lock(path) for path in outputs}
    paths.add(root / ".crapkit" / "measurement.lock")
    with own_processes(sorted(paths)) as owner:
        _put_back_strays(root, lanes)
        yield owner


def _put_back_strays(root: Path, lanes) -> None:
    for lane in lanes:
        for owner, names in owners(lane):
            for stray in put_back(root, owner, names):
                print(f"crapkit: lane {lane.name!r}: {_stray_line(root, stray)}", file=sys.stderr)


def _stray_line(root: Path, stray) -> str:
    """What happened to a file an attempt that never finished set aside, and
    what to do when crapkit could not put it back."""
    if stray.back:
        return (f"{stray.name} is back at its path; an attempt that did not finish (a kill "
                "or a timeout) had set it aside under .crapkit/aside/")
    return (f"an attempt that did not finish (a kill or a timeout) wrote {stray.name}, and the "
            f"previous one is at {stray.copy.relative_to(root).as_posix()}: move it back to "
            f"{stray.name} to reuse it, or rerun the lane, which removes it")


# --- 0.8.0's name, kept one release -------------------------------------------


def suite_drops(previous: dict, current: dict, *,
                fraction: float = lane_results.SUITE_DROP_FRACTION) -> list[str]:
    """0.8.0's suite-drop check, where `previous` was the last trusted run's lane
    provenance. It warns and answers what `lane_results.suite_drops` answers
    for that one run; 0.9.0 removes it."""
    warnings.warn("crapkit.lanes.suite_drops is deprecated and goes in 0.9.0; call "
                  "crapkit.lane_results.suite_drops(behind, current), where behind returns "
                  "the trusted runs newest first", DeprecationWarning, stacklevel=2)
    return lane_results.suite_drops(lambda: [{"lanes": previous}], current, fraction=fraction)
