"""Run crapkit the way a user or an agent does, and read what it wrote.

Driver(root) runs a CLI command at `root` and hands back its exit code and both
streams. With CRAPKIT_ACCURACY_PYTHON unset the call runs in this process
through tests/e2e/cli_in_process.py, which rebuilds a child's cwd, environment
and stdio around crapkit.cli.main; `spawn=True`, a set CRAPKIT_ACCURACY_PYTHON
(the retro replay, the wheel diff, the release gate) and `mcp` start a real
interpreter instead.

`date_now` sets GIT_TEST_DATE_NOW for the call, which is the clock git reads
for a relative `--since`. crapkit 0.8.0 and older cut the churn window at
`--since=12.months.ago`, so a golden, xplat, wheel-diff or retro run passes the
epoch corpus.toml names and the window of such a release cannot move with the
calendar. Later crapkit ends the window at HEAD's commit date and never reads
git's clock for it.

A usage error or an unknown command raises DriveUnsupported: a replay against
an older crapkit that lacks the command is `not replayable`, never red.

The store is read with sqlite3 and never through crapkit's store.py, so a
store bug cannot hide itself from the check that reads it.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

import hang_guard
import mcp_stdio
from . import tiers

PYTHON_ENV = "CRAPKIT_ACCURACY_PYTHON"
TESTS = Path(__file__).resolve().parents[2]
STORE = Path(".crapkit") / "crap.sqlite"
PROTOCOL = "2025-06-18"
_USAGE = re.compile(r"^usage: .*?^\S+: error: ", re.M | re.S)


class DriveUnsupported(RuntimeError):
    """This crapkit cannot run the command as asked: a usage error or no such command."""


@dataclass(frozen=True)
class Result:
    argv: tuple[str, ...]
    code: int
    stdout: str
    stderr: str

    def json(self):
        try:
            return json.loads(self.stdout)
        except ValueError:
            raise AssertionError(f"{' '.join(self.argv)} printed no JSON (exit {self.code}):\n"
                                 f"{self.stdout}\n{self.stderr}") from None


def _in_process_runner():
    """tests/e2e/cli_in_process.py, loaded once under its own module name."""
    loaded = sys.modules.get("cli_in_process")
    if loaded is None:
        spec = importlib.util.spec_from_file_location("cli_in_process",
                                                      TESTS / "e2e" / "cli_in_process.py")
        loaded = importlib.util.module_from_spec(spec)
        sys.modules["cli_in_process"] = loaded
        spec.loader.exec_module(loaded)
    return loaded


def child_env(extra: dict | None = None, python: str = sys.executable) -> dict:
    """The parent environment with the driving interpreter first on PATH, so a
    lane's bare `python` is this one, then `extra` applied (None removes a key)."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(filter(None, (str(Path(python).parent), env.get("PATH"))))
    for key, value in (extra or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def crapkit_argv(python: str, launch: tuple[str, ...], args: tuple[str, ...]) -> list[str]:
    """A spawned crapkit call. `-P` (Python 3.11+) keeps the call's cwd off
    sys.path: the tree under measurement can hold a crapkit/ package, as
    crapkit's own source does when a check measures it, and `-m` would import
    that copy in place of the crapkit under test (under mutmut, a copy whose
    trampolines load mutmut's settings from the tree and stop the child)."""
    return [python, "-P", *launch, "crapkit", *args]


class Driver:
    """`launch` is what a spawned call puts between the interpreter and `crapkit`:
    ("-m",) runs it plainly; ("-m", "coverage", "run", "--rcfile=RC", "-m") runs
    it under coverage.py (kit/reach.py), and implies spawn."""

    def __init__(self, root: Path, *, date_now: int | None = None, spawn: bool = False,
                 env: dict | None = None, launch: tuple[str, ...] = ("-m",)):
        self.root = Path(root)
        self.python = os.environ.get(PYTHON_ENV) or sys.executable
        self.launch = tuple(launch)
        self.spawn = spawn or _must_spawn(self.launch)
        extra = dict(env or {})
        if date_now is not None:
            extra["GIT_TEST_DATE_NOW"] = str(date_now)
        self.env = child_env(extra, self.python)

    def _call(self, args: tuple[str, ...], stdin: str | None):
        tiers.require_process("the crapkit CLI")
        if self.spawn:
            argv = crapkit_argv(self.python, self.launch, args)
            return hang_guard.run(argv, cwd=self.root, env=self.env, input=stdin, text=True,
                                  encoding="utf-8", errors="replace")
        return _in_process_runner().run(self.root, args, env=self.env, stdin=stdin,
                                        encoding="utf-8", errors="replace",
                                        timeout=hang_guard.HANG_SECONDS)

    def spawned_file(self, module: str) -> Path:
        """The file `module` loads from in the interpreter a spawned call starts: a
        retro replay's venv, or inside mutmut's copy the installed tree, where this
        process imported the copy."""
        tiers.require_process("the interpreter crapkit runs under")
        probe = f"import {module}; print({module}.__file__)"
        done = hang_guard.run([self.python, "-P", "-c", probe], cwd=self.root, env=self.env,
                              text=True, encoding="utf-8", errors="replace")
        return Path(done.stdout.strip()).resolve()

    def run(self, *args: str, stdin: str | None = None) -> Result:
        done = self._call(tuple(args), stdin)
        if done.returncode == 2 and _USAGE.search(done.stderr):
            raise DriveUnsupported(f"crapkit {' '.join(args)}: {done.stderr.strip()}")
        return Result(tuple(args), done.returncode, done.stdout, done.stderr)

    def json(self, *args: str):
        """The command's --json object; the exit code is the caller's to check."""
        return self.run(*args, "--json").json()

    def mcp(self, calls: list[tuple[str, dict]]) -> list[dict]:
        """Every call's result from one `crapkit mcp` session, in call order."""
        tiers.require_process("crapkit mcp")
        frames = [_frame(0, "initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                           "clientInfo": {"name": "accuracy-kit",
                                                          "version": "1"}}),
                  json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})]
        frames += [_frame(number, "tools/call", {"name": name, "arguments": arguments})
                   for number, (name, arguments) in enumerate(calls, start=1)]
        done = mcp_stdio.run(crapkit_argv(self.python, ("-m",), ("mcp", "--repo", str(self.root))),
                             cwd=self.root, frames="\n".join(frames), env=self.env,
                             encoding="utf-8", errors="replace")
        return _results(done.stdout, len(calls))

    def store(self, sql: str, params: tuple = ()) -> list[dict]:
        """Rows from the run store, opened read-only with sqlite3."""
        uri = (self.root / STORE).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, params)]
        finally:
            connection.close()


def _must_spawn(launch: tuple) -> bool:
    """Another interpreter, or crapkit under a wrapper, needs a real child."""
    return bool(os.environ.get(PYTHON_ENV)) or launch != ("-m",)


def _frame(number: int, method: str, params: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": params})


def _replies(stdout: str) -> dict:
    return {reply["id"]: reply for reply in map(json.loads, stdout.splitlines())}


def _results(stdout: str, count: int) -> list[dict]:
    """Each call's `result`, or the whole reply when it is a JSON-RPC error."""
    replies = _replies(stdout)
    numbers = range(1, count + 1)
    missing = [number for number in numbers if number not in replies]
    if missing:
        raise AssertionError(f"the MCP session answered no call {missing}:\n{stdout}")
    return [replies[number].get("result", replies[number]) for number in numbers]


_TYPES = {
    "fn_coverage": ("crapkit.coverage_istanbul", "FnCoverage"),
    "inventory_row": ("crapkit.snapshot", "InventoryRow"),
    "scored_row": ("crapkit.score", "ScoredRow"),
}


def to_crapkit(kind: str, value):
    """A strategy's plain tuple as the crapkit type a production function takes."""
    module, name = _TYPES[kind]
    return getattr(importlib.import_module(module), name)(*value)
