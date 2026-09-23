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
from contextlib import nullcontext
from functools import lru_cache
import json
import os
import posixpath
import re
import socket
import sys
import time
from pathlib import Path
from typing import IO, NamedTuple

from .config import Lane
from .coverage_istanbul import FnCoverage
from .coverage_format import lane_format
from .errors import CrapkitError, GitError, ToolError
from .gitio import GitFacts, worktree_root
from .lane_command import launch_spec, pytest_python
from .procs import NoProgress, own_processes, run_bounded
from .universe import ScopeMatch, owning_scope, path_matchers


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
    if not log_path.is_file():
        return []
    return log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()


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
        except ToolError as failed:  # a failed start: the log says why, as it does for a kill
            fh.write(f"\n[crapkit] {failed}\n")
            raise
        if code is None:
            fh.write(f"\n[crapkit] timed out after {lane.timeout_seconds}s; killed\n")
            raise ToolError(f"lane {lane.name!r} timed out after {lane.timeout_seconds}s "
                            f"(attempt {attempt}); log: {log_path}")
        fh.write(f"\n(exit {code})\n")
    return code


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
    return (f" — the --cov flags come from the pytest-cov package, which has to be "
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
    target = Path(os.path.relpath(root / lane.artifact, shard_dir)).as_posix()
    noun, verb = ("shard", "sits") if len(shards) == 1 else ("shards", "sit")
    return (f"; {len(shards)} coverage {noun} ({shards[0].name}, ...) {verb} in "
            f"{shard_dir}, which is what a killed parallel run leaves behind: "
            f"`coverage combine && coverage json -o {target}` there, then a "
            "re-run with --reuse-artifacts, scores what that suite did measure")


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
    leftover = f"the {', '.join(stale)} on disk"
    if reuse:
        return (f"wrote no artifact on its last attempt — {leftover} predates it and is the "
                "previous run's, which --reuse-artifacts will not score")
    if (root / lane.artifact).is_file():
        return f"wrote no artifact this run — {leftover} predates it and is the previous run's"
    return f"produced no artifact at {lane.artifact}, and {leftover} is the previous run's"


class UnwrittenArtifact(ToolError):
    """The refusal a lane draws when its attempt wrote no artifact, or left a
    declared file unwritten. `refused_mtimes` is the modification time of each
    leftover, keyed by its declared path: what the fold persists as the
    refusal and what reuse reads back. Empty when nothing was left behind."""

    def __init__(self, message: str, refused_mtimes: dict[str, int]) -> None:
        super().__init__(message)
        self.refused_mtimes = refused_mtimes


def _raise_no_artifact(root: Path, lane: Lane, log_path: Path, exit_code: int | None,
                       refused: dict[str, int] | None = None, *, reuse: bool = False) -> None:
    """Name the retained log before quoting its last 500 characters.

    The current file and optional .1 backup hold the newest command output.

    `refused` is the leftover files with the modification time each still
    carries; the message names them, the error carries them."""
    refused = refused or {}
    detail = f" (command exit {exit_code})" if exit_code is not None else ""
    tail = _log_tail(log_path)
    hint = f"; last output: {tail}" if tail else ""
    raise UnwrittenArtifact(
        f"lane {lane.name!r} {_no_artifact_head(root, lane, list(refused), reuse)}{detail}"
        f"; lane log: {log_path}{hint}{_missing_plugin_hint(tail, lane)}"
        f"{_shard_hint(root, lane)}", refused)


def _declared_files(lane: Lane) -> tuple[str, ...]:
    """Every path the lane says its command writes."""
    return (lane.artifact, lane.results_artifact) if lane.results_artifact else (lane.artifact,)


def _mtime_ns(path: Path) -> int | None:
    """The file's modification time, or None when it is not there."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _declared_mtimes(root: Path, lane: Lane) -> dict[str, int | None]:
    return {name: _mtime_ns(root / name) for name in _declared_files(lane)}


def _unwritten(root: Path, before: dict[str, int | None]) -> dict[str, int]:
    """The declared files that were already on disk and that this attempt did
    not rewrite, each with the modification time it still carries.

    Existence was the whole check until 0.4.12, so a lane failed loud exactly
    once — on the first run, against an empty `.crapkit/` — and scored the
    PREVIOUS run's file on every run after that. A vitest lane without
    reportOnFailure and a pytest run that dies in collection both land here, and
    what comes out is a confident grade off a measurement nothing took.

    A file that is not there at all is left out: that is the refusal crapkit
    already had, and a missing results_artifact has its own sentence one layer
    up. mtime, not content, because a runner that rewrites a byte-identical
    report still bumps it, so an unchanged rerun stays green. The time rides
    on the refusal so reuse can recognize the same leftover later.
    """
    return {name: stamp for name, stamp in before.items()
            if stamp is not None and _mtime_ns(root / name) == stamp}


def _run_attempts(root: Path, lane: Lane, owner=None) -> int | None:
    log_path = _lane_log_path(root, lane)
    exit_code: int | None = None
    unwritten: dict[str, int] = {}
    for attempt in range(1, lane.retries + 2):
        before = _declared_mtimes(root, lane)
        exit_code = _attempt_once(root, lane, log_path, attempt, owner)
        unwritten = _unwritten(root, before)
        if exit_code is not None and not unwritten and (root / lane.artifact).is_file():
            return exit_code
    _raise_no_artifact(root, lane, log_path, exit_code, unwritten)
    return None  # unreachable; keeps the signature honest


def _stamps_path(root: Path) -> Path:
    return root / ".crapkit" / "artifacts.json"


def read_stamps(root: Path) -> dict:
    """The recorded artifact stamps: {artifact path: {commit, lane, seconds}},
    plus `refused_mtime_ns` on an artifact the lane's last attempt failed to
    write. A missing or hand-mangled file reads as no stamps at all."""
    path = _stamps_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _stamp_entry(git: GitFacts, lane: Lane, seconds: float, measured: str, provenance: dict) -> dict:
    """What produced this artifact: the commit reuse judges staleness against,
    plus the wall seconds the parallel scheduler starts the slowest lane on.
    `proof` is the measurement key when it held from start to finish, else "";
    it is named apart from `Lane.inputs`, which holds paths, not a hash.
    `proof_parts` keeps the digests a lane without `inputs` was proved by, so a
    later rerun can say which of them moved.

    Empty in a non-git sandbox (unit tests), which records nothing. Lanes hand
    their stamp back rather than writing it, so N of them running at once cannot
    lose each other's entry through a read-modify-write of one shared file.
    """
    try:
        commit = git.head_commit()
    except GitError:
        return {}
    proof = _measurement_proof(git.root, lane)
    clean = bool(measured) and measured == proof.key
    return {"commit": commit, "lane": lane.name, "seconds": round(seconds, 1),
            "proof": measured if clean else "", "proof_parts": proof.parts if clean else {},
            "artifacts": _artifact_digests(lane, provenance)}


def _artifact_digests(lane: Lane, provenance: dict) -> dict:
    digests = {lane.artifact: provenance["artifact_sha256"]}
    if lane.results_artifact:
        digests[lane.results_artifact] = provenance["results_artifact_sha256"]
    return digests


class _Proof(NamedTuple):
    """The key a stamp records, the digests a lane without `inputs` takes it
    over, and why there is no key: `why` is set exactly when `key` is ""."""
    key: str
    parts: dict
    why: str


def _measurement_key(root: Path, lane: Lane) -> str:
    """The proof a stamp records that nothing the lane reads moved while it ran.

    A lane that declares `inputs` is proved by those paths and its own config
    block, env included. Every other lane is proved by the whole clean checkout,
    crapkit.toml and the inherited environment.
    """
    return _measurement_proof(root, lane).key


def _measurement_proof(root: Path, lane: Lane) -> _Proof:
    if lane.inputs:
        key = _declared_inputs_key(root, lane)
        return _Proof(key, {}, "" if key else "its inputs have uncommitted changes")
    return _whole_tree_proof(root, lane)


def _inputs_key(commit: str, lane: Lane) -> str:
    """The `proof` a lane that declares `inputs` stamps: its own configuration,
    env and input paths included, bound to a commit."""
    payload = json.dumps(("inputs", commit, lane), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _declared_inputs_key(root: Path, lane: Lane) -> str:
    """HEAD bound to the lane's config, or "" while an uncommitted change touches
    its inputs: the artifact would describe the edit, not the commit. A lane
    output under the inputs is not such a change."""
    from .lane_changes import ChangeReads

    try:
        outputs = _output_names(root, lane)
        commit = GitFacts(root).head_commit()
        with ChangeReads(root, (), lane.inputs) as reads:
            dirty = _unless(reads.status_names(), outputs)
    except (GitError, OSError):
        return ""
    return "" if dirty else _inputs_key(commit, lane)


# Variables a shell or terminal keeps for its own bookkeeping. A `cd` moves
# OLDPWD and each new terminal or SSH login gets its own session ids, and no
# runner measures differently for them. Hashed, OLDPWD alone reran every lane of
# a large consumer repo on a clean, unchanged tree.
_SESSION_VARIABLES = frozenset({
    "OLDPWD", "PWD", "SHLVL", "_",
    "WT_SESSION", "TERM_SESSION_ID", "ITERM_SESSION_ID", "TMUX_PANE", "WINDOWID",
    "SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "SECURITYSESSIONID", "XDG_SESSION_ID",
})


def _whole_tree_proof(root: Path, lane: Lane) -> _Proof:
    """The clean worktree's HEAD, crapkit.toml's bytes, the lane's own table and
    the inherited environment, each as a digest, hashed together.

    Only a clean worktree qualifies, but the lanes' declared artifacts and
    results files do not count against it: every run rewrites them, and each
    stamp proves its own by their digests. crapkit.toml is hashed apart from the
    tree because callers can supply a lane without a tracked crapkit.toml.
    External files and services remain outside this proof.
    """
    try:
        config = _config_bytes(root)
        head, dirty = _worktree(root, _output_names(root, lane, config))
    except (GitError, OSError) as exc:
        return _Proof("", {}, f"nothing proves its inputs unchanged: {exc}")
    if dirty:
        return _Proof("", {}, f"the working tree has {len(dirty)} uncommitted change(s): {_sample(dirty)}")
    parts = {"commit": head, "config": _digest(config), "lane": _digest(_lane_bytes(lane)),
             "env": _environment_digests()}
    return _Proof(_digest(json.dumps(parts, sort_keys=True).encode("utf-8")), parts, "")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lane_bytes(lane: Lane) -> bytes:
    return json.dumps(lane, sort_keys=True).encode("utf-8")


def _environment_digests() -> dict[str, str]:
    """Each inherited variable the proof covers, as a short digest of its value.
    The stamp keeps these, never a value, so a rerun can name what changed."""
    return {name: _digest(value.encode("utf-8", "surrogatepass"))[:16]
            for name, value in os.environ.items() if name not in _SESSION_VARIABLES}


def _config_bytes(root: Path) -> bytes:
    config = root / "crapkit.toml"
    return config.read_bytes() if config.is_file() else b""


def _worktree(root: Path, outputs: frozenset[str]) -> tuple[str, list[str]]:
    """HEAD, and every uncommitted change in the whole checkout but `outputs`,
    named from the checkout's top as git names them."""
    top = worktree_root(root)
    facts = GitFacts(top)
    skip = _from_top(root, top, outputs)
    return facts.head_commit(), _unless(facts.status_names(), skip)


def uncommitted_changes(root: Path) -> list[str]:
    """The changes that keep every lane without `inputs` from reuse: each
    uncommitted change in the checkout but the lane outputs crapkit.toml
    declares. Empty when git cannot say."""
    try:
        return _worktree(root, _configured_outputs(_config_bytes(root)))[1]
    except (GitError, OSError):
        return []


def _unless(names, skip: frozenset[str]) -> list[str]:
    return [name for name in names if name not in skip]


def _from_top(root: Path, top: Path, names: frozenset[str]) -> frozenset[str]:
    """Root-relative `names` spelled from the checkout's top."""
    try:
        prefix = root.resolve().relative_to(top).as_posix()
    except ValueError:
        return frozenset()
    return frozenset(posixpath.normpath(f"{prefix}/{name}") for name in names)


def _output_names(root: Path, lane: Lane, config: bytes | None = None) -> frozenset[str]:
    """Every declared artifact and results file of the lanes crapkit.toml
    configures, plus this lane's own, as root-relative paths."""
    text = _config_bytes(root) if config is None else config
    own = frozenset(_normalized(name) for name in _declared_files(lane))
    return own | _configured_outputs(text)


@lru_cache(maxsize=4)
def _configured_outputs(config: bytes) -> frozenset[str]:
    from .config import load_config_text
    from .repotext import repo_bytes_text

    try:
        lanes = load_config_text(repo_bytes_text(config, "crapkit.toml")).lanes
    except (CrapkitError, ValueError):
        return frozenset()
    return frozenset(_normalized(name) for lane in lanes for name in _declared_files(lane))


def _normalized(name: str) -> str:
    return posixpath.normpath(name.replace("\\", "/"))


def write_stamps(root: Path, entries: dict[str, dict]) -> None:
    """Merge this run's artifact stamps into .crapkit/artifacts.json in one write.

    Dying before this point loses stamps but never fabricates one, so the worst
    a crash costs is a rerun of lanes that could have been reused.
    """
    fresh = {artifact: entry for artifact, entry in entries.items() if entry}
    if not fresh:
        return
    path = _stamps_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**read_stamps(root), **fresh}, sort_keys=True, indent=1),
                    encoding="utf-8")


def stamp_for(stamps: dict, artifact: str) -> dict:
    """One artifact's entry, or {} when the file records none, or something
    hand-mangled under that key."""
    entry = stamps.get(artifact)
    return entry if isinstance(entry, dict) else {}


def unreadable_stamps(stamps: dict) -> list[str]:
    """The keys whose entry is not an object, sorted. Every reader here takes
    such an entry as no stamp at all; this is what lets doctor name them."""
    return sorted(key for key, entry in stamps.items() if not isinstance(entry, dict))


def _stamp_commit(entry: dict) -> str:
    """The commit a stamp records, or "" for an entry that holds only a refusal
    (a lane whose every attempt so far failed) or nothing usable."""
    commit = entry.get("commit")
    return commit if isinstance(commit, str) else ""


def _refused_on_disk(entry: dict, path: Path) -> bool:
    """Is the file at `path` the one the last attempt failed to write? Only
    while its modification time still equals the one the refusal recorded: a
    salvage combined by hand or a real run rewrites the file and moves it."""
    refused = entry.get("refused_mtime_ns")
    return refused is not None and refused == _mtime_ns(path)


def refusal_stamp(root: Path, lane: Lane, error: object) -> dict[str, dict]:
    """The stamp that records the lane's artifact as the one its last attempt
    failed to write, keyed by artifact path like every stamp, or {} when
    `error` carries no such refusal.

    The refusal is the one `_run_attempts` raised, not a second reading of the
    disk: the attempt already knew which file it left unwritten and what time
    that file carried. A lane refused before it ran (the container guard) and a
    lane that failed after rewriting its artifact (a file that does not parse,
    one from another tree) raise something else and record nothing, which
    keeps `--reuse-artifacts` the way through the guard and leaves reuse to
    judge the rewritten file on its own terms. A refusal about the results
    artifact alone records nothing either: the stamp is the coverage file's.

    The previous stamp's fields ride along. Its commit is still where the file
    on disk was built, and its duration still orders parallel starts.
    """
    refused = getattr(error, "refused_mtimes", {}).get(lane.artifact)
    if refused is None:
        return {}
    entry = stamp_for(read_stamps(root), lane.artifact)
    return {lane.artifact: {**entry, "lane": lane.name, "refused_mtime_ns": refused}}


def _recorded_seconds(entry: object) -> float | None:
    """The duration one stamp recorded, or None when it has none to give."""
    value = entry.get("seconds") if isinstance(entry, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def _is_lane(entry: object, name: str) -> bool:
    return isinstance(entry, dict) and entry.get("lane") == name


def _named_seconds(stamps: dict, name: str) -> float | None:
    """The longest run any stamp recorded under this lane's NAME, or None when
    no stamp names it.

    Stamps are filed under the artifact PATH, so moving an artifact orphans its
    duration and the lane sorts as never-measured: the consumer repo renamed 12 of them
    and 11 of its 14 lanes read as zero seconds, which makes longest-first
    scheduling do nothing at all. Every stamp already names the lane that wrote
    it, so the name finds the record the path lost. Longest wins because the
    schedule only ever needs an upper bound on how long the lane can run.

    Durations only. Reuse and staleness still key on the exact artifact path:
    judging a NEW artifact's freshness by an OLD one's commit would hand back a
    stale coverage number, and a start order can never do that.
    """
    named = (_recorded_seconds(entry) for entry in stamps.values() if _is_lane(entry, name))
    return max((seconds for seconds in named if seconds is not None), default=None)


def recorded_seconds(stamps: dict, lane: Lane) -> float | None:
    """How long this lane took the last time it actually ran, or None when no
    stamp records it. The declared artifact is the exact record; the lane name
    is the fallback that survives a rename. The start order and doctor --tune
    both read this, so the two cannot disagree about a renamed artifact."""
    exact = _recorded_seconds(stamps.get(lane.artifact))
    return exact if exact is not None else _named_seconds(stamps, lane.name)


def lane_order(root: Path, lanes: list[Lane]) -> list[Lane]:
    """Longest recorded lane first: with lanes running concurrently the makespan
    is the slowest lane, so starting it last wastes exactly its own duration.
    Sorting is stable, so unrecorded lanes and ties keep declaration order.
    A recorded duration only ever changes WHICH lane starts first — results are
    merged in declaration order regardless, so it cannot move a score."""
    stamps = read_stamps(root)
    return sorted(lanes, key=lambda lane: -(recorded_seconds(stamps, lane) or 0.0))


def _lane_matchers(lane: Lane, scope_paths: dict) -> tuple[ScopeMatch, ...]:
    """Ownership over the scopes THIS lane names, read by universe's one predicate.

    Both questions a lane asks about paths go through here: which of its scopes'
    files moved since the artifact was stamped, and whether the artifact reached
    any of them at all. A second hand-rolled prefix test would answer the exact
    arm — a scope that declares a FILE rather than a directory — differently
    from the rule that assigns files, and the two would drift.
    """
    return path_matchers({name: scope_paths.get(name, ()) for name in lane.scopes})


def _scope_changes(git: GitFacts, lane: Lane, scope_paths: dict, since_commit: str) -> list[str]:
    """Committed or working-tree changes under this lane's scope paths since a commit.

    Ownership is universe's, asked over this lane's scopes alone so a file a
    NESTED scope owns cannot go stale on the parent's lane. Prefix matching on
    its own missed a scope that declares a FILE rather than a directory — the
    shape crapkit's own tests/e2e/test_parallel_lanes_e2e.py writes — and
    editing that file read as no change at all.
    """
    matchers = _lane_matchers(lane, scope_paths)
    if not matchers:
        return []
    changed = set(git.diff_names_since(since_commit)) | set(git.status_names())
    return sorted(f for f in changed if owning_scope(f, matchers))


def _warn_stale_artifact(git: GitFacts, lane: Lane, scope_paths: dict | None) -> None:
    """On reuse: say when the artifact predates changes touching this lane's scopes.
    Uncommitted working-tree edits count — that is the most common way to go stale."""
    commit = _stamp_commit(stamp_for(read_stamps(git.root), lane.artifact))
    if not commit or not scope_paths:
        return
    try:
        changed = _scope_changes(git, lane, scope_paths, commit)
    except GitError:
        return
    if changed:
        print(f"crapkit: lane {lane.name!r} artifact was built at {commit[:11]}; "
              f"{len(changed)} file(s) in its scopes changed since (their coverage is stale)",
              file=sys.stderr)


def _facts(root: Path, git: GitFacts | None) -> GitFacts:
    """A whole command shares one context; a lone caller gets its own."""
    return git if git is not None else GitFacts(root)


def _artifact_commit(root: Path, lane: Lane) -> str:
    """The recorded commit of an existing artifact with no pending write refusal."""
    stamp = stamp_for(read_stamps(root), lane.artifact)
    path = root / lane.artifact
    if not path.is_file() or _refused_on_disk(stamp, path):
        return ""
    return _stamp_commit(stamp)


def lane_sources_unchanged(root: Path, lane: Lane, scope_paths: dict,
                           git: GitFacts | None = None) -> bool:
    """Whether source edits made this artifact's line locations stale.

    Tests, runner settings and environment changes require a new measurement
    but leave source locations intact. This read-side check accepts legacy
    commit stamps and shares Git facts across lanes; it cannot authorize reuse.
    """
    commit = _artifact_commit(root, lane)
    if not commit:
        return False
    facts = _facts(root, git)
    try:
        return facts.is_ancestor(commit) and not _scope_changes(facts, lane, scope_paths, commit)
    except GitError:
        return False


def staleness_reads(root: Path, lanes, scope_paths: dict, git=None):
    """The git answers lane staleness reads, for every lane of one command.

    A caller's own facts are used as they stand. Without them every read starts
    at once: the ancestry and commit-range reads for each stamp commit on disk,
    and diff and ls-files narrowed to every lane's scope paths. The pathspec
    names lanes with no stamp yet too, because a concurrent `crapkit coverage`
    can stamp one between these reads and its verdict. With no stamp on disk at
    all, git is asked only if a lane gets one, and then about the whole tree.
    """
    if git is not None:
        return nullcontext(git)
    commits = tuple(dict.fromkeys(_stamped_commits(root, lanes)))
    if not commits:
        return nullcontext(GitFacts(root))
    paths = dict.fromkeys(path for lane in lanes for path in _declared_paths(lane, scope_paths))
    return _started_reads(root, commits, tuple(paths))


def _stamped_commits(root: Path, lanes) -> list[str]:
    """The stamp commit of each lane whose artifact only git can judge."""
    return [commit for commit in (_artifact_commit(root, lane) for lane in lanes) if commit]


def _declared_paths(lane: Lane, scope_paths: dict) -> tuple[str, ...]:
    """The paths this lane's scopes declare, as written in the config."""
    return tuple(path for name in lane.scopes for path in scope_paths.get(name, ()))


def _started_reads(root: Path, commits: tuple, paths: tuple):
    """ChangeReads, or lazy GitFacts when git cannot even start: each question
    then raises GitError and the lane reads stale, as it always has."""
    from .lane_changes import ChangeReads

    try:
        return ChangeReads(root, commits, paths)
    except GitError:
        return nullcontext(GitFacts(root))


def lane_reuse_commit(root: Path, lane: Lane) -> str:
    """The commit the lane's artifact was built at when automatic reuse is
    proved, or "". `lane_reuse_verdict` says why not."""
    return lane_reuse_verdict(root, lane).commit


class ReuseVerdict(NamedTuple):
    """`commit` is the commit the lane's artifact was built at when reuse is
    proved, else "", and then `reason` says why the lane reruns."""
    commit: str
    reason: str


def lane_reuse_verdict(root: Path, lane: Lane) -> ReuseVerdict:
    """Whether automatic reuse is proved for this lane, and the first condition
    that fails when it is not.

    A lane command can read tests, configuration or any other repository input,
    and source ownership cannot prove that a changed path leaves its
    measurement intact. So a lane that declares nothing is reused only at the
    same clean repository. A lane that declares `inputs` is reused while its
    commit is behind HEAD and no committed, staged, unstaged or untracked
    change touches those paths. Either way the stamp's `proof` has to equal the
    key the lane would stamp now and the artifact bytes have to match the stamp.
    A stamp without a `proof` (one written before it had that name, or measured
    while it did not hold) is never reused automatically. Explicit artifact
    reuse remains a separate deliberate request.
    """
    stamp = stamp_for(read_stamps(root), lane.artifact)
    commit = _artifact_commit(root, lane)
    reason = (_stamp_gap(root, lane, stamp, commit) or _proof_gap(root, lane, stamp, commit)
              or _artifact_gap(root, lane, stamp))
    return ReuseVerdict("" if reason else commit, reason)


def _stamp_gap(root: Path, lane: Lane, stamp: dict, commit: str) -> str:
    """Why the stamp itself cannot vouch for the artifact, or ""."""
    if not commit:
        return _no_commit(root, lane, stamp)
    if not stamp.get("proof"):
        return ("its stamp holds no proof: it was measured with uncommitted changes, or by "
                "a crapkit that recorded none")
    return ""


def _no_commit(root: Path, lane: Lane, stamp: dict) -> str:
    path = root / lane.artifact
    if not path.is_file():
        return f"no artifact at {lane.artifact}"
    if _refused_on_disk(stamp, path):
        return f"its last attempt wrote no artifact, and the {lane.artifact} on disk predates it"
    return f"no stamp records the commit {lane.artifact} was built at"


def _proof_gap(root: Path, lane: Lane, stamp: dict, commit: str) -> str:
    if lane.inputs:
        return _inputs_gap(root, lane, stamp["proof"], commit)
    return _whole_tree_gap(root, lane, stamp, commit)


def _inputs_gap(root: Path, lane: Lane, proof: str, commit: str) -> str:
    """The stamp's commit is still behind HEAD and nothing under the inputs moved.

    git reads the inputs as a pathspec, so an untracked file outside them
    costs nothing and blocks nothing."""
    if proof != _inputs_key(commit, lane):
        return "its lane table or env differs from the one it was measured with"
    return _inputs_moved(root, lane, commit)


def _inputs_moved(root: Path, lane: Lane, commit: str) -> str:
    from .lane_changes import ChangeReads

    try:
        outputs = _output_names(root, lane)
        with ChangeReads(root, (commit,), lane.inputs) as reads:
            behind = reads.is_ancestor(commit)
            moved = _unless(reads.changed_since(commit), outputs) if behind else []
    except (GitError, OSError) as exc:
        return f"nothing proves its inputs unchanged: {exc}"
    if not behind:
        return f"its artifact was built at {commit[:11]}, which is not behind HEAD"
    return f"{len(moved)} change(s) under its inputs since {commit[:11]}: {_sample(moved)}" if moved else ""


def _whole_tree_gap(root: Path, lane: Lane, stamp: dict, commit: str) -> str:
    proof = _whole_tree_proof(root, lane)
    if proof.why:
        return proof.why
    if proof.parts["commit"] != commit:
        return f"HEAD is {proof.parts['commit'][:11]} and its artifact was built at {commit[:11]}"
    if proof.key == stamp["proof"]:
        return ""
    return _moved_parts(proof.parts, stamp.get("proof_parts"))


_PART_NAMES = (("config", "crapkit.toml"), ("lane", "its lane table"))
_UNNAMED_PART = ("crapkit.toml, its lane table or the environment changed, and its stamp "
                 "does not record which")


def _moved_parts(now: dict, then) -> str:
    """Which recorded part of a whole-tree proof moved, named."""
    moved = _moved_names(now, then) if isinstance(then, dict) else []
    return "; ".join(moved) if moved else _UNNAMED_PART


def _moved_names(now: dict, then: dict) -> list[str]:
    moved = [f"{name} changed" for key, name in _PART_NAMES if now[key] != then.get(key)]
    env = _environment_moved(now["env"], then.get("env"))
    return moved + ([f"{len(env)} environment variable(s) changed: {_sample(env)}"] if env else [])


def _environment_moved(now: dict, then) -> list[str]:
    then = then if isinstance(then, dict) else {}
    return sorted(name for name in now.keys() | then.keys() if now.get(name) != then.get(name))


def _artifact_gap(root: Path, lane: Lane, stamp: dict) -> str:
    expected = stamp.get("artifacts")
    if not isinstance(expected, dict):
        return "its stamp records no digest of its artifact"
    moved = [name for name in _declared_files(lane) if _file_digest(root / name) != expected.get(name)]
    return f"{_sample(moved)}: bytes differ from its stamp" if moved else ""


def _file_digest(path: Path) -> str:
    try:
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()
    except OSError:
        return ""


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
        dead_lines.add(artifact_path, dead)
    return per_file, digest


_SAMPLE_PATHS = 3

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


def _resolved(path: str) -> str:
    """One spelling, so both sides of the root comparison can be compared at
    all: symlinks followed, separators normalized, and the case folded where the
    filesystem folds it (`normcase` is identity on POSIX, which does not).

    A path whose tail does not exist still normalizes; only a name the platform
    cannot express at all raises, and that is answered as written."""
    try:
        resolved = str(Path(path).resolve())
    except (OSError, ValueError):
        return os.path.normcase(path)
    return os.path.normcase(resolved)


def _under(root: str, path: str) -> bool:
    """Both already `_resolved`. The root itself counts as under itself."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _lands_in_checkout(root: str, path: str) -> bool:
    """An absolute path naming a file this checkout holds after all.

    `../` is excluded on purpose: it is relative to the runner's working
    directory, which the artifact never records, so there is nothing to resolve
    it against and no honest way to place it."""
    return _is_absolute(path) and _under(root, _resolved(path))


def _unreached_paths(lane: Lane, coverage: dict, scope_paths: dict) -> tuple[str, ...]:
    """The paths this lane's scopes declare when NOTHING the artifact measured
    reaches any of them, else (). Empty too when the lane's scopes declare no
    path at all: nothing to compare against is not evidence.

    `owning_scope` is the reach test, so "could a scope claim this path?" can
    never drift from the rule that assigns files to scopes.

    The DECLARED path, not the matcher's directory prefix: a scope may name an
    individual file, and the prefix half of that is `src/faro/core.py/`, a path
    that exists neither in the config the reader is about to open nor on disk.
    """
    matchers = _lane_matchers(lane, scope_paths)
    if not matchers or any(owning_scope(path, matchers) for path in coverage):
        return ()
    return tuple(dict.fromkeys(m.path for m in matchers))


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

    The root resolves once, and every path resolves the same way, or a symlinked
    or short-name checkout compares unequal to its own files."""
    resolved_root = _resolved(str(root))
    elsewhere: list[str] = []
    inside: list[str] = []
    for path in escaped:
        (inside if _lands_in_checkout(resolved_root, path) else elsewhere).append(path)
    return elsewhere, inside


def _sample(paths) -> str:
    """A few of them and a count of the rest. A lane scoped to forty declared
    paths listed all forty, which pushed the sentence saying what to do off the
    end of a line nobody reads that far into."""
    ordered = sorted(paths)
    shown = ", ".join(ordered[:_SAMPLE_PATHS])
    rest = len(ordered) - _SAMPLE_PATHS
    return f"{shown} and {rest} more" if rest > 0 else shown


def _zero_overlap(lane: Lane, coverage: dict, declared) -> str:
    """The finding both verdicts open on, written once: what the artifact
    measured, and that none of it is in scope. The two messages part company
    after it, and a sentence kept in two places is a sentence that drifts."""
    return (f"lane {lane.name!r} measured {len(coverage)} file(s), none of them under the "
            f"paths its scopes declare ({_sample(declared)})")


def _wrong_tree_message(lane: Lane, coverage: dict, declared, outside: list[str]) -> str:
    return (f"{_zero_overlap(lane, coverage, declared)}, and {len(outside)} of them "
            f"outside this checkout entirely — {lane.artifact} describes a different tree, "
            f"so joining it would score every function in those scopes untested; it reports "
            f"paths like {_sample(outside)}. {lane_format(lane).WRONG_TREE_FIX}")


def _absolute_message(lane: Lane, coverage: dict, declared, inside: list[str]) -> str:
    return (f"{_zero_overlap(lane, coverage, declared)}, and {len(inside)} of them written "
            f"as absolute paths that DO sit under this checkout — {lane.artifact} measured "
            f"this tree and spelled it absolutely, and the join is on root-relative paths, "
            f"so it still matches nothing and every function in those scopes would score "
            f"untested; it reports paths like {_sample(inside)}. {lane_format(lane).ABSOLUTE_FIX}")


def _unmeasured_message(lane: Lane, coverage: dict, declared) -> str:
    reports = f"; it measured {_sample(coverage)}" if coverage else ""
    return (f"{_zero_overlap(lane, coverage, declared)}, so every function in those "
            f"scopes will score untested{reports} — either nothing in them is exercised yet, "
            f"{lane_format(lane).UNMEASURED_READING}")


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
    declared = _unreached_paths(lane, coverage, scope_paths or {})
    if not declared:
        return
    elsewhere, inside = _split_escaped(root, _escaped_paths(lane, coverage))
    if elsewhere:
        raise ToolError(_wrong_tree_message(lane, coverage, declared, elsewhere))
    if inside:
        raise ToolError(_absolute_message(lane, coverage, declared, inside))
    print(f"crapkit: {_unmeasured_message(lane, coverage, declared)}", file=sys.stderr)


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
    failed, counts = suite_summary(raw.decode("utf-8"))
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


SUITE_DROP_FRACTION = 0.1


def _tests_total(prov: dict) -> int:
    return prov.get("tests_total") or 0


def suite_drops(previous: dict, current: dict, *,
                fraction: float = SUITE_DROP_FRACTION) -> list[str]:
    """Lanes whose junit counted far fewer tests than the last trusted run's.

    The cheap half of the crashed-worker check, for the runner that dies without
    writing the crash into its own report: then the count is the only signature
    left. The lane this came from wrote 10,674 of 15,300 collected tests after
    one xdist worker died, and reported success.

    A tenth is wide enough that deleting a test file does not cry wolf, and
    narrow enough that a dead worker's whole queue cannot hide under it. Both
    arguments are lane-name -> provenance, the shape a run records.
    """
    notes = []
    for name, prov in sorted(current.items()):
        before, now = _tests_total(previous.get(name, {})), _tests_total(prov)
        if before and now < before * (1 - fraction):
            notes.append(f"lane {name!r} ran {now} tests, {before - now} fewer than the "
                         f"last trusted run's {before} — check the runner's log for a "
                         "worker that died without reporting it")
    return notes


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
    before = _mtime_ns(root / lane.results_artifact)
    with command_log(log_path, max_bytes=lane.log_max_bytes, append=True) as fh:
        fh.write(f"\n--- flake retest ---\n$ {command}\n")
        fh.flush()
        try:
            code = run_bounded(command, _deadline(lane), stream=fh,
                               no_progress=_no_progress(lane), owner=owner, **kwargs)
        except NoProgress:
            return set()
    if code not in (0, 1):
        return set()
    return tests & _retested_passes(root, lane, before)


def _retest_template(template: str, tests: set[str]) -> tuple[str, dict[str, str]]:
    from .procs import prepare_template

    ordered = sorted(tests)
    files = sorted({test.split("::", 1)[0] for test in ordered})
    names = "|".join(re.escape(test.split("::", 1)[1]) for test in ordered if "::" in test)
    return prepare_template(template, {"tests": ordered, "files": files, "names": [names]})


def _retested_passes(root: Path, lane: Lane, before: int | None) -> set[str]:
    from .junitparse import passed_test_ids

    path = root / lane.results_artifact
    if _mtime_ns(path) == before:
        return set()
    try:
        return passed_test_ids(path.read_text(encoding="utf-8"))
    except (OSError, ToolError):
        return set()


def _still_failed(root: Path, lane: Lane) -> set[str] | None:
    """The failing ids in the lane's results artifact; None means unreadable."""
    from .junitparse import failed_test_ids

    results_path = root / lane.results_artifact
    if not results_path.is_file():
        return None
    try:
        return failed_test_ids(results_path.read_text(encoding="utf-8"))
    except ToolError:
        return None


class LaneOutcome(NamedTuple):
    """One lane's result. `stamp` is what the caller must persist (empty when the
    lane reused an artifact, or when there is no git repo to stamp against)."""
    coverage: dict[str, list[FnCoverage]]
    provenance: dict
    stamp: dict


def _run_or_reuse(root: Path, lane: Lane, git: GitFacts, scope_paths: dict | None,
                  reuse_artifact: bool, owner=None) -> tuple[int | None, float]:
    """Reuse warns and costs nothing; a real run returns its exit code and wall seconds.

    The container guard belongs on this side of the branch. It names an OOM a
    python suite hits under a container memory cap, and `--reuse-artifacts`
    launches no suite: it parses a file already on disk, which costs no memory
    the guard is about. Sitting a line above this call, it refused the crapkit
    image reading host-built artifacts, and its message said something untrue
    about what the lane was going to do.
    """
    if reuse_artifact:
        _refuse_unwritten_artifact(root, lane)
        _warn_stale_artifact(git, lane, scope_paths)
        return None, 0.0
    _refuse_container_python(lane)
    started = time.monotonic()
    return _run_attempts(root, lane, owner), time.monotonic() - started


def _refuse_unwritten_artifact(root: Path, lane: Lane) -> None:
    """On reuse: the refusal the failed attempt raised, again, while the file
    on disk is still the one that attempt left. Reuse read that file back and
    scored it, so a dead lane's old numbers became the trusted baseline. A file
    that is gone falls through to `_artifact_path`, whose sentence is the one
    the recover skill triages on."""
    path = root / lane.artifact
    stamp = stamp_for(read_stamps(root), lane.artifact)
    if path.is_file() and _refused_on_disk(stamp, path):
        _raise_no_artifact(root, lane, _lane_log_path(root, lane), None,
                           {lane.artifact: stamp["refused_mtime_ns"]}, reuse=True)


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
             dead_lines=None, owner=None) -> LaneOutcome:
    ownership = nullcontext(owner) if owner is not None else measurement_owner(root, (lane,))
    with ownership as held:
        return _run_owned_lane(root, lane, reuse_artifact, scope_paths, git, dead_lines, held)


def _run_owned_lane(root, lane, reuse_artifact, scope_paths, git, dead_lines, owner) -> LaneOutcome:
    facts = _facts(root, git)
    measured = "" if reuse_artifact else _measurement_key(root, lane)
    exit_code, seconds = _run_or_reuse(root, lane, facts, scope_paths, reuse_artifact, owner)
    coverage, digest = _read_and_parse(lane, root, _artifact_path(root, lane), dead_lines)
    _judge_artifact_scope(lane, coverage, scope_paths, root)
    provenance = {
        "artifact_sha256": digest,
        "exit_code": exit_code,
        "parser": lane.parser,
        "scopes": list(lane.scopes),
    }
    if lane.results_artifact:
        provenance.update(_results_provenance(root, lane, reuse_artifact=reuse_artifact))
    stamp = {} if reuse_artifact else _stamp_entry(facts, lane, seconds, measured, provenance)
    return LaneOutcome(coverage, provenance, stamp)


def _output_lock(path: Path) -> Path:
    """Coordinate local outputs outside directories their runners may replace."""
    key = os.path.normcase(str(path.resolve())).encode("utf-8")
    host = hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:16]
    directory = Path.home() / ".cache" / "crapkit" / "measurements" / host
    return directory / ("measurement-" + hashlib.sha256(key).hexdigest() + ".lock")


def measurement_owner(root: Path, lanes):
    """Own resolved outputs, plus this checkout's shared log and stamp state."""
    if not lanes:
        return nullcontext(None)
    outputs = {root / name for lane in lanes for name in _declared_files(lane)}
    paths = {_output_lock(path) for path in outputs}
    paths.add(root / ".crapkit" / "measurement.lock")
    return own_processes(sorted(paths))
