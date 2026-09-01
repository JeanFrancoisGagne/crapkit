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

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import IO, NamedTuple

from .config import Lane, shell_words
from .coverage_istanbul import FnCoverage
from .covstream import lane_prefix, parse_coveragepy_file, parse_istanbul_both_file
from .errors import GitError, ToolError
from .gitio import GitFacts
from .procs import NoProgress, run_bounded
from .uncovered import fold_dead_lines
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


def _popen_kwargs(root: Path, lane: Lane) -> dict:
    return {
        "cwd": root / lane.cwd if lane.cwd else root,
        "env": {**os.environ, **dict(lane.env)} if lane.env else None,
    }


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


def _stream_command(root: Path, lane: Lane, log_path: Path, attempt: int) -> int:
    with open(log_path, "a" if attempt > 1 else "w", encoding="utf-8", errors="replace") as fh:
        _log_header(fh, lane.command, attempt)
        # The log is a file, not a pipe, so streaming costs the deadline nothing:
        # run_bounded kills the shell's whole tree, which is where the suite is.
        # It is also what the progress watch measures.
        try:
            code = run_bounded(lane.command, _deadline(lane), stream=fh,
                               no_progress=_no_progress(lane), **_popen_kwargs(root, lane))
        except NoProgress as stalled:
            _raise_stalled(fh, lane, log_path, attempt, stalled.seconds)
        if code is None:
            fh.write(f"\n[crapkit] timed out after {lane.timeout_seconds}s; killed\n")
            raise ToolError(f"lane {lane.name!r} timed out after {lane.timeout_seconds}s "
                            f"(attempt {attempt}); log: {log_path}")
        fh.write(f"\n(exit {code})\n")
    return code


def _attempt_once(root: Path, lane: Lane, log_path: Path, attempt: int) -> int | None:
    """One attempt; None means it timed out but another attempt remains."""
    try:
        return _stream_command(root, lane, log_path, attempt)
    except ToolError:
        if attempt > lane.retries:
            raise
        return None


def _lane_runner(lane: Lane) -> str:
    """The word this lane's command starts with, read by the shell that runs it.

    `shell_words`, never a whitespace split: a quoted interpreter path breaks at
    its space and half of `C:\\Program Files\\py\\python.exe` is not a program.
    """
    words = shell_words(lane.command)
    return words[0] if words else lane.command


def _missing_plugin_hint(tail: str, lane: Lane) -> str:
    """The one failure signature a new user cannot decode: pytest rejecting
    --cov points at crapkit's config when the real gap is the pytest-cov package.

    Which environment it is missing from is the other half of the hint, and it
    used to name none. A repo whose lane runs its own venv got `pip install
    pytest-cov`, which lands in whatever venv the reader's shell has active; the
    reporter ran it verbatim, it installed fine, and the next `crapkit coverage`
    failed identically. Same sentence `init` prints for the same gap, so a
    reader who has seen one recognises the other.
    """
    if "unrecognized arguments" not in tail or "--cov" not in tail:
        return ""
    word = _lane_runner(lane)
    return (f" — the --cov flags come from the pytest-cov package, which has to be "
            f"installed in the environment `{word}` runs in (`{word} -m pip install "
            f"pytest-cov`), not in the shell's active venv")


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
    shard_dir = root / lane.cwd if lane.cwd else root
    shards = sorted(shard_dir.glob(".coverage.*"))
    if not shards:
        return ""
    target = Path(os.path.relpath(root / lane.artifact, shard_dir)).as_posix()
    noun, verb = ("shard", "sits") if len(shards) == 1 else ("shards", "sit")
    return (f"; {len(shards)} coverage {noun} ({shards[0].name}, ...) {verb} in "
            f"{shard_dir}, which is what a killed parallel run leaves behind: "
            f"`coverage combine && coverage json -o {target}` there, then a "
            "re-run with --reuse-artifacts, scores what that suite did measure")


def _no_artifact_head(lane: Lane, stale: list[str]) -> str:
    """What the lane failed to do, in the words the disk supports.

    An empty `.crapkit/` and a leftover file are the same failure — this run
    wrote nothing — and they read completely differently to whoever has to fix
    it. Told "produced no artifact at .crapkit/cov/py.json" about a path that
    holds a report, a reader concludes crapkit cannot see the file, so the
    leftover case says which run the file belongs to instead.
    """
    if not stale:
        return f"produced no artifact at {lane.artifact}"
    return (f"wrote no artifact this run — the {', '.join(stale)} on disk predates it "
            f"and is the previous run's")


def _raise_no_artifact(root: Path, lane: Lane, log_path: Path, exit_code: int | None,
                       stale: list[str] | None = None) -> None:
    """The log PATH before the log's words. A tail is 500 characters of a file
    that holds the whole story, and a reader who is not told where that file is
    has to go looking for it — one reporter had to ask another agent to find
    .crapkit/lane-py.log while ten collection tracebacks sat inside it."""
    detail = f" (command exit {exit_code})" if exit_code is not None else ""
    tail = _log_tail(log_path)
    hint = f"; last output: {tail}" if tail else ""
    raise ToolError(f"lane {lane.name!r} {_no_artifact_head(lane, stale or [])}{detail}"
                    f"; full log: {log_path}{hint}{_missing_plugin_hint(tail, lane)}"
                    f"{_shard_hint(root, lane)}")


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


def _unwritten(root: Path, before: dict[str, int | None]) -> list[str]:
    """The declared files that were already on disk and that this attempt did
    not rewrite.

    Existence was the whole check until 0.4.12, so a lane failed loud exactly
    once — on the first run, against an empty `.crapkit/` — and scored the
    PREVIOUS run's file on every run after that. A vitest lane without
    reportOnFailure and a pytest run that dies in collection both land here, and
    what comes out is a confident grade off a measurement nothing took.

    A file that is not there at all is left out: that is the refusal crapkit
    already had, and a missing results_artifact has its own sentence one layer
    up. mtime, not content, because a runner that rewrites a byte-identical
    report still bumps it, so an unchanged rerun stays green.
    """
    return [name for name, stamp in before.items()
            if stamp is not None and _mtime_ns(root / name) == stamp]


def _run_attempts(root: Path, lane: Lane) -> int | None:
    log_path = _lane_log_path(root, lane)
    exit_code: int | None = None
    stale: list[str] = []
    for attempt in range(1, lane.retries + 2):
        before = _declared_mtimes(root, lane)
        exit_code = _attempt_once(root, lane, log_path, attempt)
        stale = _unwritten(root, before)
        if exit_code is not None and not stale and (root / lane.artifact).is_file():
            return exit_code
    _raise_no_artifact(root, lane, log_path, exit_code, stale)
    return None  # unreachable; keeps the signature honest


def _stamps_path(root: Path) -> Path:
    return root / ".crapkit" / "artifacts.json"


def read_stamps(root: Path) -> dict:
    """The recorded artifact stamps: {artifact path: {commit, lane, seconds}}.
    A missing or hand-mangled file reads as no stamps at all."""
    path = _stamps_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _stamp_entry(git: GitFacts, lane: Lane, seconds: float) -> dict:
    """What produced this artifact: the commit reuse judges staleness against,
    plus the wall seconds the parallel scheduler starts the slowest lane on.

    Empty in a non-git sandbox (unit tests), which records nothing. Lanes hand
    their stamp back rather than writing it, so N of them running at once cannot
    lose each other's entry through a read-modify-write of one shared file.
    """
    try:
        commit = git.head_commit()
    except GitError:
        return {}
    return {"commit": commit, "lane": lane.name, "seconds": round(seconds, 1)}


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


def _recorded_seconds(entry: object) -> float | None:
    """The duration one stamp recorded, or None when it has none to give."""
    value = entry.get("seconds") if isinstance(entry, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def _is_lane(entry: object, name: str) -> bool:
    return isinstance(entry, dict) and entry.get("lane") == name


def _named_seconds(stamps: dict, name: str) -> float:
    """The longest run any stamp recorded under this lane's NAME.

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
    return max((seconds for seconds in named if seconds is not None), default=0.0)


def _stamp_seconds(stamps: dict, lane: Lane) -> float:
    """How long this lane took last time it actually ran; 0 when never recorded.
    The declared artifact is the exact record; the lane name is the fallback that
    survives a rename."""
    exact = _recorded_seconds(stamps.get(lane.artifact))
    return exact if exact is not None else _named_seconds(stamps, lane.name)


def lane_order(root: Path, lanes: list[Lane]) -> list[Lane]:
    """Longest recorded lane first: with lanes running concurrently the makespan
    is the slowest lane, so starting it last wastes exactly its own duration.
    Sorting is stable, so unrecorded lanes and ties keep declaration order.
    A recorded duration only ever changes WHICH lane starts first — results are
    merged in declaration order regardless, so it cannot move a score."""
    stamps = read_stamps(root)
    return sorted(lanes, key=lambda lane: -_stamp_seconds(stamps, lane))


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
    stamp = read_stamps(git.root).get(lane.artifact)
    if not stamp or not scope_paths:
        return
    try:
        changed = _scope_changes(git, lane, scope_paths, stamp["commit"])
    except GitError:
        return
    if changed:
        print(f"crapkit: lane {lane.name!r} artifact was built at {stamp['commit'][:11]}; "
              f"{len(changed)} file(s) in its scopes changed since (their coverage is stale)",
              file=sys.stderr)


def _facts(root: Path, git: GitFacts | None) -> GitFacts:
    """A whole command shares one context; a lone caller gets its own."""
    return git if git is not None else GitFacts(root)


def lane_unchanged(root: Path, lane: Lane, scope_paths: dict, git: GitFacts | None = None) -> bool:
    """True when the recorded artifact still describes this lane's scopes exactly:
    the stamp commit reached HEAD and nothing under the scopes moved since."""
    facts = _facts(root, git)
    stamp = read_stamps(root).get(lane.artifact)
    if not stamp or not (root / lane.artifact).is_file():
        return False
    try:
        if not facts.is_ancestor(stamp["commit"]):
            return False
        return not _scope_changes(facts, lane, scope_paths, stamp["commit"])
    except GitError:
        return False


def _read_and_parse(lane: Lane, root: Path,
                    artifact_path: Path) -> tuple[dict[str, list[FnCoverage]], str]:
    """This lane's coverage, plus the sha256 of the artifact's own bytes.

    The reader takes the PATH, not the text: a whole-document parse needs the
    bytes and their UTF-8 decode both live before the first function is
    attributed, which is two copies of an artifact that runs to hundreds of MB.
    Streaming holds one chunk and one file's coverage instead, and hashes the
    bytes on the way past, so the recorded digest costs no second read.

    An istanbul walk also yields the lines no statement ran, which diff coverage
    used to get by reopening the artifact and decoding every member a second
    time. They are folded into the run's union here and dropped, so no lane's
    map has to stay alive next to the others'.
    """
    if lane.parser == "istanbul":
        per_file, dead, digest = parse_istanbul_both_file(artifact_path, repo_root=str(root))
        fold_dead_lines(artifact_path, dead)
        return per_file, digest
    if lane.parser == "coveragepy":
        return parse_coveragepy_file(artifact_path, path_prefix=lane.path_prefix)
    raise ToolError(f"lane {lane.name!r}: parser {lane.parser!r} not implemented yet")


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


def _as_reported(lane: Lane, path: str) -> str:
    """One coverage key with this lane's own `path_prefix` taken back off, which
    is the path the runner actually wrote.

    The coveragepy reader prepends the prefix to EVERY key, an absolute one
    included, so `backend/` + `/other/checkout/a.py` starts with neither `/` nor
    a drive letter. Asked of that key, `_escapes_repo` answers no on every lane
    that declares the knob — the monorepo shape the check was written for."""
    prefix = lane_prefix(lane.path_prefix)
    return path[len(prefix):] if prefix and path.startswith(prefix) else path


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
    spelled the way the artifact spells them."""
    reported = (_as_reported(lane, path) for path in coverage)
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


# What to do about it, which is not the same sentence for both readers. The
# coveragepy reader takes path_prefix and takes no repo root, so its refusal is
# about the environment the lane binds to and the prefix is a real knob. The
# istanbul reader takes the root, rebases every path under it and never reads
# path_prefix at all, so a path that stayed absolute came from another tree and
# no key on the lane can rebase it.
_COVERAGEPY_FIX = ("Point the lane at this checkout's own environment (a bare "
                   "`python -m pytest` binds to whichever venv the shell has active — run "
                   "it through the project's manager, `uv run python -m pytest ...`), or "
                   "set path_prefix when the runner reports paths relative to a subdirectory")
_ISTANBUL_FIX = ("The reader rebases every path under this checkout's root, so these were "
                 "written against another one: rerun the suite here rather than reusing an "
                 "artifact copied in or restored from a CI cache")

_COVERAGEPY_MISS = "or the runner reports paths this lane needs path_prefix to rebase"
_ISTANBUL_MISS = "or the suite measured a part of the tree these scopes do not name"

# And the third case: this tree, spelled absolutely. Neither fix above applies —
# the environment is right and path_prefix only ever PREPENDS — so the knob is
# the runner's own, and each reader has a different one.
_COVERAGEPY_ABSOLUTE_FIX = ("Make the runner write relative paths: `relative_files = true` "
                            "under `[tool.coverage.run]` in pyproject.toml, or "
                            "`[run] relative_files = true` in .coveragerc, then rerun the lane")
_ISTANBUL_ABSOLUTE_FIX = ("The reader strips this checkout's root off every measured path "
                          "literally, so the reporter spelled that root some other way: point "
                          "it at this checkout with its own cwd/root option, then rerun the lane")


def _wrong_tree_fix(lane: Lane) -> str:
    return _COVERAGEPY_FIX if lane.parser == "coveragepy" else _ISTANBUL_FIX


def _absolute_fix(lane: Lane) -> str:
    return _COVERAGEPY_ABSOLUTE_FIX if lane.parser == "coveragepy" else _ISTANBUL_ABSOLUTE_FIX


def _unmeasured_reading(lane: Lane) -> str:
    return _COVERAGEPY_MISS if lane.parser == "coveragepy" else _ISTANBUL_MISS


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
            f"paths like {_sample(outside)}. {_wrong_tree_fix(lane)}")


def _absolute_message(lane: Lane, coverage: dict, declared, inside: list[str]) -> str:
    return (f"{_zero_overlap(lane, coverage, declared)}, and {len(inside)} of them written "
            f"as absolute paths that DO sit under this checkout — {lane.artifact} measured "
            f"this tree and spelled it absolutely, and the join is on root-relative paths, "
            f"so it still matches nothing and every function in those scopes would score "
            f"untested; it reports paths like {_sample(inside)}. {_absolute_fix(lane)}")


def _unmeasured_message(lane: Lane, coverage: dict, declared) -> str:
    reports = f"; it measured {_sample(coverage)}" if coverage else ""
    return (f"{_zero_overlap(lane, coverage, declared)}, so every function in those "
            f"scopes will score untested{reports} — either nothing in them is exercised yet, "
            f"{_unmeasured_reading(lane)}")


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


def _results_summary(root: Path, lane: Lane) -> tuple[set[str], dict]:
    """The lane junit's failing ids and counts, or a ToolError saying why the
    report cannot be read. The message names the report and not the lane: one
    caller, two paths out of it, and each supplies the lane itself. The re-raise
    reaches the runner, which prefixes the lane name; `_warn_unreadable_results`
    names the lane in its own sentence."""
    from .junitparse import suite_summary

    results_path = root / lane.results_artifact
    if not results_path.is_file():
        raise ToolError(f"results_artifact {lane.results_artifact} is missing")
    return suite_summary(results_path.read_text(encoding="utf-8"))


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
        failed, counts = _results_summary(root, lane)
    except ToolError as reason:
        if not reuse_artifact:
            raise
        _warn_unreadable_results(lane, reason)
        return {}
    return {"failures": sorted(failed),
            "tests_total": counts["tests"], "tests_skipped": counts["skipped"]}


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


def build_retest_command(template: str, tests: set[str]) -> str:
    """Fill a retest template. {tests}: sorted quoted ids verbatim (pytest style).
    {files}: unique quoted classnames — vitest's junit classname IS the test file.
    {names}: re.escape'd alternation of test names, for -t style regex filters."""
    import re

    ordered = sorted(tests)
    files = sorted({t.split("::", 1)[0] for t in ordered})
    names = "|".join(re.escape(t.split("::", 1)[1]) for t in ordered if "::" in t)
    return (template.replace("{tests}", " ".join(f'"{t}"' for t in ordered))
                    .replace("{files}", " ".join(f'"{f}"' for f in files))
                    .replace("{names}", names))


def retest_lane(root: Path, lane: Lane, tests: set[str]) -> set[str]:
    """Run the lane's retest_command on just these ids; return the ones that
    PASS the rerun (the flakes). Any doubt — no artifact, a crash, a timeout —
    keeps everything failed."""
    command = build_retest_command(lane.retest_command, tests)
    log_path = _lane_log_path(root, lane)
    with open(log_path, "a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"\n--- flake retest ---\n$ {command}\n")
        fh.flush()
        try:
            code = run_bounded(command, _deadline(lane), stream=fh,
                               no_progress=_no_progress(lane), **_popen_kwargs(root, lane))
        except NoProgress:
            return set()
    if code is None:
        return set()
    still = _still_failed(root, lane)
    if still is None:
        return set()
    return set(tests) - still


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
                  reuse_artifact: bool) -> tuple[int | None, float]:
    """Reuse warns and costs nothing; a real run returns its exit code and wall seconds.

    The container guard belongs on this side of the branch. It names an OOM a
    python suite hits under a container memory cap, and `--reuse-artifacts`
    launches no suite: it parses a file already on disk, which costs no memory
    the guard is about. Sitting a line above this call, it refused the crapkit
    image reading host-built artifacts, and its message said something untrue
    about what the lane was going to do.
    """
    if reuse_artifact:
        _warn_stale_artifact(git, lane, scope_paths)
        return None, 0.0
    _refuse_container_python(lane)
    started = time.monotonic()
    return _run_attempts(root, lane), time.monotonic() - started


def _artifact_path(root: Path, lane: Lane) -> Path:
    """The artifact, or the same refusal a run that wrote none raises.

    Reuse is what reaches here — the run path refuses inside _run_attempts — and
    it was the one lane refusal that named no log, on the reading that a reused
    artifact had no run behind it. The previous run's log is usually sitting
    there with the story, and a reader told to look for `full log:` on every
    refusal has nowhere to go when one omits it.
    """
    path = root / lane.artifact
    if not path.is_file():
        _raise_no_artifact(root, lane, _lane_log_path(root, lane), None)
    return path


def run_lane(root: Path, lane: Lane, *, reuse_artifact: bool = False,
             scope_paths: dict | None = None, git: GitFacts | None = None) -> LaneOutcome:
    facts = _facts(root, git)
    exit_code, seconds = _run_or_reuse(root, lane, facts, scope_paths, reuse_artifact)
    coverage, digest = _read_and_parse(lane, root, _artifact_path(root, lane))
    _judge_artifact_scope(lane, coverage, scope_paths, root)
    provenance = {
        "artifact_sha256": digest,
        "exit_code": exit_code,
        "parser": lane.parser,
        "scopes": list(lane.scopes),
    }
    if lane.results_artifact:
        provenance.update(_results_provenance(root, lane, reuse_artifact=reuse_artifact))
    stamp = {} if reuse_artifact else _stamp_entry(facts, lane, seconds)
    return LaneOutcome(coverage, provenance, stamp)
