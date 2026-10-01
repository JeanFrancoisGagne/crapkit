"""Replay every past calculation bug's check on the commit before its fix and on the fix.

    python tools/accuracy/retro.py run ID...|all [--record] [--python 3.12]
    python tools/accuracy/retro.py nightly --slice-of 7 [--day N] [--platform-only]
    python tools/accuracy/retro.py release
    python tools/accuracy/retro.py digest NODE_ID
    python tools/accuracy/retro.py stale
    python tools/accuracy/retro.py sync

tests/accuracy/suite_strength/retro/bugs.tsv names each bug (an R id), its fix
commits, the commit before them and the check that must catch it: a node id in
the accuracy suite (a parametrized one, `test_x[case]`, names the bug's own
case), or an API probe under retro/probes/. Its env cell, NAME=value words, sets
the switches an older crapkit needs to read the check's files (SWITCHES: the
languages it read, the root scope named by the tree's top-level entries).
ledger.tsv records what the last replay saw.

A replay adds a worktree at each commit, builds a venv there with uv (the
commit's crapkit, no dependencies, plus the lizard release it was built
against and the test runner the accuracy suite pins, which crapkit's own
children start), and runs the check from this tree with CRAPKIT_ACCURACY_PYTHON
pointing at that venv, so kit.drive spawns the old crapkit, and with that
crapkit first on the check's PYTHONPATH, so a check that calls crapkit in its
own process reads the old crapkit too. For a wheel install that entry is a copy
of crapkit alone (import_root): the rest of the venv's site-packages would
reach every python the check starts.
CRAPKIT_ACCURACY_CHECKOUT names the commit's worktree, where a check finds the
files a wheel does not carry, such as the Action's action.yml. A probe runs with
the venv's interpreter directly, with the worktree as its argument.

The verdict is strict. Before counts as red only when the check fails on an
AssertionError (a pin_ruling mismatch is one): the check saw the wrong value.
An item pytest reports as a declared xfail (an open defect a rulings row pins)
counts as neither a pass nor a failure, and an item a python marker keeps for a
newer Python than the replay's is dropped, since it cannot run there. Any other
failure (DriveUnsupported, a refused config, an ImportError, a KeyError on an
older schema) is `not replayable`, and the row needs an API probe; its ledger
note says so. A check that passes on its before commit
catches nothing and is refused. The fix commit must pass. `run --record` keeps a
refused row pending, with the refusal and its date in the note, since a refused
replay is no evidence.

The ledger row carries a digest over the check's file, its static import
closure under tests/ and tools/, and the data files of its packet, taken in
repo path order with case so every OS computes the same one, so a replay
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
import ast
from dataclasses import dataclass, replace
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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
BUG_COLUMNS = ("id", "fix_commits", "before_commit", "packet", "test", "probe", "env", "method",
               "platform", "replay", "calc", "symptom")
LEDGER_COLUMNS = ("id", "test", "before_commit", "fix_commit", "lizard", "before",
                  "failure_class", "before_evidence", "fix", "fix_evidence", "digest",
                  "replayed", "note")
LIZARD = "1.24.0"  # every commit on main was built against it (pyproject's comment)
# The test runner requirements-push.txt pins: crapkit's own children start it (doctor's
# coverage probe, test-scoped's pytest), and in-process checks find it in the dev venv.
RUNNER = ("pytest==9.1.1", "pytest-cov==7.1.0", "coverage==7.16.1")
OUTCOMES_ENV = "CRAPKIT_RETRO_OUTCOMES"
BUNDLE_ENV = "CRAPKIT_RETRO_BUNDLE"
PYTHON_ENV = "CRAPKIT_ACCURACY_PYTHON"
# The commit's checkout: a check that runs files beside the package (action.yml,
# tools/action/comment.py) reads them there, since a wheel install carries only src/.
CHECKOUT_ENV = "CRAPKIT_ACCURACY_CHECKOUT"
COLLECT_ALL_ENV = "CRAPKIT_ACCURACY_COLLECT_ALL"
# The switches a bugs.tsv env cell may set for its check: the analysis-oracles packet
# cuts its file set to the languages an older crapkit read, and names the root scope
# by the tree's top-level entries where an older crapkit refused "." (analysis_inventory).
SWITCHES = ("CRAPKIT_ACCURACY_LANGUAGES", "CRAPKIT_ACCURACY_ROOT_PATHS")
DATA_DIRS = ("fixtures", "probes", "recorded", "small", "goldens", "known_kill")
PLATFORMS = {"any": "", "windows": "win32", "linux": "linux", "macos": "darwin"}
WINDOWS = os.name == "nt"


class RetroError(ValueError):
    """A table, commit or argument this tool cannot use."""


def _read(path: Path) -> str:
    """A file's text; every file this tool reads is UTF-8."""
    return path.read_bytes().decode()


def _write(path: Path, text: str) -> None:
    """Write UTF-8 text with the newlines as given, on every OS. The text goes to a
    sibling file first and replaces the table in one step, so a write that fails
    halfway (a full disk emptied the ledger once) leaves the old table whole."""
    partial = path.with_name(path.name + ".partial")
    try:
        partial.write_bytes(text.encode())
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def _text(raw: bytes) -> str:
    """A child's output; a byte that is not UTF-8 reads as U+FFFD, never an error."""
    return raw.decode(errors="replace")


def _tabbed(line: str) -> list[str]:
    """A table line's cells: tabs separate them, and a cell may hold spaces."""
    return line.split("\t")


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
    if not lines or tuple(_tabbed(lines[0])) != columns:
        raise RetroError(f"{path}: the header must be {' '.join(columns)} (tab-separated)")


def _cells(path: Path, number: int, line: str, columns: tuple) -> dict:
    cells = _tabbed(line)
    if len(cells) != len(columns):
        raise RetroError(f"{path}:{number}: {len(cells)} cells, the header has {len(columns)}")
    return dict(zip(columns, cells))


def write_table(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    body = ["\t".join(columns)] + ["\t".join(_cell(row[c]) for c in columns) for row in rows]
    _write(path, "\n".join(body) + "\n")


# A replay's evidence quotes the paths it failed on. The tables name those paths by
# role, never by the user, drive or temp directory of the machine that replayed. The
# folders under the work directory are the commit's worktree (its 12-hex name) and
# its venvs (`<sha>-venv-...`), wherever CRAPKIT_RETRO_WORK put them.
LOCAL_PATHS = ((re.compile(r"""(?:[A-Za-z]:)?(?:[\\/]{1,2}[^\s'"\\/]+)+?"""
                           r"""(?=[\\/]{1,2}[0-9a-f]{12}(?:-venv-[\w.-]+)?[\\/])"""), "<work>"),
               (re.compile(r"""[^\s'"]*pytest-of-[^\s'"\\/]+[\\/]+pytest-\d+"""), "<tmp>"),
               (re.compile(r"""(?:[A-Za-z]:)?[\\/]+(?:Users|home)[\\/]+[^\s'"\\/]+"""),
                "<home>"))


def _cell(value: str) -> str:
    """One table cell: on one line, with no local path in it."""
    text = str(value)
    for pattern, role in LOCAL_PATHS:
        text = pattern.sub(role, text)
    return " ".join(text.split())


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

_RAISED: dict[tuple[str, str], dict] = {}  # (nodeid, phase): the record, until pytest reports it


def pytest_collection_modifyitems(config, items):
    """Loaded with `-p retro`: COLLECT_ALL_ENV keeps every item, python markers
    included, so an item marked for a newer Python than the one running would run
    and fail on a grammar this Python lacks. The replay drops it: it cannot run
    here, so its failure says nothing about the commit."""
    dropped = _too_new(items) if os.environ.get(OUTCOMES_ENV) else []
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = [item for item in items if item not in dropped]


def _too_new(items: list) -> list:
    """The items a python marker keeps for a newer Python than this one."""
    from accuracy.kit import tiers
    return [item for item in items
            if not tiers.runs_on_python(tuple(mark.args) for mark in item.iter_markers("python"))]


def pytest_runtest_makereport(item, call):
    """Loaded with `-p retro`: keep each failing or passing phase's record. The
    exception is raw here: pytest has not yet turned a declared xfail into one."""
    if os.environ.get(OUTCOMES_ENV) and (call.when == "call" or call.excinfo is not None):
        _RAISED[(item.nodeid, call.when)] = _record(item.nodeid, call)
    return None


def pytest_runtest_logreport(report):
    """Append the phase's record once pytest has judged it: a strict xfail that failed
    as declared (an open defect a rulings row pins) is skipped, not failed."""
    record = _RAISED.pop((report.nodeid, report.when), None)
    if record is None:
        return
    if hasattr(report, "wasxfail"):
        record = {**record, "outcome": "skipped"}
    with open(os.environ[OUTCOMES_ENV], "ab") as handle:
        handle.write((json.dumps(record) + "\n").encode())


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
    """A file under one of the packet's data folders. Bytecode under __pycache__ is
    not: Python writes it wherever a module is imported, and no checkout holds it."""
    folders = set(path.relative_to(packet).parts[:-1])
    return path.is_file() and "__pycache__" not in folders and bool(set(DATA_DIRS) & folders)


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
    data files, ordered by their repo path with its case, as Linux orders them:
    Windows orders paths without case, which gave a check another digest there."""
    test_file = (repo / test.split("::")[0]).resolve()
    files = (_closure_files(test_file, repo) | _package_inits(test_file, repo)
             | _packet_data(test_file, repo))
    if probe:
        files.add((repo / RETRO.relative_to(REPO) / "probes" / probe).resolve())
    root = repo.resolve()
    return sorted(files, key=lambda path: path.relative_to(root).parts)


def digest(test: str, probe: str = "", repo: Path = REPO, env: str = "") -> str:
    """16 hex of a sha256 over every check file's repo path and bytes, and the row's
    env when it has one."""
    root = repo.resolve()
    hashed = hashlib.sha256(f"env\0{env}\0".encode() if env else b"")
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
    packages: tuple = (f"lizard=={LIZARD}", *RUNNER)


def _run(argv: list, cwd: Path = REPO, env: dict | None = None) -> subprocess.CompletedProcess:
    """argv run to the end, its output decoded as UTF-8 with the newlines it wrote."""
    done = subprocess.run([str(part) for part in argv], cwd=cwd, env=env, capture_output=True)
    done.stdout, done.stderr = _text(done.stdout), _text(done.stderr)
    return done


def _checked(argv: list, cwd: Path = REPO) -> str:
    done = _run(argv, cwd)
    if done.returncode != 0:
        raise RetroError(f"{' '.join(map(str, argv))}: {done.stderr.strip()[-400:]}")
    return done.stdout


def have_commit(sha: str, repo: Path = REPO) -> bool:
    return _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo).returncode == 0


def fetch_bundle(sha: str, repo: Path = REPO) -> None:
    bundle = os.environ.get(BUNDLE_ENV)
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
    return venv / ("Scripts/python.exe" if WINDOWS else "bin/python")


CURRENT = f"{sys.version_info.major}.{sys.version_info.minor}"


def _create_venv(venv: Path, python: str) -> None:
    """The standard library's venv for this interpreter's version; uv for another."""
    if python == CURRENT:
        venv_module.EnvBuilder(symlinks=not WINDOWS).create(venv)
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


BUILT = "retro-built"  # written last: the packages a venv finished installing, one per line


def _built(venv: Path, manifest: str) -> bool:
    marker = venv / BUILT
    return marker.is_file() and _read(marker) == manifest


def build_venv(tree: Path, python: str, site: Site = Site(), extra: tuple = ()) -> Path:
    """A venv beside the worktree holding its crapkit, the site's packages and `extra`.
    A venv whose BUILT marker is missing (a failed or killed install) or names other
    packages is removed and built again."""
    venv = tree.parent / f"{tree.name}-venv-{python}-{site.install}"
    interpreter = venv_python(venv)
    packages = [*site.packages, *extra]
    manifest = "".join(f"{package}\n" for package in packages)
    if not _built(venv, manifest):
        shutil.rmtree(venv, ignore_errors=True)
        _create_venv(venv, python)
        _install(interpreter, tree, site.install)
        _packages(interpreter, packages)
        venv.mkdir(parents=True, exist_ok=True)
        _write(venv / BUILT, manifest)
    return interpreter


# --- replaying one check -------------------------------------------------------------------------------

def _holds_crapkit(entry: str) -> bool:
    return (Path(entry) / "crapkit" / "__init__.py").is_file()


def _inherited_paths() -> list[str]:
    """The caller's PYTHONPATH minus any entry holding a crapkit package: the
    spawned old crapkit inherits it, and this tree's src/ would shadow the commit's."""
    entries = os.environ.get("PYTHONPATH", "").split(os.pathsep)
    return [entry for entry in entries if entry and not _holds_crapkit(entry)]


ROOT_CODE = "import crapkit, pathlib; print(pathlib.Path(crapkit.__file__).resolve().parents[1])"


def crapkit_root(interpreter: Path) -> str:
    """The directory `interpreter` imports crapkit from, asked without this tree's src/."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(_inherited_paths())}
    done = _run([interpreter, "-c", ROOT_CODE], env=env)
    if done.returncode != 0:
        raise RetroError(f"{interpreter} cannot import crapkit: {done.stderr.strip()[-400:]}")
    return done.stdout.strip()


ALONE = "retro-crapkit"  # beside a venv's site-packages: its crapkit and nothing else


def import_root(interpreter: Path) -> str:
    """The directory the check's PYTHONPATH takes the commit's crapkit from. A wheel
    puts crapkit in site-packages beside pytest, pytest-cov and lizard, and that
    folder on PYTHONPATH reaches every python the check starts: a bare venv then
    imports pytest_cov, and init prints no install note. So the check gets a copy
    of crapkit and its dist-info in a folder of their own."""
    root = Path(crapkit_root(interpreter))
    return str(_alone(root)) if root.name == "site-packages" else str(root)


def _alone(site_packages: Path) -> Path:
    """crapkit and its dist-info copied out of `site-packages`, once per venv."""
    alone = site_packages.parent / ALONE
    if not (alone / "crapkit").is_dir():
        staging = alone.with_name(ALONE + ".partial")
        shutil.rmtree(staging, ignore_errors=True)
        for entry in [site_packages / "crapkit", *site_packages.glob("crapkit-*.dist-info")]:
            shutil.copytree(entry, staging / entry.name)
        shutil.rmtree(alone, ignore_errors=True)
        os.replace(staging, alone)
    return alone


def _switch(word: str) -> tuple[str, str]:
    name, equals, value = word.partition("=")
    if not equals or name not in SWITCHES:
        raise RetroError(f"a bugs.tsv env names {name}; it may set only "
                         f"{', '.join(SWITCHES)}, each as NAME=value")
    return name, value


def switches(env: str) -> dict[str, str]:
    """A bugs.tsv env cell, `NAME=value` words, as the variables its replay sets."""
    return dict(map(_switch, env.split()))


def _pytest_env(interpreter: Path, outcomes: Path, root: str = "", env: str = "",
                tree: Path | None = None) -> dict:
    paths = [root, str(REPO / "tests"), str(REPO / "tools" / "accuracy"), *_inherited_paths()]
    checkout = {CHECKOUT_ENV: str(tree)} if tree else {}
    return {**os.environ, **switches(env), **checkout, PYTHON_ENV: str(interpreter),
            OUTCOMES_ENV: str(outcomes), COLLECT_ALL_ENV: "1", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join(filter(None, paths))}


def _rootdir(test: str) -> Path:
    """This tree for a check in it; a check file elsewhere is its own root, so pytest
    never walks the directories above it."""
    path = (REPO / test.split("::")[0]).resolve()
    return REPO if path == REPO or REPO in path.parents else path.parent


def replay_node(test: str, interpreter: Path, env: str = "", tree: Path | None = None) -> list[dict]:
    """Run one node id of this tree against `interpreter`'s crapkit, under the row's
    env switches, with `tree`, the commit's checkout, named in CHECKOUT_ENV; one
    record per item, and one more when the child ended before it reported them all."""
    with tempfile.TemporaryDirectory(prefix="crapkit-retro-") as scratch:
        outcomes = Path(scratch) / "outcomes.jsonl"
        outcomes.touch()
        argv = [sys.executable, "-m", "pytest", test, "-q", "-p", "no:cacheprovider",
                "-p", "no:randomly", "-p", "retro", "--rootdir", _rootdir(test)]
        done = _run(argv, env=_pytest_env(interpreter, outcomes, import_root(interpreter), env, tree))
        records = item_outcomes(_read(outcomes).splitlines())
    return records + _died(done.returncode, records)


def _unfinished(code: int, records: list[dict]) -> bool:
    """A child that ended before it reported every item: a signal, an exit no
    finished session gives, or exit 1 with no item failed."""
    return code not in (0, 5) and not (code == 1 and _failed(records))


def _died(code: int, records: list[dict]) -> list[dict]:
    """The record of a child that ended mid-item, as a crapkit call stuck in C code
    ends it with exit 1 (tests/e2e/cli_in_process.py) and a crash by a signal: the
    item's passing setup was its only record, and the replay read as a pass."""
    if not _unfinished(code, records):
        return []
    ending = f"exit {code}" if code > 0 else f"signal {-code}"
    return [{"nodeid": "(session)", "outcome": "failed", "exc_type": "SessionDied",
             "assertion": False, "message": f"pytest ended with {ending} before it reported every item"}]


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
    kind = tail[0].partition(":")[0].rpartition(".")[2]
    return [{"nodeid": probe.name, "outcome": "failed", "exc_type": kind,
             "assertion": kind == "AssertionError", "message": _cell(tail[0])[:300]}]


@dataclass(frozen=True)
class Bug:
    id: str
    test: str
    before: str
    fix: str
    probe: str = ""
    env: str = ""


def bug_of(row: dict) -> Bug:
    fixes = [sha.strip() for sha in row["fix_commits"].split(",") if sha.strip()]
    return Bug(row["id"], row["test"], row["before_commit"], fixes[-1] if fixes else "",
               row["probe"], row.get("env", ""))


def _probe_records(probe: Path, tree: Path, python: str, site: Site) -> list[dict]:
    requires, editable = probe_header(probe)
    probe_site = replace(site, install="editable") if editable else site
    return replay_probe(probe, build_venv(tree, python, probe_site, tuple(requires)), tree)


def _records(bug: Bug, sha: str, python: str, site: Site) -> list[dict]:
    tree = worktree(sha, site)
    if bug.probe:
        return _probe_records(RETRO / "probes" / bug.probe, tree, python, site)
    return replay_node(bug.test, build_venv(tree, python, site), bug.env, tree)


def replay(bug: Bug, python: str, site: Site = Site()) -> tuple[Outcome, Outcome]:
    before = classify_before(_records(bug, bug.before, python, site))
    fix = classify_fix(_records(bug, bug.fix, python, site))
    return before, fix


def ledger_row(bug: Bug, before: Outcome, fix: Outcome, note: str = "") -> dict:
    today = datetime.date.today().isoformat()
    return {"id": bug.id, "test": bug.test, "before_commit": bug.before, "fix_commit": bug.fix,
            "lizard": LIZARD, "before": before.verdict, "failure_class": before.failure_class,
            "before_evidence": before.evidence, "fix": fix.verdict,
            "fix_evidence": fix.evidence, "digest": digest(bug.test, bug.probe, env=bug.env),
            "replayed": today, "note": note}


# --- choosing rows -------------------------------------------------------------------------------------

def _defines(path: Path, name: str) -> bool:
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    return any(isinstance(node, functions) and node.name == name
               for node in ast.walk(ast.parse(path.read_bytes())))


def check_exists(row: dict, repo: Path = REPO) -> bool:
    """Whether this tree holds the row's check yet: its packet may not have landed,
    or landed without this test. A parametrized id (`test_x[case]`) names its
    function without the case. A check that is not there yet never replays: its
    node id would fail at the fix commit, and every nightly would report it."""
    path, _, node = row["test"].partition("::")
    file = repo / path
    return file.is_file() and _defines(file, node.split("::")[-1].partition("[")[0])


def replayable_here(row: dict, platform: str = sys.platform) -> bool:
    """An open bug has no fix to replay, a platform row runs on its platform only,
    and a check this tree does not hold yet cannot run."""
    wanted = PLATFORMS.get(row["platform"], row["platform"])
    on_platform = not wanted or platform.startswith(wanted)
    return row["replay"] != "open" and on_platform and check_exists(row)


def _is_stale(row: dict, ledger: dict) -> bool:
    recorded = ledger.get(row_key(row))
    bug = bug_of(row)
    return recorded is None or recorded["digest"] != digest(bug.test, bug.probe, env=bug.env)


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
        header = _tabbed(lines[0])
        tables[path.parent.name] = [dict(zip(header, _tabbed(line))) for line in lines[1:]]
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


PLATFORM_SYNONYMS = {"all": "any"}  # several packets' retro.tsv spell every platform `all`


def _platform(listed: dict) -> str:
    named = listed.get("platform") or ""
    return PLATFORM_SYNONYMS.get(named, named)


def _bug_for(bugs: list[dict], packet: str, listed: dict) -> dict:
    """A packet's pair as a bugs.tsv row. The probe and env stay with the pair bugs.tsv
    already held; an env column in the packet's retro.tsv names the env itself."""
    base = _template(bugs, packet, listed["id"])
    same = base["packet"] == packet and base["test"] == listed["test"]
    kept = {"probe": base["probe"], "env": base.get("env", "")} if same else {"probe": "", "env": ""}
    return {**base, **kept, "packet": packet, "test": listed["test"],
            "env": listed.get("env", kept["env"]), "platform": _platform(listed) or base["platform"]}


def synced_bugs(bugs: list[dict], tables: dict[str, list[dict]]) -> list[dict]:
    """bugs.tsv with each landed packet's rows replaced by the pairs its retro.tsv lists.
    A pair bugs.tsv already held keeps its place; a new one follows its bug's rows. A bug
    no landed packet lists keeps its proposed rows, so a triaged fix never loses its last row.
    A packet that lists one pair once per fix commit gives it one row: the ledger keys on it."""
    fresh = [_bug_for(bugs, packet, listed) for packet, rows in tables.items() for listed in rows]
    unique = {row_key(row): row for row in reversed(fresh)}
    return sorted(_unsynced(bugs, tables) + list(unique.values()), key=_placed(bugs))


def _unsynced(bugs: list[dict], tables: dict[str, list[dict]]) -> list[dict]:
    """The rows sync keeps as they are: a packet that has not landed, or a bug no landed
    packet lists."""
    listed = _listed_ids(tables)
    return [row for row in bugs if row["packet"] not in tables or row["id"] not in listed]


def _listed_ids(tables: dict[str, list[dict]]) -> set[str]:
    return {row["id"] for rows in tables.values() for row in rows}


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
    return problem, recorded_row(row, before, fix, problem)


PROBE_NOTE = "not replayable before the fix: the row needs an API-level probe under retro/probes/"


def recorded_row(row: dict, before: Outcome, fix: Outcome, problem: str) -> dict:
    """The ledger row a replay leaves. A refused replay is no evidence, so the row
    stays pending and its note says why; a not-replayable before names the probe it needs."""
    if problem:
        return {**_waiting(row), "note": f"refused {datetime.date.today().isoformat()}: {problem}"}
    note = PROBE_NOTE if before.verdict == "not replayable" else ""
    return ledger_row(bug_of(row), before, fix, note=note)


def _replay_rows(rows: list[dict], ledger: dict, python: str, record: bool = False) -> int:
    """Replay each row, print what contradicts the ledger, and rewrite the ledger
    only when asked: nightly and release judge, `run --record` records."""
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


def _names_its_platform(row: dict) -> bool:
    """A row that replays on one OS only: that OS's nightly cell takes it, since
    the Linux retro job replays every `any` row."""
    return row["platform"] != "any"


def _nightly(args) -> int:
    bugs, ledger = _load()
    public = _public(bugs)
    if args.platform_only:
        public = list(filter(_names_its_platform, public))
    day = datetime.date.today().toordinal() if args.day is None else args.day
    chosen = _union(stale(public, ledger), weekly_slice(public, args.slice_of, day))
    return _replay_rows(chosen, ledger, args.python)


def _release(args) -> int:
    bugs, ledger = _load()
    rows = [row for row in bugs if replayable_here(row)]
    chosen = _union(stale(rows, ledger), [row for row in rows if _bundle(row)])
    return _replay_rows(chosen, ledger, args.python)


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
    nightly.add_argument("--platform-only", action="store_true",
                         help="replay only the rows whose platform is this OS (a Windows or "
                              "macOS cell); the Linux job replays the `any` rows")
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
