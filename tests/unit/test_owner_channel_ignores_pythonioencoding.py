"""The measurement owner's JSON channel reads the same under any PYTHONIOENCODING.

`own_processes` starts a guardian child and talks to it over its stdin and
stdout, one JSON line each way, read as UTF-8. The child inherits the caller's
environment, and PYTHONIOENCODING picks the codec of its standard streams, so
under `utf-8-sig` its first reply began with a byte-order mark
(`JSONDecodeError: Unexpected UTF-8 BOM`) and under `utf-16` or `utf-32` every
byte pair broke the parse (`UnicodeDecodeError ... 0xff` on Windows, `Expecting
property name` on Linux). `coverage` then exited 1 before a lane ran, and on
Linux every MCP tool call answered a JSON-RPC error, since each one starts a
CLI child that owns its processes through the same channel.

The child now pins its own stdin and stdout to UTF-8 before it reads or writes;
the environment it inherits stays as the user set it, for the commands it
supervises.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import hang_guard
import mcp_stdio
from crapkit import _process_owner
from crapkit._process_owner import own_processes

# The first four always worked; the last three are the rows that failed.
ENCODINGS = [None, "utf-8", "cp1252", "latin-1", "utf-8-sig", "utf-16", "utf-32"]
IDS = [encoding or "no-env" for encoding in ENCODINGS]

TOML = ('[crapkit]\ntarget = 6\n\n'
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
        '[[lane]]\nname = "unit"\ncommand = "python make_cov.py"\n'
        'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')
MAKE_COV = (
    "import json, os\n"
    "app = os.path.join(os.getcwd(), 'src', 'app.ts')\n"
    "cov = {app: {'path': app,\n"
    "             'fnMap': {'0': {'name': 'tiny', 'decl': {'start': {'line': 1}},\n"
    "                             'loc': {'start': {'line': 1}, 'end': {'line': 1}}}},\n"
    "             'f': {'0': 1}, 'branchMap': {}, 'b': {}}}\n"
    "json.dump(cov, open('cov.json', 'w'))\n")
APP = "export function tiny(a: number) { return a; }\n"
TANGLED = ("export function tangled(a: number, b: number): number {\n"
           "  let r = 0;\n"
           "  if (a > 0) { if (b > 0) { r = 1; } else if (b < -5) { r = 2; } }\n"
           "  if (a > 10 && b > 10) { r += 3; }\n"
           "  if (a < -1) { r -= 1; } else if (b === 0) { r -= 2; }\n"
           "  return r;\n}\n")


def _environment(encoding: str | None) -> dict:
    """The suite's interpreter first on PATH, so the lane's bare `python`
    finds it, and PYTHONIOENCODING as the row names it."""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONIOENCODING"}
    env["PATH"] = os.pathsep.join(filter(None, (str(Path(sys.executable).parent), env.get("PATH"))))
    if encoding:
        env["PYTHONIOENCODING"] = encoding
    return env


# --- the channel itself -------------------------------------------------------------

@pytest.mark.parametrize("encoding", ENCODINGS, ids=IDS)
def test_the_owner_confirms_and_answers_under_any_pythonioencoding(tmp_path, monkeypatch, encoding):
    """Both directions: the first reply (the locks held) and a request the
    caller writes, answered on the same channel."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    if encoding:
        monkeypatch.setenv("PYTHONIOENCODING", encoding)

    with own_processes([tmp_path / "measure.lock"]) as owner:
        assert owner.held is True
        owner.stop(os.getpid())  # not registered: the owner answers ok and does nothing
        owner.cancel()


class _Stream:
    def __init__(self):
        self.calls = []

    def reconfigure(self, **kwargs) -> None:
        self.calls.append(kwargs)


def test_the_owner_child_pins_both_channel_streams_to_utf8(monkeypatch):
    stdin, stdout = _Stream(), _Stream()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    _process_owner._utf8_channel()

    assert stdin.calls == stdout.calls == [{"encoding": "utf-8"}]


# --- the commands that read it ------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def _crapkit(repo: Path, *args: str, encoding: str | None = None) -> subprocess.CompletedProcess:
    return hang_guard.run([sys.executable, "-m", "crapkit", *args], cwd=repo,
                          env=_environment(encoding), text=True, encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def scored_repo(tmp_path_factory) -> Path:
    """A TypeScript repo with one istanbul lane, one run, and one function over
    the ceiling for `--github` to annotate."""
    repo = tmp_path_factory.mktemp("owner-channel")
    (repo / "src").mkdir()
    (repo / "src" / "app.ts").write_text(APP, encoding="utf-8")
    (repo / "src" / "tangled.ts").write_text(TANGLED, encoding="utf-8")
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (repo / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\ncov.json\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    first = _crapkit(repo, "coverage")
    assert first.returncode == 0, first.stdout + first.stderr
    return repo


@pytest.mark.parametrize("encoding", ENCODINGS, ids=IDS)
def test_coverage_json_answers_under_any_pythonioencoding(scored_repo, encoding):
    result = _crapkit(scored_repo, "coverage", "--json", encoding=encoding)

    assert result.returncode == 0, result.stderr
    assert "run_id" in json.loads(result.stdout), result.stdout


@pytest.mark.parametrize("encoding", ENCODINGS, ids=IDS)
def test_coverage_github_annotates_under_any_pythonioencoding(scored_repo, encoding):
    result = _crapkit(scored_repo, "coverage", "--github", encoding=encoding)

    assert result.returncode == 0, result.stderr
    assert any(line.startswith("::") and "tangled.ts" in line
               for line in result.stdout.splitlines()), result.stdout + result.stderr


def _frame(frame_id: int, method: str, params: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": frame_id, "method": method, "params": params})


@pytest.mark.parametrize("encoding", ENCODINGS, ids=IDS)
def test_mcp_tools_answer_under_any_pythonioencoding(scored_repo, encoding):
    """Each tool call starts a CLI child, which owns its processes through the
    channel on Linux; Windows reads without a guardian and always answered."""
    frames = "\n".join([
        _frame(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}),
        _frame(2, "tools/call", {"name": "list_worklist", "arguments": {}}),
        _frame(3, "tools/call", {"name": "get_function_brief",
                                 "arguments": {"path": "src/tangled.ts", "name": "tangled"}}),
    ]) + "\n"

    result = mcp_stdio.run([sys.executable, "-m", "crapkit", "mcp", "--repo", str(scored_repo)],
                           cwd=scored_repo, frames=frames, env=_environment(encoding),
                           encoding="utf-8", errors="replace")

    replies = {message["id"]: message for message in map(json.loads, result.stdout.splitlines())}
    for call in (2, 3):
        assert "error" not in replies[call], replies[call]
        assert replies[call]["result"]["isError"] is False, replies[call]
    assert "tangled" in json.dumps(replies[3]["result"]["structuredContent"])
