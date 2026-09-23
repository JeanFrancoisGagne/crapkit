"""The one way tests/e2e runs the CLI, and the git lines every repo fixture needs.

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
body to diff against 41 others. The defaults are the plainest child (platform
decoding, the inherited environment) with one policy: a run that names no timeout
waits the suite's hang bound (tests/hang_guard.py), and a miss kills the child and
fails with what it printed. Two files once bound the CLI at 30 s, and a loaded
machine failed three of their tests on a correct tree. A file may still name a
longer bound for a run that does real work.

The child inherits the parent's package selection. Development can select the
working tree with PYTHONPATH; isolated CI selects its verified wheel through
the environment's interpreter. No default here may replace that choice.

A fixture lane spells a bare `python`, which its shell finds through PATH, so the
suite's own interpreter directory goes first on the child's PATH. With another
project's virtualenv first, each nested pytest loaded that environment's
plugins, and one e2e file spent twice the CPU.

The call itself runs in this process unless the file asks otherwise:
cli_in_process.py calls `crapkit.cli.main` with the child's cwd, environment,
stdio and argv in place and puts the worker back afterwards, which saves an
interpreter start per call. `spawn=True` starts the real child, and a file that
tests the process boundary binds it in its `cli_runner` line (AGENTS.md lists
them). `mcp` always spawns: the server reads a real stdin descriptor.
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
from pathlib import Path

import hang_guard
import pytest

CRAPKIT = [sys.executable, "-m", "crapkit"]


def child_env(env_extra: dict | None = None) -> dict:
    """The parent environment with the suite's interpreter directory first on
    PATH, then `env_extra` applied. A None value removes the key, which is how a
    test drops an inherited grant (CRAPKIT_OVERRIDE_REASON) it must not be
    judged under."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(filter(None, (str(Path(sys.executable).parent), env.get("PATH"))))
    for key, value in (env_extra or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def run_cli(repo: Path, *args: str, timeout: float | None = None, env_extra: dict | None = None,
            encoding: str | None = None, errors: str | None = None,
            stdin: str | None = None, spawn: bool = False) -> subprocess.CompletedProcess:
    """`python -m crapkit <args>` in `repo`, captured as text, under the hang
    bound unless `timeout` names another."""
    from cli_in_process import fits, run as in_process
    if fits(args, spawn):
        return in_process(repo, args, env=child_env(env_extra), stdin=stdin,
                          encoding=encoding, errors=errors, timeout=timeout)
    if args and args[0] == 'mcp' and stdin is not None:
        from mcp_stdio import run
        bound = hang_guard.HANG_SECONDS if timeout is None else timeout
        return run([*CRAPKIT, *args], cwd=repo, frames=stdin, env=child_env(env_extra),
                   timeout=bound, encoding=encoding, errors=errors)
    return hang_guard.run([*CRAPKIT, *args], cwd=repo, input=stdin, text=True,
                          encoding=encoding, errors=errors, timeout=timeout,
                          env=child_env(env_extra))


def cli_runner(**contract):
    """A `run_cli` with this file's contract bound. A call site may still
    override any of it, which is what a one-off env or stdin is."""
    return functools.partial(run_cli, **contract)


@pytest.fixture(scope="session", autouse=True)
def in_process_hang_log(tmp_path_factory):
    """Where an in-process call stuck in C code past its bound leaves every
    thread's stack before the worker exits: in-process-hangs.log under the
    worker's basetemp (popen-gw<N> under xdist), outside pytest's capture."""
    from cli_in_process import log_hangs_to
    path = tmp_path_factory.getbasetemp() / "in-process-hangs.log"
    with open(path, "a", encoding="utf-8") as log:
        log_hangs_to(log)
        yield path
    log_hangs_to(sys.__stderr__)


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
