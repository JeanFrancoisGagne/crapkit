"""Replay a consumer repository's recorded coverage under two crapkit wheels, at release.

    python tools/accuracy/consumer_replay.py record --repo PATH --commit SHA --artifacts DIR
    python tools/accuracy/consumer_replay.py replay --repo PATH --artifacts DIR
        --base-wheel SIDE --candidate-wheel SIDE [--declared-since REF] [--out DIR]

A large consumer repository measures crapkit on code nobody wrote for it.
`record` checks the repository out at a pinned commit, runs `crapkit
coverage` there once with the real lanes (the crapkit on this interpreter),
and copies every lane's coverage and results artifact into DIR with a
manifest naming the commit. That is the one expensive step, done when the pin
moves.

`replay` checks the same commit out twice, once per side, puts the recorded
artifacts under .crapkit/recorded/ and rewrites each [[lane]] command to the
kit's copy command, so no test suite runs. Each side (a wheel, a CI hand-off
directory or `crapkit==VERSION`, as wheel_diff.py reads them) then runs
`inventory --export` and `coverage --export`. The moved-row map is
wheel_diff.py's. With `--declared-since REF` (the previous release tag) every
moved calc must be named by a CHANGES.tsv row added since REF; the counts per
calc are printed either way.

A side that cannot be installed is an infra failure. The candidate exiting
non-zero, an internal-check stop (exit 5) included, is a failure: a consumer
repository is where a wrong number would reach a user.

Exit codes: 0 replayed and every move declared; 1 an undeclared move or a
candidate that stopped; 3 a side, a checkout or a recording could not be made.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

HERE = Path(__file__).resolve().parent
WHEEL_DIFF = "accuracy_wheel_diff"


def _wheel_diff():
    """wheel_diff.py, loaded once by path under the name the accuracy tests use."""
    if WHEEL_DIFF not in sys.modules:
        spec = importlib.util.spec_from_file_location(WHEEL_DIFF, HERE / "wheel_diff.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[WHEEL_DIFF] = module
        spec.loader.exec_module(module)
    return sys.modules[WHEEL_DIFF]


wheel_diff = _wheel_diff()
from accuracy.kit import drive, repos  # noqa: E402  (wheel_diff put tests/ on sys.path)

EXIT_MOVED, EXIT_INFRA = 1, 3
MANIFEST = "manifest.json"
RECORDED = ".crapkit/recorded"
_TABLE = re.compile(r"^\s*\[")
_COMMAND = re.compile(r"^\s*command\s*=")


class ReplayError(RuntimeError):
    """A checkout, a recording or a config the replay cannot use: infra, not a move."""


class CandidateStopped(RuntimeError):
    """The candidate exited non-zero on the consumer repository."""


# --- checkout and the recorded lanes ---------------------------------------------------------

def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise ReplayError(f"git {' '.join(args)} exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def checkout(repo: Path, commit: str, dest: Path) -> Path:
    """A clone of repo at commit that shares its objects, with no line-ending conversion."""
    _git(dest.parent, "clone", "-q", "--shared", "--no-checkout", "-c", "core.autocrlf=false",
         str(repo), dest.name)
    _git(dest, "checkout", "-q", "--detach", commit)
    return dest


def lanes(config_text: str) -> list[dict]:
    """Each [[lane]] table's name and the artifacts it writes."""
    return [{"name": lane["name"], "artifact": lane["artifact"],
             "results_artifact": lane.get("results_artifact")}
            for lane in tomllib.loads(config_text).get("lane", [])]


def _pairs(lane: dict) -> list[tuple[str, str]]:
    """(recorded copy, artifact path) for each file the lane writes."""
    written = [lane["artifact"], lane["results_artifact"]]
    return [(f"{RECORDED}/{lane['name']}/{Path(path).name}", path) for path in written if path]


def _command_line(lane: dict) -> str:
    return 'command = "' + repos.copy_command(*_pairs(lane)).replace('"', '\\"') + '"'


def _lane_starts(lines: list[str]) -> list[int]:
    return [number for number, line in enumerate(lines) if line.strip() == "[[lane]]"]


def _command_at(lines: list[str], start: int) -> int:
    """The line number of the `command =` key in the table that opens at start."""
    for number in range(start + 1, len(lines)):
        if _TABLE.match(lines[number]):
            break
        if _COMMAND.match(lines[number]):
            return number
    raise ReplayError(f"the [[lane]] table at line {start + 1} has no one-line `command =`")


def recorded_config(config_text: str) -> str:
    """crapkit.toml with every lane command replaced by the copy of its recording."""
    lines = config_text.split("\n")
    for start, lane in zip(_lane_starts(lines), lanes(config_text)):
        number = _command_at(lines, start)
        if lines[number].count('"""') or lines[number].count("'''"):
            raise ReplayError(f"lane {lane['name']}: a multi-line command cannot be rewritten")
        lines[number] = _command_line(lane)
    return "\n".join(lines)


def place(root: Path, artifacts: Path) -> None:
    """Put the recording under .crapkit/recorded/ and point the lanes at it."""
    config = root / "crapkit.toml"
    text = config.read_bytes().decode("utf-8")
    for lane in lanes(text):
        for recorded, _ in _pairs(lane):
            source = artifacts / Path(recorded).relative_to(RECORDED)
            (root / recorded).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, root / recorded)
    config.write_bytes(recorded_config(text).encode("utf-8"))


# --- record ------------------------------------------------------------------------------------

def _save(root: Path, lane: dict, artifacts: Path) -> None:
    for recorded, artifact in _pairs(lane):
        target = artifacts / Path(recorded).relative_to(RECORDED)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / artifact, target)


def record(repo: Path, commit: str, artifacts: Path, work: Path) -> list[str]:
    """Run the real lanes once at commit and keep what they wrote."""
    root = checkout(repo, commit, work / "record")
    result = drive.Driver(root, spawn=True).run("coverage")
    if result.code != 0:
        raise ReplayError(f"crapkit coverage exited {result.code} at {commit}: {result.stderr[-800:]}")
    found = lanes((root / "crapkit.toml").read_bytes().decode("utf-8"))
    for lane in found:
        _save(root, lane, artifacts)
    manifest = {"commit": _git(root, "rev-parse", "HEAD").strip(),
                "lanes": [lane["name"] for lane in found]}
    (artifacts / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return [f"recorded {len(found)} lane(s) at {manifest['commit'][:12]} into {artifacts}"]


# --- replay ---------------------------------------------------------------------------------

def committed_at(root: Path) -> int:
    return int(_git(root, "log", "-1", "--format=%ct").strip())


def measure_side(site: Path, root: Path, out: Path, candidate: bool) -> dict[str, str]:
    """One side's exports; a candidate that exits non-zero raises CandidateStopped."""
    out.mkdir(parents=True)
    try:
        return wheel_diff.run_commands(root, site, out, wheel_diff.SMALL_COMMANDS,
                                       committed_at(root) + 86_400)
    except wheel_diff.WheelDiffError as failed:
        raise (CandidateStopped(str(failed)) if candidate else failed) from None


def _side(wheel: Path, name: str, repo: Path, artifacts: Path, work: Path) -> dict[str, str]:
    commit = json.loads((artifacts / MANIFEST).read_text(encoding="utf-8"))["commit"]
    root = checkout(repo, commit, work / f"{name}-repo")
    place(root, artifacts)
    site = wheel_diff.unpack(wheel, work / f"{name}-site")
    return measure_side(site, root, work / f"{name}-out", candidate=name == "candidate")


def _judged(args, exports: tuple) -> tuple[int, list[str]]:
    rows = wheel_diff.diff_exports(*exports)
    if args.out:
        wheel_diff.write_out(args.out, exports, rows)
    declared = wheel_diff.declared_since(args.declared_since) if args.declared_since else None
    return wheel_diff.verdict(rows, [wheel_diff.undeclared_problem(rows, declared)],
                              " on the consumer repository")


def replay(args, work: Path) -> tuple[int, list[str]]:
    sides = tuple(wheel_diff.resolve(spec, args.wheelhouse)
                  for spec in (args.base_wheel, args.candidate_wheel))
    skipped = wheel_diff.skip_line(sides)
    if skipped:
        return 0, [skipped]
    return _judged(args, tuple(_side(wheel, name, args.repo, args.artifacts, work)
                               for wheel, name in zip(sides, ("base", "candidate"))))


# --- commands ------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="consumer_replay.py",
                                     description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--repo", type=Path, required=True)
    rec.add_argument("--commit", required=True)
    rec.add_argument("--artifacts", type=Path, required=True)
    rep = sub.add_parser("replay")
    rep.add_argument("--repo", type=Path, required=True)
    rep.add_argument("--artifacts", type=Path, required=True)
    rep.add_argument("--base-wheel", required=True)
    rep.add_argument("--candidate-wheel", required=True)
    rep.add_argument("--declared-since")
    rep.add_argument("--out", type=Path)
    rep.add_argument("--wheelhouse", type=Path, default=wheel_diff.default_wheelhouse())
    return parser


def _dispatch(args, work: Path) -> tuple[int, list[str]]:
    if args.command == "record":
        return 0, record(args.repo, args.commit, args.artifacts, work)
    return replay(args, work)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="crapkit-consumer-",
                                     ignore_cleanup_errors=True) as scratch:
        try:
            code, lines = _dispatch(args, Path(scratch))
        except CandidateStopped as stopped:
            print(f"consumer_replay.py: the candidate stopped: {stopped}", file=sys.stderr)
            return EXIT_MOVED
        except (ReplayError, wheel_diff.WheelDiffError) as failed:
            print(f"consumer_replay.py: {failed}", file=sys.stderr)
            return EXIT_INFRA
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
