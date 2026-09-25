"""Replay every past calculation bug's check on the commit before its fix and on the fix.

    python tools/accuracy/retro.py run ID...|all [--record] [--python 3.12]
    python tools/accuracy/retro.py nightly --slice-of 7 [--day N]
    python tools/accuracy/retro.py release
    python tools/accuracy/retro.py digest NODE_ID
    python tools/accuracy/retro.py stale
    python tools/accuracy/retro.py sync

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

Each packet confirms the check names bugs.tsv proposed and lists the ones it
landed in its own tests/accuracy/<packet>/retro.tsv. `sync` rewrites a landed
packet's bugs.tsv rows to those (id, test) pairs, carrying each bug's commits,
calc and symptom over, gives every new pair a pending ledger row and drops the
ledger rows of pairs no packet names any more; `run all --record` then replays
them.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv as venv_module

REPO = Path(__file__).resolve().parents[2]
RETRO = REPO / "tests" / "accuracy" / "suite_strength" / "retro"
# The digest reads each check's import closure through the kit (accuracy.kit.closure).
sys.path.insert(0, str(REPO / "tests"))
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


def _read(path: Path) -> str:
    """A file's text; every file this tool reads is UTF-8."""
    return path.read_bytes().decode()


def _write(path: Path, text: str) -> None:
    """Write UTF-8 text with the newlines as given, on every OS."""
    path.write_bytes(text.encode())


# --- tables -------------------------------------------------------------------------------

def read_table(path: Path, columns: tuple[str, ...]) -> list[dict]:
    """A tab-separated table with `columns` as its header; a missing file has no rows."""
    if not path.is_file():
        return []
    lines = _lines(path)
    _check_header(path, lines, columns)
    return [_cells(path, number, line, columns) for number, line in enumerate(lines[1:], 2)]


def _lines(path: Path) -> list[str]:
    return [line for line in _read(path).splitlines() if line.strip()]


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
    _write(path, "\n".join(body) + "\n")


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

def _roots(repo: Path) -> tuple[Path, ...]:
    return (repo / "tests", repo / "tools" / "accuracy", repo / "tools")


def _closure_files(test_file: Path, repo: Path) -> set[Path]:
    from accuracy.kit import closure
    return closure.closure(test_file, _roots(repo.resolve()))


def _is_data(path: Path, packet: Path) -> bool:
    return path.is_file() and bool(set(DATA_DIRS) & set(path.relative_to(packet).parts[:-1]))


def _packet_data(test_file: Path, repo: Path) -> set[Path]:
    accuracy = (repo / "tests" / "accuracy").resolve()
    packet = accuracy / test_file.relative_to(accuracy).parts[0]
    return {path.resolve() for path in packet.rglob("*") if _is_data(path, packet)}


def _package_inits(test_file: Path, repo: Path) -> set[Path]:
    """The __init__.py of every package the check's module sits in under tests/:
    pytest runs each of them before the check."""
    top = (repo / "tests").resolve()
    folders = [folder for folder in test_file.parents if top in folder.parents]
    return {folder / "__init__.py" for folder in folders if (folder / "__init__.py").is_file()}


def check_files(test: str, probe: str = "", repo: Path = REPO) -> list[Path]:
    """The check's file, its import closure, the packages it sits in and its packet's
    data files."""
    test_file = (repo / test.split("::")[0]).resolve()
    files = (_closure_files(test_file, repo) | _package_inits(test_file, repo)
             | _packet_data(test_file, repo))
    if probe:
        files.add((repo / RETRO.relative_to(REPO) / "probes" / probe).resolve())
    return sorted(files)


def digest(test: str, probe: str = "", repo: Path = REPO) -> str:
    """16 hex of a sha256 over every check file's repo path and bytes."""
    root = repo.resolve()
    hashed = hashlib.sha256()
    for path in check_files(test, probe, repo):
        hashed.update(path.relative_to(root).as_posix().encode() + b"\0")
        hashed.update(hashlib.sha256(path.read_bytes()).digest())
    return hashed.hexdigest()[:16]


# --- worktrees and venvs ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Site:
    """Where a replay builds and what it installs: the repo that holds the
    commits, the directory for worktrees and venvs, how a commit's crapkit goes
    into its venv (`wheel`, `editable`, or `link`: a .pth pointing at its src/)
    and the packages beside it. uv honours UV_CACHE_DIR and UV_OFFLINE, which CI
    points at its cached wheelhouse."""
    repo: Path = REPO
    work: Path = WORK
    install: str = "wheel"
    packages: tuple = (f"lizard=={LIZARD}",)


def _run(argv: list, cwd: Path = REPO, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(part) for part in argv], cwd=cwd, env=env, capture_output=True,
                          encoding="utf-8", errors="replace")


def _checked(argv: list, cwd: Path = REPO) -> str:
    done = _run(argv, cwd)
    if done.returncode != 0:
        raise RetroError(f"{' '.join(map(str, argv))}: {done.stderr.strip()[-400:]}")
    return done.stdout


def have_commit(sha: str, repo: Path = REPO) -> bool:
    return _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo).returncode == 0


def fetch_bundle(sha: str, repo: Path = REPO) -> None:
    bundle = os.environ.get(BUNDLE_ENV, "")
    if not bundle:
        raise RetroError(f"{sha} is not in this clone; set {BUNDLE_ENV} to the history bundle")
    _checked(["git", "fetch", "-q", bundle, "+refs/*:refs/retro-bundle/*"], cwd=repo)
    if not have_commit(sha, repo):
        raise RetroError(f"{sha} is in neither this clone nor {bundle}")


def worktree(sha: str, site: Site = Site()) -> Path:
    """A detached worktree at `sha`, reused when it is already there."""
    path = site.work / sha[:12]
    if (path / ".git").exists():
        return path
    if not have_commit(sha, site.repo):
        fetch_bundle(sha, site.repo)
    site.work.mkdir(parents=True, exist_ok=True)
    _checked(["git", "worktree", "add", "-f", "--detach", path, sha], cwd=site.repo)
    return path


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


CURRENT = f"{sys.version_info.major}.{sys.version_info.minor}"


def _create_venv(venv: Path, python: str) -> None:
    """The standard library's venv for this interpreter's version; uv for another."""
    if python == CURRENT:
        venv_module.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(venv)
    else:
        _checked(["uv", "venv", "-q", "--python", python, venv])


def _purelib(interpreter: Path) -> Path:
    code = "import sysconfig; print(sysconfig.get_path('purelib'))"
    return Path(_checked([interpreter, "-c", code]).strip())


def _install(interpreter: Path, tree: Path, how: str) -> None:
    if how == "link":
        link = _purelib(interpreter) / "retro-src.pth"
        _write(link, str(tree / "src") + "\n")
        return
    target = ["-e", tree] if how == "editable" else [tree]
    _checked(["uv", "pip", "install", "-q", "--python", interpreter, "--no-deps", *target])


def _packages(interpreter: Path, packages: list[str]) -> None:
    if packages:
        _checked(["uv", "pip", "install", "-q", "--python", interpreter, *packages])


def build_venv(tree: Path, python: str, site: Site = Site(), extra: tuple = ()) -> Path:
    """A venv beside the worktree holding its crapkit, the site's packages and `extra`."""
    venv = tree.parent / f"{tree.name}-venv-{python}-{site.install}"
    interpreter = venv_python(venv)
    if not interpreter.exists():
        _create_venv(venv, python)
        _install(interpreter, tree, site.install)
        _packages(interpreter, [*site.packages, *extra])
    return interpreter


# --- replaying one check -------------------------------------------------------------------------------

def _holds_crapkit(entry: str) -> bool:
    return (Path(entry) / "crapkit" / "__init__.py").is_file()


def _inherited_paths() -> list[str]:
    """The caller's PYTHONPATH minus any entry holding a crapkit package: the
    spawned old crapkit inherits it, and this tree's src/ would shadow the commit's."""
    entries = os.environ.get("PYTHONPATH", "").split(os.pathsep)
    return [entry for entry in entries if entry and not _holds_crapkit(entry)]


def _pytest_env(interpreter: Path, outcomes: Path) -> dict:
    paths = [str(REPO / "tests"), str(REPO / "tools" / "accuracy"), *_inherited_paths()]
    return {**os.environ, PYTHON_ENV: str(interpreter), OUTCOMES_ENV: str(outcomes),
            COLLECT_ALL_ENV: "1", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join(filter(None, paths))}


def _rootdir(test: str) -> Path:
    """This tree for a check in it; a check file elsewhere is its own root, so pytest
    never walks the directories above it."""
    path = (REPO / test.split("::")[0]).resolve()
    return REPO if path == REPO or REPO in path.parents else path.parent


def replay_node(test: str, interpreter: Path) -> list[dict]:
    """Run one node id of this tree against `interpreter`'s crapkit; one record per item."""
    with tempfile.TemporaryDirectory(prefix="crapkit-retro-") as scratch:
        outcomes = Path(scratch) / "outcomes.jsonl"
        outcomes.touch()
        argv = [sys.executable, "-m", "pytest", test, "-q", "-p", "no:cacheprovider",
                "-p", "no:randomly", "-p", "retro", "--rootdir", _rootdir(test)]
        _run(argv, env=_pytest_env(interpreter, outcomes))
        return item_outcomes(_read(outcomes).splitlines())


def probe_header(path: Path) -> tuple[list[str], bool]:
    """A probe's `# requires:` packages and whether it wants `# install: editable`."""
    requires, editable = [], False
    for line in _read(path).splitlines()[:20]:
        if line.startswith("# requires:"):
            requires += line.split(":", 1)[1].split()
        editable = editable or line.strip() == "# install: editable"
    return requires, editable


def replay_probe(probe: Path, interpreter: Path, tree: Path) -> list[dict]:
    """A probe exits 0 when the expected value holds and raises AssertionError when not."""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    done = _run([interpreter, probe, tree], cwd=tree, env=env)
    if done.returncode == 0:
        return [{"nodeid": probe.name, "outcome": "passed", "exc_type": "", "assertion": False,
                 "message": ""}]
    tail = done.stderr.strip().splitlines()[-1:] or ["(no output)"]
    kind = tail[0].split(":", 1)[0].rsplit(".", 1)[-1]
    return [{"nodeid": probe.name, "outcome": "failed", "exc_type": kind,
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


def _probe_records(probe: Path, tree: Path, python: str, site: Site) -> list[dict]:
    requires, editable = probe_header(probe)
    probe_site = replace(site, install="editable") if editable else site
    return replay_probe(probe, build_venv(tree, python, probe_site, tuple(requires)), tree)


def _records(bug: Bug, sha: str, python: str, site: Site) -> list[dict]:
    tree = worktree(sha, site)
    if bug.probe:
        return _probe_records(RETRO / "probes" / bug.probe, tree, python, site)
    return replay_node(bug.test, build_venv(tree, python, site))


def replay(bug: Bug, python: str, site: Site = Site()) -> tuple[Outcome, Outcome]:
    before = classify_before(_records(bug, bug.before, python, site))
    fix = classify_fix(_records(bug, bug.fix, python, site))
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


# --- syncing bugs.tsv with the packets' own retro tables ------------------------------------------------

ACCURACY = REPO / "tests" / "accuracy"
PENDING_NOTE = "no replay yet: the check lands with its packet"


def landed(accuracy: Path = ACCURACY) -> dict[str, list[dict]]:
    """{packet: its retro.tsv rows} for every packet that has one; each row has at
    least an id and a test, and may name a platform."""
    tables = {}
    for path in sorted(accuracy.glob("*/retro.tsv")):
        lines = _lines(path)
        header = lines[0].split("\t")
        tables[path.parent.name] = [dict(zip(header, line.split("\t"))) for line in lines[1:]]
    return tables


def _rows_of(bugs: list[dict], bug_id: str, packet: str | None = None) -> list[dict]:
    return [row for row in bugs if row["id"] == bug_id and packet in (None, row["packet"])]


def _template(bugs: list[dict], packet: str, bug_id: str) -> dict:
    """The row a new (id, test) pair copies: this packet's own row for the bug, else any."""
    rows = _rows_of(bugs, bug_id)
    if not rows:
        raise RetroError(f"{packet}/retro.tsv names {bug_id}, which bugs.tsv has no row for: "
                         "triage the commit that fixed it first")
    return (_rows_of(rows, bug_id, packet) or rows)[0]


def _bug_for(bugs: list[dict], packet: str, listed: dict) -> dict:
    base = _template(bugs, packet, listed["id"])
    same = base["packet"] == packet and base["test"] == listed["test"]
    return {**base, "packet": packet, "test": listed["test"], "probe": base["probe"] if same else "",
            "platform": listed.get("platform") or base["platform"]}


def synced_bugs(bugs: list[dict], tables: dict[str, list[dict]]) -> list[dict]:
    """bugs.tsv with each landed packet's rows replaced by the pairs its retro.tsv lists.
    A pair bugs.tsv already held keeps its place; a new one follows its bug's rows."""
    kept = [row for row in bugs if row["packet"] not in tables]
    fresh = [_bug_for(bugs, packet, listed) for packet, rows in tables.items() for listed in rows]
    return sorted(kept + fresh, key=_placed(bugs))


def _placed(bugs: list[dict]):
    """A sort key: by bug id, then where bugs.tsv held the pair, new pairs last."""
    place = {row_key(row): index for index, row in enumerate(bugs)}
    return lambda row: (int(row["id"][1:]), place.get(row_key(row), len(bugs)), row["test"])


def _waiting(bug: dict) -> dict:
    """A ledger row for a bug nobody has replayed yet: open while its fix is off main."""
    state = "open" if bug["replay"] == "open" else "pending"
    note = "branch not merged: strict xfail until it lands" if state == "open" else PENDING_NOTE
    fix = bug_of(bug).fix
    return {"id": bug["id"], "test": bug["test"], "before_commit": bug["before_commit"],
            "fix_commit": fix, "lizard": "", "before": state, "failure_class": "",
            "before_evidence": "", "fix": state, "fix_evidence": "", "digest": "", "replayed": "",
            "note": note}


def synced_ledger(ledger: dict, bugs: list[dict]) -> list[dict]:
    """One ledger row per bugs row: the recorded one where there is one, else a waiting one."""
    return sorted((ledger.get(row_key(row)) or _waiting(row) for row in bugs), key=_ledger_order)


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


def chosen(bugs: list[dict], ids: set[str]) -> list[dict]:
    """The replayable rows `ids` name; the id `all` names every row."""
    return [row for row in bugs if ("all" in ids or row["id"] in ids) and replayable_here(row)]


def _run_cmd(args) -> int:
    bugs, ledger = _load()
    wanted = set(args.ids)
    rows = chosen(bugs, wanted)
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


def _sync_cmd(args) -> int:
    bugs, ledger = _load()
    fresh = synced_bugs(bugs, landed(ACCURACY))
    write_table(BUGS, BUG_COLUMNS, fresh)
    write_table(LEDGER, LEDGER_COLUMNS, synced_ledger(ledger, fresh))
    added = len({row_key(row) for row in fresh} - {row_key(row) for row in bugs})
    print(f"retro: bugs.tsv holds {len(fresh)} rows ({added} new pairs); ledger.tsv follows")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retro.py", description=__doc__.splitlines()[0])
    parser.add_argument("--python", default=CURRENT)
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
    sub.add_parser("sync")
    return parser


COMMANDS = {"run": _run_cmd, "nightly": _nightly, "release": _release, "digest": _digest_cmd,
            "stale": _stale_cmd, "sync": _sync_cmd}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except RetroError as refused:
        print(f"retro.py: {refused}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
