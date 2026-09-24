"""Git repositories built from a spec, and the one lane command form.

A spec is a list of steps: Commit, Branch, Checkout and Merge. Every commit
carries fixed author and committer dates and a fixed identity, so two builds of
one spec have the same commit ids on every OS. A repo sets core.autocrlf false,
core.eol lf and core.quotePath true: the bytes a spec writes are the bytes git
stores, and a non-ASCII path comes back C-quoted as it would on a stock git.
`root` puts crapkit.toml below the git top.

A spec is built once into a template directory, and each test gets a copy:
copying is the reset between tests.

A lane in a kit repo runs copy_command(): `python -c` copying each recorded
artifact into place and then stamping it with os.utime(ns=(t, t)) at
t = time.time_ns(). A plain copy can keep the previous file's mtime on a
filesystem with a coarse clock, and crapkit refuses an artifact whose mtime
did not move (lanes.py _unwritten).
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

import hang_guard
from . import tiers

IDENTITY = ("A U Thor", "author@example.com")
EPOCH = 1_750_000_000  # 2025-06-15T15:06:40Z: the default date of a kit commit
CONFIG = (("core.autocrlf", "false"), ("core.eol", "lf"), ("core.quotePath", "true"),
          ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("init.defaultBranch", "main"),
          ("user.name", IDENTITY[0]), ("user.email", IDENTITY[1]))
# The suite's own subprocess coverage must not measure a lane child.
LANE_ENV = '{ COVERAGE_PROCESS_CONFIG = "", COV_CORE_DATAFILE = "" }'


@dataclass(frozen=True)
class Commit:
    """Writes `files` (str or bytes; None deletes), applies `renames` with git
    mv first, and commits at `date` (epoch seconds, UTC)."""
    files: dict = field(default_factory=dict)
    message: str = "change"
    date: int = EPOCH
    author: tuple = IDENTITY
    renames: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Branch:
    name: str


@dataclass(frozen=True)
class Checkout:
    name: str


@dataclass(frozen=True)
class Merge:
    branch: str
    message: str = "merge"
    date: int = EPOCH


@dataclass(frozen=True)
class Spec:
    steps: tuple = ()
    root: str = ""


@dataclass(frozen=True)
class Built:
    top: Path
    root: Path


def git(top: Path, *args: str, date: int | None = None, author: tuple = IDENTITY) -> str:
    """One git command in `top`, with dates pinned when given."""
    tiers.require_process("git")
    env = {**os.environ, "GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
           "GIT_COMMITTER_NAME": author[0], "GIT_COMMITTER_EMAIL": author[1]}
    if date is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{date} +0000"
    done = hang_guard.run(["git", *args], cwd=top, env=env, text=True, encoding="utf-8",
                          errors="replace")
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} exited {done.returncode}: {done.stderr}")
    return done.stdout


def _write(top: Path, path: str, content) -> None:
    target = top / path
    if content is None:
        git(top, "rm", "-q", "--", path)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    target.write_bytes(data)
    git(top, "add", "--", path)


def _commit(top: Path, step: Commit) -> None:
    for old, new in step.renames.items():
        (top / new).parent.mkdir(parents=True, exist_ok=True)
        git(top, "mv", "--", old, new)
    for path, content in step.files.items():
        _write(top, path, content)
    git(top, "commit", "-q", "--allow-empty", "-m", step.message, date=step.date,
        author=step.author)


def _merge(top: Path, step: Merge) -> None:
    git(top, "merge", "-q", "--no-ff", "-m", step.message, step.branch, date=step.date)


_APPLY = {
    Commit: _commit,
    Branch: lambda top, step: git(top, "checkout", "-q", "-b", step.name),
    Checkout: lambda top, step: git(top, "checkout", "-q", step.name),
    Merge: _merge,
}


def build(spec: Spec, top: Path) -> Built:
    """Build the spec's repo at `top`, which must not exist yet."""
    top.mkdir(parents=True)
    git(top, "init", "-q", "-b", "main")
    for key, value in CONFIG:
        git(top, "config", key, value)
    for step in spec.steps:
        _APPLY[type(step)](top, step)
    return Built(top, top / spec.root if spec.root else top)


def _plain(value):
    if isinstance(value, bytes):
        return {"bytes": base64.b64encode(value).decode("ascii")}
    return value


def digest(spec: Spec) -> str:
    """A key that changes when any byte, date or step of the spec changes."""
    steps = [[type(step).__name__, {key: _plain_tree(value) for key, value in asdict(step).items()}]
             for step in spec.steps]
    text = json.dumps([spec.root, steps], sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:16]


def _plain_tree(value):
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return _plain(value)


class Templates:
    """Each spec built once under `base`; every caller gets a fresh copy."""

    def __init__(self, base: Path | None = None):
        self.base = base or Path(tempfile.mkdtemp(prefix="crapkit-accuracy-repos-"))

    def template(self, spec: Spec) -> Path:
        path = self.base / digest(spec)
        if not path.exists():
            staging = self.base / f"{path.name}.building"
            shutil.rmtree(staging, ignore_errors=True)
            build(spec, staging)
            staging.rename(path)
        return path

    def copy(self, spec: Spec, dest: Path) -> Built:
        shutil.copytree(self.template(spec), dest, symlinks=True)
        return Built(dest, dest / spec.root if spec.root else dest)


_COPY = ("import os,shutil,sys,time;a=sys.argv[1:];p=list(zip(a[::2],a[1::2]));"
         "[os.makedirs(os.path.dirname(d) or '.',exist_ok=True) for s,d in p];"
         "[shutil.copyfile(s,d) for s,d in p];t=time.time_ns();"
         "[os.utime(d,ns=(t,t)) for s,d in p]")


def copy_command(*pairs: tuple[str, str]) -> str:
    """The lane command that copies each (recorded, artifact) pair and stamps it.

    A bare `python`, which the lane's shell finds first on the PATH kit.drive
    hands it: the driving interpreter. Paths are repo-relative and hold no
    space or quote, so one spelling reads the same under sh and cmd.exe."""
    words = [word for pair in pairs for word in pair]
    return f'python -c "{_COPY}" ' + " ".join(words)


def lane_toml(name: str, artifact: str, parser: str, scopes: list[str], recorded: str,
              results: tuple[str, str] | None = None) -> str:
    """A [[lane]] table whose command copies recorded artifacts into place.
    `results` is (recorded JUnit, results_artifact) when the lane reports tests."""
    pairs = [(recorded, artifact)] + ([results] if results else [])
    command = copy_command(*pairs).replace('"', '\\"')
    lines = [f'[[lane]]\nname = "{name}"\ncommand = "{command}"\nartifact = "{artifact}"',
             f'parser = "{parser}"\nscopes = {json.dumps(scopes)}\nenv = {LANE_ENV}']
    if results:
        lines.append(f'results_artifact = "{results[1]}"')
    return "\n".join(lines) + "\n"


def tree(directory: Path) -> dict:
    """Every file under `directory` as {posix path: bytes}, for a Commit."""
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    return {path.relative_to(directory).as_posix(): path.read_bytes() for path in files}


def tree_spec(directory: Path, date: int = EPOCH, root: str = "") -> Spec:
    """A one-commit spec holding every file under `directory`."""
    return Spec(steps=(Commit(files=tree(directory), message="seed", date=date),), root=root)
