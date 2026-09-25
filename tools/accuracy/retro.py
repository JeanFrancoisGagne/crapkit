"""Replay every past calculation bug's check on the commit before its fix and on the fix.

    python tools/accuracy/retro.py run ID... [--record] [--python 3.12]
    python tools/accuracy/retro.py nightly --slice-of 7 [--day N]
    python tools/accuracy/retro.py release
    python tools/accuracy/retro.py digest NODE_ID
    python tools/accuracy/retro.py stale

tests/accuracy/suite_strength/retro/bugs.tsv names each bug (an R id), its fix
commits, the commit before them and the check that must catch it: a node id in
the accuracy suite, or an API probe under retro/probes/. ledger.tsv records what
the last replay saw.

A replay adds a worktree at each commit, builds a venv there with uv (the
commit's crapkit, no dependencies, plus the lizard release it was built
against), and runs the check from this tree with CRAPKIT_ACCURACY_PYTHON
pointing at that venv, so kit.drive spawns the old crapkit. A probe runs with
the venv's interpreter directly, with the worktree as its argument.

The verdict is strict. Before counts as red only when the check fails on an
AssertionError (a pin_ruling mismatch is one): the check saw the wrong value.
Any other failure (DriveUnsupported, a refused config, an ImportError, a
KeyError on an older schema) is `not replayable`, and the row needs an API
probe. A check that passes on its before commit catches nothing and is refused.
The fix commit must pass.

The ledger row carries a digest over the check's file, its static import
closure under tests/ and tools/, and the data files of its packet, so a replay
that no longer matches the code it recorded shows as stale. `nightly` replays
the stale rows plus one seventh of the rest (every row once a week); `release`
replays every row whose digest changed plus the bundle rows, whose commits live
only in the pre-2026-08-24 history bundle (CRAPKIT_RETRO_BUNDLE). Both exit 1
when a replay contradicts its ledger row: a before that is no longer red, a fix
that no longer passes.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
RETRO = REPO / "tests" / "accuracy" / "suite_strength" / "retro"
BUGS = RETRO / "bugs.tsv"
LEDGER = RETRO / "ledger.tsv"
WORK_ENV = "CRAPKIT_RETRO_WORK"
WORK = Path(os.environ.get(WORK_ENV) or REPO / ".crapkit" / "accuracy" / "retro")
BUG_COLUMNS = ("id", "fix_commits", "before_commit", "packet", "test", "probe", "method",
               "platform", "replay", "calc", "symptom")
LEDGER_COLUMNS = ("id", "test", "before_commit", "fix_commit", "lizard", "before",
                  "failure_class", "before_evidence", "fix", "fix_evidence", "digest",
                  "replayed", "note")
LIZARD = "1.24.0"  # every commit on main was built against it (pyproject's comment)
OUTCOMES_ENV = "CRAPKIT_RETRO_OUTCOMES"
BUNDLE_ENV = "CRAPKIT_RETRO_BUNDLE"
PYTHON_ENV = "CRAPKIT_ACCURACY_PYTHON"
COLLECT_ALL_ENV = "CRAPKIT_ACCURACY_COLLECT_ALL"
DATA_DIRS = ("fixtures", "probes", "recorded", "small", "goldens", "known_kill")
PLATFORMS = {"any": "", "windows": "win32", "linux": "linux", "macos": "darwin"}


class RetroError(ValueError):
    """A table, commit or argument this tool cannot use."""


# --- tables -------------------------------------------------------------------------------

def read_table(path: Path, columns: tuple[str, ...]) -> list[dict]:
    """A tab-separated table with `columns` as its header; a missing file has no rows."""
    if not path.is_file():
        return []
    lines = _lines(path)
    _check_header(path, lines, columns)
    return [_cells(path, number, line, columns) for number, line in enumerate(lines[1:], 2)]


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _check_header(path: Path, lines: list[str], columns: tuple[str, ...]) -> None:
    if not lines or tuple(lines[0].split("\t")) != columns:
        raise RetroError(f"{path}: the header must be {' '.join(columns)} (tab-separated)")


def _cells(path: Path, number: int, line: str, columns: tuple) -> dict:
    cells = line.split("\t")
    if len(cells) != len(columns):
        raise RetroError(f"{path}:{number}: {len(cells)} cells, the header has {len(columns)}")
    return dict(zip(columns, cells))


def write_table(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    body = ["\t".join(columns)] + ["\t".join(_cell(row[c]) for c in columns) for row in rows]
    path.write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")


def _cell(value: str) -> str:
    return " ".join(str(value).replace("\t", " ").split())


def row_key(row: dict) -> tuple[str, str]:
    return row["id"], row["test"]


# --- the verdict --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Outcome:
    """One replay of a check against one commit."""
    verdict: str        # red | green | not replayable | pass | fail
    failure_class: str = ""
    evidence: str = ""


NOT_COLLECTED = {"exc_type": "NotCollected", "message": "the check collected no test"}


def _failed(records: list[dict]) -> list[dict]:
    return [record for record in records if record["outcome"] == "failed"]


def _red(failed: list[dict]) -> list[dict]:
    return [record for record in failed if record["assertion"]]


def classify_before(records: list[dict]) -> Outcome:
    """red on an AssertionError, green when every item passed, else not replayable."""
    failed = _failed(records)
    if records and not failed:
        return Outcome("green", "", f"{len(records)} item(s) passed")
    red = _red(failed)
    first = _first(red, failed)
    return Outcome("red" if red else "not replayable", first["exc_type"], first["message"])


def _first(red: list[dict], failed: list[dict]) -> dict:
    """The record that explains the verdict: a red one, else any failure, else none ran."""
    return (red or failed or [NOT_COLLECTED])[0]


def classify_fix(records: list[dict]) -> Outcome:
    failed = _failed(records)
    if records and not failed:
        return Outcome("pass", "", f"{len(records)} item(s) passed")
    first = failed[0] if failed else NOT_COLLECTED
    return Outcome("fail", first["exc_type"], first["message"])


def contradiction(recorded: dict, before: Outcome, fix: Outcome) -> str:
    """Why a replay disagrees with its ledger row, or "" when it agrees."""
    if before.verdict == "green":
        return f"{recorded['id']}: the check passes on its before commit, so it catches nothing"
    if recorded.get("before") == "red" and before.verdict != "red":
        return f"{recorded['id']}: the ledger says red on the before commit, the replay says {before.verdict}"
    if fix.verdict != "pass":
        return f"{recorded['id']}: the check fails on its fix commit: {fix.evidence[:200]}"
    return ""


# --- the pytest plugin that records each item's exception ------------------------------------------

def pytest_runtest_makereport(item, call):
    """Loaded with `-p retro`: append each failing or passing phase's outcome."""
    target = os.environ.get(OUTCOMES_ENV)
    if not target or (call.when != "call" and call.excinfo is None):
        return None
    record = _record(item.nodeid, call)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return None


def _record(nodeid: str, call) -> dict:
    info = call.excinfo
    if info is None:
        return {"nodeid": nodeid, "outcome": "passed", "exc_type": "", "assertion": False,
                "message": ""}
    kind = info.type
    return {"nodeid": nodeid, "outcome": _failure_kind(kind), "exc_type": kind.__name__,
            "assertion": issubclass(kind, AssertionError),
            "message": _cell(str(info.value))[:300]}


def _failure_kind(kind) -> str:
    """pytest's skip and xfail are exceptions too; neither is a failure."""
    return "skipped" if kind.__name__ in ("Skipped", "XFailed") else "failed"


def _replaces(kept: dict | None) -> bool:
    """A later phase's record replaces a passing one: a teardown error fails the item."""
    return kept is None or kept["outcome"] == "passed"


def item_outcomes(lines: list[str]) -> list[dict]:
    """One record per item: its failing phase if any, else its call phase."""
    by_item: dict[str, dict] = {}
    for record in map(json.loads, lines):
        if _replaces(by_item.get(record["nodeid"])):
            by_item[record["nodeid"]] = record
    return [record for record in by_item.values() if record["outcome"] != "skipped"]


# --- the digest ------------------------------------------------------------------------------------

def _closure_files(test_file: Path) -> set[Path]:
    sys.path.insert(0, str(REPO / "tests"))
    from accuracy.kit import closure
    return closure.closure(test_file)


def _packet_data(test_file: Path) -> set[Path]:
    accuracy = REPO / "tests" / "accuracy"
    parts = test_file.resolve().relative_to(accuracy).parts
    packet = accuracy / parts[0]
    return {path.resolve() for name in DATA_DIRS for path in packet.rglob("*")
            if path.is_file() and name in path.relative_to(packet).parts[:-1]}


def check_files(test: str, probe: str = "") -> list[Path]:
    """The check's file, its import closure and its packet's data files."""
    test_file = (REPO / test.split("::")[0]).resolve()
    files = _closure_files(test_file) | _packet_data(test_file)
    if probe:
        files.add((RETRO / "probes" / probe).resolve())
    return sorted(files)


def digest(test: str, probe: str = "") -> str:
    hashed = hashlib.sha256()
    for path in check_files(test, probe):
        relative = path.relative_to(REPO).as_posix()
        hashed.update(relative.encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return hashed.hexdigest()[:16]


# --- worktrees and venvs ------------------------------------------------------------------------------

def _run(argv: list, cwd: Path = REPO, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(part) for part in argv], cwd=cwd, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _checked(argv: list, cwd: Path = REPO) -> str:
    done = _run(argv, cwd)
    if done.returncode != 0:
        raise RetroError(f"{' '.join(map(str, argv))}: {done.stderr.strip()[-400:]}")
    return done.stdout


def have_commit(sha: str) -> bool:
    return _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"]).returncode == 0


def fetch_bundle(sha: str) -> None:
    bundle = os.environ.get(BUNDLE_ENV, "")
    if not bundle:
        raise RetroError(f"{sha} is not in this clone; set {BUNDLE_ENV} to the history bundle")
    _checked(["git", "fetch", "-q", bundle, "+refs/*:refs/retro-bundle/*"])
    if not have_commit(sha):
        raise RetroError(f"{sha} is in neither this clone nor {bundle}")


def worktree(sha: str, work: Path = WORK) -> Path:
    """A detached worktree at `sha`, reused when it is already there."""
    path = work / sha[:12]
    if (path / ".git").exists():
        return path
    if not have_commit(sha):
        fetch_bundle(sha)
    work.mkdir(parents=True, exist_ok=True)
    _checked(["git", "worktree", "add", "-f", "--detach", path, sha])
    return path


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def build_venv(tree: Path, python: str, extras: list[str], editable: bool) -> Path:
    """The commit's crapkit (no dependencies) plus lizard and any extras a probe asks for."""
    venv = tree.parent / f"{tree.name}-venv-{python}"
    interpreter = venv_python(venv)
    if not interpreter.exists():
        _checked(["uv", "venv", "-q", "--python", python, venv])
        install = ["-e", tree] if editable else [tree]
        _checked(["uv", "pip", "install", "-q", "--python", interpreter, "--no-deps", *install])
        _checked(["uv", "pip", "install", "-q", "--python", interpreter, f"lizard=={LIZARD}",
                  *extras])
    return interpreter


# --- replaying one check -------------------------------------------------------------------------------

def _pytest_env(interpreter: Path, outcomes: Path) -> dict:
    paths = [str(REPO / "tests"), str(REPO / "tools" / "accuracy"), os.environ.get("PYTHONPATH", "")]
    return {**os.environ, PYTHON_ENV: str(interpreter), OUTCOMES_ENV: str(outcomes),
            COLLECT_ALL_ENV: "1", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join(filter(None, paths))}


def replay_node(test: str, interpreter: Path) -> list[dict]:
    """Run one node id of this tree against `interpreter`'s crapkit; one record per item."""
    with tempfile.TemporaryDirectory(prefix="crapkit-retro-") as scratch:
        outcomes = Path(scratch) / "outcomes.jsonl"
        outcomes.touch()
        argv = [sys.executable, "-m", "pytest", test, "-q", "-p", "no:cacheprovider",
                "-p", "no:randomly", "-p", "retro", "--rootdir", REPO]
        _run(argv, env=_pytest_env(interpreter, outcomes))
        return item_outcomes(outcomes.read_text(encoding="utf-8").splitlines())


def probe_header(path: Path) -> tuple[list[str], bool]:
    """A probe's `# requires:` packages and whether it wants `# install: editable`."""
    requires, editable = [], False
    for line in path.read_text(encoding="utf-8").splitlines()[:20]:
        if line.startswith("# requires:"):
            requires += line.split(":", 1)[1].split()
        editable = editable or line.strip() == "# install: editable"
    return requires, editable


def replay_probe(probe: str, interpreter: Path, tree: Path) -> list[dict]:
    """A probe exits 0 when the expected value holds and raises AssertionError when not."""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    done = _run([interpreter, RETRO / "probes" / probe, tree], cwd=tree, env=env)
    if done.returncode == 0:
        return [{"nodeid": probe, "outcome": "passed", "exc_type": "", "assertion": False,
                 "message": ""}]
    tail = done.stderr.strip().splitlines()[-1:] or ["(no output)"]
    kind = tail[0].split(":", 1)[0].rsplit(".", 1)[-1]
    return [{"nodeid": probe, "outcome": "failed", "exc_type": kind,
             "assertion": kind == "AssertionError", "message": _cell(tail[0])[:300]}]


@dataclass(frozen=True)
class Bug:
    id: str
    test: str
    before: str
    fix: str
    probe: str = ""


def bug_of(row: dict) -> Bug:
    fixes = [sha.strip() for sha in row["fix_commits"].split(",") if sha.strip()]
    return Bug(row["id"], row["test"], row["before_commit"], fixes[-1] if fixes else "",
               row["probe"])


def _records(bug: Bug, sha: str, python: str) -> list[dict]:
    tree = worktree(sha)
    requires, editable = probe_header(RETRO / "probes" / bug.probe) if bug.probe else ([], False)
    interpreter = build_venv(tree, python, requires, editable)
    if bug.probe:
        return replay_probe(bug.probe, interpreter, tree)
    return replay_node(bug.test, interpreter)


def replay(bug: Bug, python: str) -> tuple[Outcome, Outcome]:
    before = classify_before(_records(bug, bug.before, python))
    fix = classify_fix(_records(bug, bug.fix, python))
    return before, fix


def ledger_row(bug: Bug, before: Outcome, fix: Outcome, note: str = "") -> dict:
    today = datetime.date.today().isoformat()
    return {"id": bug.id, "test": bug.test, "before_commit": bug.before, "fix_commit": bug.fix,
            "lizard": LIZARD, "before": before.verdict, "failure_class": before.failure_class,
            "before_evidence": before.evidence, "fix": fix.verdict,
            "fix_evidence": fix.evidence, "digest": digest(bug.test, bug.probe),
            "replayed": today, "note": note}


# --- choosing rows -------------------------------------------------------------------------------------

def check_exists(row: dict, repo: Path = REPO) -> bool:
    """Whether this tree holds the row's check yet: its packet may not have landed."""
    return (repo / row["test"].split("::")[0]).is_file()


def replayable_here(row: dict, platform: str = sys.platform) -> bool:
    """An open bug has no fix to replay, a platform row runs on its platform only,
    and a check this tree does not hold yet cannot run."""
    wanted = PLATFORMS.get(row["platform"], row["platform"])
    on_platform = not wanted or platform.startswith(wanted)
    return row["replay"] != "open" and on_platform and check_exists(row)


def _is_stale(row: dict, ledger: dict) -> bool:
    recorded = ledger.get(row_key(row))
    bug = bug_of(row)
    return recorded is None or recorded["digest"] != digest(bug.test, bug.probe)


def stale(bugs: list[dict], ledger: dict) -> list[dict]:
    """Rows whose check this tree holds and that were never replayed, or whose
    check changed since the recorded replay."""
    return [row for row in bugs if check_exists(row) and _is_stale(row, ledger)]


def weekly_slice(rows: list[dict], of: int, day: int) -> list[dict]:
    """Slice `day % of` of the rows in id order: `of` nights replay every row once."""
    ordered = sorted(rows, key=lambda row: (int(row["id"][1:]), row["test"]))
    return ordered[day % of::of]


def _bundle(row: dict) -> bool:
    return row["replay"] == "bundle"


# --- commands ------------------------------------------------------------------------------------------

def _load() -> tuple[list[dict], dict]:
    bugs = read_table(BUGS, BUG_COLUMNS)
    ledger = {row_key(row): row for row in read_table(LEDGER, LEDGER_COLUMNS)}
    return bugs, ledger


def _replay_one(row: dict, ledger: dict, python: str) -> tuple[str, dict]:
    """(what contradicts the ledger, or "", the fresh ledger row) for one bugs row."""
    bug = bug_of(row)
    before, fix = replay(bug, python)
    print(f"retro: {bug.id} {bug.test}: before {before.verdict}, fix {fix.verdict}")
    problem = contradiction(ledger.get(row_key(row), {"id": bug.id}), before, fix)
    return problem, ledger_row(bug, before, fix, note=problem)


def _replay_rows(rows: list[dict], ledger: dict, python: str, record: bool) -> int:
    fresh = dict(ledger)
    problems = []
    for row in rows:
        problem, fresh[row_key(row)] = _replay_one(row, ledger, python)
        problems.append(problem)
    if record:
        write_table(LEDGER, LEDGER_COLUMNS, sorted(fresh.values(), key=_ledger_order))
    return _report(list(filter(None, problems)))


def _report(problems: list[str]) -> int:
    for problem in problems:
        print(f"retro: {problem}", file=sys.stderr)
    return 1 if problems else 0


def _ledger_order(row: dict) -> tuple:
    return int(row["id"][1:]), row["test"]


def _run_cmd(args) -> int:
    bugs, ledger = _load()
    wanted = set(args.ids)
    rows = [row for row in bugs if row["id"] in wanted and replayable_here(row)]
    if not rows:
        raise RetroError(f"no replayable bugs.tsv row for {', '.join(sorted(wanted))} here")
    return _replay_rows(rows, ledger, args.python, args.record)


def _union(*groups: list[dict]) -> list[dict]:
    return list({row_key(row): row for group in groups for row in group}.values())


def _public(bugs: list[dict]) -> list[dict]:
    return [row for row in bugs if replayable_here(row) and not _bundle(row)]


def _nightly(args) -> int:
    bugs, ledger = _load()
    public = _public(bugs)
    day = datetime.date.today().toordinal() if args.day is None else args.day
    chosen = _union(stale(public, ledger), weekly_slice(public, args.slice_of, day))
    return _replay_rows(chosen, ledger, args.python, record=False)


def _release(args) -> int:
    bugs, ledger = _load()
    rows = [row for row in bugs if replayable_here(row)]
    chosen = _union(stale(rows, ledger), [row for row in rows if _bundle(row)])
    return _replay_rows(chosen, ledger, args.python, record=False)


def _digest_cmd(args) -> int:
    print(digest(args.test, args.probe))
    return 0


def _stale_cmd(args) -> int:
    bugs, ledger = _load()
    for row in stale(bugs, ledger):
        print(f"{row['id']}\t{row['test']}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retro.py", description=__doc__.splitlines()[0])
    parser.add_argument("--python", default=f"{sys.version_info.major}.{sys.version_info.minor}")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("ids", nargs="+")
    run.add_argument("--record", action="store_true", help="rewrite the replayed ledger rows")
    nightly = sub.add_parser("nightly")
    nightly.add_argument("--slice-of", type=int, default=7)
    nightly.add_argument("--day", type=int)
    sub.add_parser("release")
    digest_p = sub.add_parser("digest")
    digest_p.add_argument("test")
    digest_p.add_argument("--probe", default="")
    sub.add_parser("stale")
    return parser


COMMANDS = {"run": _run_cmd, "nightly": _nightly, "release": _release, "digest": _digest_cmd,
            "stale": _stale_cmd}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except RetroError as refused:
        print(f"retro.py: {refused}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
