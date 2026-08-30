"""The one way tests/e2e spawns the CLI, and the git lines every repo fixture needs.

AGENTS.md fixes the contract these tests run under: tests/e2e drives
`python -m crapkit` against a real git repo in a tmp dir and asserts through the
CLI only. Before this file, 42 copies of that four-line `subprocess.run` lived in
the test files, 23 of them different, and the differences were invisible: a file
that decoded the child as UTF-8 sat next to one that took the platform default,
with nothing to say which was deliberate.

`run_cli` keeps every one of those differences, but as a named argument. A file
binds its own contract once, at the top, with `cli_runner`:

    run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace",
                         env_extra={"CRAPKIT_OVERRIDE_REASON": None})

so what that file needs from the child process is one readable line instead of a
body to diff against 41 others. Nothing here is a policy: the defaults are the
plainest child (120 s, platform decoding, the inherited environment), and a file
that needs otherwise says so.

The child inherits PYTHONPATH. That is what makes the suite test the working
tree rather than an installed crapkit, so no default here may drop it.
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
from pathlib import Path

CRAPKIT = [sys.executable, "-m", "crapkit"]


def child_env(env_extra: dict | None = None) -> dict:
    """The parent environment with `env_extra` applied. A None value removes the
    key, which is how a test drops an inherited grant (CRAPKIT_OVERRIDE_REASON)
    it must not be judged under."""
    env = dict(os.environ)
    for key, value in (env_extra or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def run_cli(repo: Path, *args: str, timeout: int = 120, env_extra: dict | None = None,
            encoding: str | None = None, errors: str | None = None,
            stdin: str | None = None) -> subprocess.CompletedProcess:
    """`python -m crapkit <args>` in `repo`, captured as text."""
    return subprocess.run([*CRAPKIT, *args], cwd=repo, input=stdin,
                          capture_output=True, text=True, encoding=encoding,
                          errors=errors, timeout=timeout, env=child_env(env_extra))


def cli_runner(**contract):
    """A `run_cli` with this file's contract bound. A call site may still
    override any of it, which is what a one-off env or stdin is."""
    return functools.partial(run_cli, **contract)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def git_init_repo(repo: Path) -> Path:
    """An empty repo on `main`, whatever the machine's init.defaultBranch says."""
    git(repo, "init", "-q", "-b", "main")
    return repo


def git_commit_all(repo: Path, message: str) -> None:
    """Commit the whole tree under a test identity, so no global git config is
    required to run the suite."""
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)
