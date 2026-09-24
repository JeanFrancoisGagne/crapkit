"""The next command crapkit names runs as printed in the shell that reads it.

An agent on Windows reads crapkit's refusals through the MCP server or through
its Bash tool, and that tool is Git Bash. Before the first run both answer
"run `<command> coverage` first", and the MCP server's children run as
`python -m crapkit`, so the command named the interpreter as
`C:\\venv\\Scripts\\python.exe -m crapkit`. Git Bash drops every backslash and
answered `C:venvScriptspython.exe: command not found`, 127, while cmd.exe and
PowerShell ran it. The interpreter is now spelled with forward slashes, which
all of them run, and `crapkit` is named when PATH resolves it to this
interpreter's console script.

Each case takes the command from the real CLI's stderr and the real MCP
result, swaps `coverage` for `--version`, and runs it in every shell this
machine has. Git Bash is named by its path: a bare `bash` on Windows can reach
WSL's bash instead.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import hang_guard
import mcp_stdio

GIT_BASH = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
ADVICE = re.compile(r"run `(.+?) coverage` first")
TOML = ('[crapkit]\ntarget = 6\n\n'
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n')


def _shells() -> list[str]:
    if os.name == "nt":
        return (["git-bash"] if GIT_BASH.is_file() else []) + ["cmd", "powershell"]
    return ["bash", "sh"]


def _without_launchers(path: str) -> str:
    """PATH minus every directory that holds a crapkit console script."""
    names = ("crapkit", "crapkit.exe", "crapkit.cmd")
    kept = [entry for entry in path.split(os.pathsep)
            if entry and not any((Path(entry) / name).is_file() for name in names)]
    return os.pathsep.join(kept)


def _environment(console_script: bool) -> dict:
    env = dict(os.environ)
    if not console_script:
        env["PATH"] = _without_launchers(env.get("PATH", ""))
    return env


@pytest.fixture(scope="module")
def unmeasured_repo(tmp_path_factory) -> Path:
    """A repo crapkit has never measured: every read command names `coverage`."""
    repo = tmp_path_factory.mktemp("unmeasured")
    (repo / "src").mkdir()
    (repo / "src" / "app.ts").write_text("export function f(a: number) { return a; }\n", encoding="utf-8")
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       cwd=repo, check=True, capture_output=True)
    return repo


def _from_cli(repo: Path, env: dict) -> str:
    result = hang_guard.run([sys.executable, "-m", "crapkit", "worklist"], cwd=repo, env=env,
                            text=True, encoding="utf-8", errors="replace")
    assert result.returncode != 0, result.stdout
    return result.stderr


def _from_mcp(repo: Path, env: dict) -> str:
    frames = "\n".join(json.dumps(frame) for frame in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "list_worklist", "arguments": {}}})) + "\n"
    result = mcp_stdio.run([sys.executable, "-m", "crapkit", "mcp", "--repo", str(repo)], cwd=repo,
                           frames=frames, env=env, encoding="utf-8", errors="replace")
    replies = {message["id"]: message for message in map(json.loads, result.stdout.splitlines())}
    # The tool's text is the CLI's --json error object; an agent reads its message.
    return json.loads(replies[2]["result"]["content"][0]["text"])["error"]["message"]


SURFACES = {"cli-stderr": _from_cli, "mcp-result": _from_mcp}


def _advised(surface: str, repo: Path, env: dict) -> str:
    text = SURFACES[surface](repo, env)
    found = ADVICE.search(text)
    assert found, text
    return found.group(1)


def _run_in(shell: str, line: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    if shell == "git-bash":
        script = cwd / "advised.sh"
        script.write_bytes(line.encode("utf-8") + b"\n")
        argv = [str(GIT_BASH), "--noprofile", "--norc", script.as_posix()]
    elif shell == "powershell":
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", line + "; exit $LASTEXITCODE"]
    elif shell == "cmd":
        argv = line
    else:
        argv = [shell, "-c", line]
    return hang_guard.run(argv, shell=shell == "cmd", cwd=cwd, env=env,
                          text=True, encoding="utf-8", errors="replace")


@pytest.mark.parametrize("shell", _shells())
@pytest.mark.parametrize("surface", list(SURFACES))
def test_the_advised_interpreter_command_runs_in_every_shell(unmeasured_repo, tmp_path, surface, shell):
    env = _environment(console_script=False)
    advised = _advised(surface, unmeasured_repo, env)

    result = _run_in(shell, f"{advised} --version", tmp_path, env)

    assert result.returncode == 0, f"{shell}: {advised}\n{result.stdout}{result.stderr}"
    assert result.stdout.startswith("crapkit "), result.stdout


@pytest.mark.parametrize("shell", _shells())
@pytest.mark.parametrize("surface", list(SURFACES))
def test_a_console_script_on_path_is_named_and_runs_in_every_shell(unmeasured_repo, tmp_path,
                                                                   surface, shell):
    """CI installs crapkit into the interpreter that runs the suite, so this
    row runs there; a checkout driven through PYTHONPATH skips it."""
    from crapkit.invocation import _runs_here

    if not _runs_here(shutil.which("crapkit")):
        pytest.skip("this interpreter has no crapkit console script on PATH")
    env = _environment(console_script=True)
    advised = _advised(surface, unmeasured_repo, env)

    assert advised == "crapkit", advised
    result = _run_in(shell, "crapkit --version", tmp_path, env)

    assert result.returncode == 0, f"{shell}\n{result.stdout}{result.stderr}"
