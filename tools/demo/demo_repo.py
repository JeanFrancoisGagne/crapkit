"""Build the throwaway git repo the demo runs against.

The committed fixture under `fixture/` is the final tree and carries no `.git`,
so every run starts from nothing. `history/` holds the earlier version of the
two files that change twice; replaying the stages gives the worklist real churn
to rank on, which a single-commit repo cannot (every weight is 0.00 there).

Identity and dates are fixed because the commit sha reaches the frames: the
demo prints `run 1 @ <sha>` and two runs have to print the same one.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixture"
HISTORY = HERE / "history"

AUTHOR_NAME = "Dana Reyes"
AUTHOR_EMAIL = "dana@example.invalid"

# Runner droppings and the store: copying them into the repo would commit them.
_SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".crapkit", ".coverage")


def stages() -> list[dict]:
    """The commit plan, oldest first. An empty `dir` means the fixture itself."""
    return json.loads((HISTORY / "stages.json").read_text(encoding="utf-8"))


def _source(stage: dict) -> Path:
    return FIXTURE if not stage["dir"] else HISTORY / stage["dir"]


def _env(date: str) -> dict:
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = AUTHOR_NAME
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = AUTHOR_EMAIL
    env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    return env


def git(repo: Path, *args: str, date: str = "") -> subprocess.CompletedProcess:
    """One git call, always with the fixed identity behind it."""
    return subprocess.run(("git", *args), cwd=repo, env=_env(date), check=True,
                          capture_output=True, text=True)


def _commit(repo: Path, stage: dict) -> None:
    git(repo, "add", "-A", date=stage["date"])
    git(repo, "commit", "-q", "-m", stage["message"], date=stage["date"])


def build(repo: Path) -> Path:
    """A git repo holding the fixture and its history. Returns the repo path."""
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    # cmd.exe and Git Bash disagree about core.autocrlf, and a repo that rewrites
    # line endings prints a warning per file on every `git add`, into the frames.
    git(repo, "config", "core.autocrlf", "false")
    for stage in stages():
        shutil.copytree(_source(stage), repo, dirs_exist_ok=True, ignore=_SKIP)
        _commit(repo, stage)
    return repo
