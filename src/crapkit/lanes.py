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
import sys
import time
from pathlib import Path
from typing import IO, NamedTuple

from .config import Lane
from .coverage_istanbul import FnCoverage
from .covstream import parse_coveragepy_file, parse_istanbul_file
from .errors import GitError, ToolError
from .gitio import GitFacts
from .procs import run_bounded
from .universe import owning_scope, path_matchers


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


def _log_tail(log_path: Path) -> str:
    if not log_path.is_file():
        return ""
    return log_path.read_text(encoding="utf-8", errors="replace").strip()[-500:]


def _popen_kwargs(root: Path, lane: Lane) -> dict:
    return {
        "cwd": root / lane.cwd if lane.cwd else root,
        "env": {**os.environ, **dict(lane.env)} if lane.env else None,
    }


def _deadline(lane: Lane) -> float | None:
    """The lane's own timeout, or None for no crapkit-owned deadline at all.
    0 is the config default and means the suite decides when it is done."""
    return lane.timeout_seconds or None


def _log_header(fh: IO[str], command: str, attempt: int) -> None:
    if attempt > 1:
        fh.write(f"\n--- attempt {attempt} ---\n")
    fh.write(f"$ {command}\n")
    fh.flush()


def _stream_command(root: Path, lane: Lane, log_path: Path, attempt: int) -> int:
    with open(log_path, "a" if attempt > 1 else "w", encoding="utf-8", errors="replace") as fh:
        _log_header(fh, lane.command, attempt)
        # The log is a file, not a pipe, so streaming costs the deadline nothing:
        # run_bounded kills the shell's whole tree, which is where the suite is.
        code = run_bounded(lane.command, _deadline(lane), stream=fh,
                           **_popen_kwargs(root, lane))
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


def _missing_plugin_hint(tail: str) -> str:
    """The one failure signature a new user cannot decode: pytest rejecting
    --cov points at crapkit's config when the real gap is the pytest-cov package."""
    if "unrecognized arguments" in tail and "--cov" in tail:
        return " — the --cov flags come from the pytest-cov package: pip install pytest-cov"
    return ""


def _raise_no_artifact(lane: Lane, log_path: Path, exit_code: int | None) -> None:
    detail = f" (command exit {exit_code})" if exit_code is not None else ""
    tail = _log_tail(log_path)
    hint = f"; last output: {tail}" if tail else ""
    raise ToolError(f"lane {lane.name!r} produced no artifact at {lane.artifact}{detail}{hint}"
                    f"{_missing_plugin_hint(tail)}")


def _run_attempts(root: Path, lane: Lane) -> int | None:
    log_path = _lane_log_path(root, lane)
    exit_code: int | None = None
    for attempt in range(1, lane.retries + 2):
        exit_code = _attempt_once(root, lane, log_path, attempt)
        if exit_code is not None and (root / lane.artifact).is_file():
            return exit_code
    _raise_no_artifact(lane, log_path, exit_code)
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


def _scope_changes(git: GitFacts, lane: Lane, scope_paths: dict, since_commit: str) -> list[str]:
    """Committed or working-tree changes under this lane's scope paths since a commit.

    Ownership is universe's, asked over this lane's scopes alone so a file a
    NESTED scope owns cannot go stale on the parent's lane. Prefix matching on
    its own missed a scope that declares a FILE rather than a directory — the
    shape crapkit's own tests/e2e/test_parallel_lanes_e2e.py writes — and
    editing that file read as no change at all.
    """
    matchers = path_matchers({name: scope_paths.get(name, ()) for name in lane.scopes})
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
    """
    if lane.parser == "istanbul":
        return parse_istanbul_file(artifact_path, repo_root=str(root))
    if lane.parser == "coveragepy":
        return parse_coveragepy_file(artifact_path, path_prefix=lane.path_prefix)
    raise ToolError(f"lane {lane.name!r}: parser {lane.parser!r} not implemented yet")


def _results_provenance(root: Path, lane: Lane) -> dict:
    from .junitparse import suite_summary

    results_path = root / lane.results_artifact
    if not results_path.is_file():
        # Under --reuse-artifacts too: a declared-but-missing results file
        # would make the no-NEW-failures check pass vacuously.
        raise ToolError(f"lane {lane.name!r}: results_artifact {lane.results_artifact} is missing")
    failed, counts = suite_summary(results_path.read_text(encoding="utf-8"))
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
        code = run_bounded(command, _deadline(lane), stream=fh, **_popen_kwargs(root, lane))
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
    """Reuse warns and costs nothing; a real run returns its exit code and wall seconds."""
    if reuse_artifact:
        _warn_stale_artifact(git, lane, scope_paths)
        return None, 0.0
    started = time.monotonic()
    return _run_attempts(root, lane), time.monotonic() - started


def _artifact_path(root: Path, lane: Lane) -> Path:
    path = root / lane.artifact
    if not path.is_file():
        raise ToolError(f"lane {lane.name!r} produced no artifact at {lane.artifact}")
    return path


def run_lane(root: Path, lane: Lane, *, reuse_artifact: bool = False,
             scope_paths: dict | None = None, git: GitFacts | None = None) -> LaneOutcome:
    facts = _facts(root, git)
    _refuse_container_python(lane)
    exit_code, seconds = _run_or_reuse(root, lane, facts, scope_paths, reuse_artifact)
    coverage, digest = _read_and_parse(lane, root, _artifact_path(root, lane))
    provenance = {
        "artifact_sha256": digest,
        "exit_code": exit_code,
        "parser": lane.parser,
        "scopes": list(lane.scopes),
    }
    if lane.results_artifact:
        provenance.update(_results_provenance(root, lane))
    stamp = {} if reuse_artifact else _stamp_entry(facts, lane, seconds)
    return LaneOutcome(coverage, provenance, stamp)
