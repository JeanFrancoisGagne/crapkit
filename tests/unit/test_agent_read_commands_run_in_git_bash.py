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


# --- an interpreter path that holds a space -----------------------------------
#
# A venv under C:\Users\Jane Doe or an install under C:\Program Files puts a
# space in the interpreter path. On Windows the next step names the same file
# without the space where a link or an 8.3 name allows, and otherwise quotes the
# spaced segment alone, so the line never opens with a quote and PowerShell runs
# it as printed; only cmd.exe loses a venv in a spaced directory with no 8.3 name
# (docs/adr/0003). sh reads the path in single quotes. README.md and CONTEXT.md
# say so.

def _link_directory(link: Path, target: Path) -> None:
    """`link` reaches `target`: a junction on Windows, which needs no
    privilege, and a symlink elsewhere."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.fixture(scope="module")
def spaced_interpreter(tmp_path_factory):
    """This interpreter reached through a directory named `my python`, so the
    child's sys.executable holds a space."""
    try:
        inside = Path(sys.executable).relative_to(sys.prefix)
    except ValueError:
        pytest.skip("this interpreter lives outside sys.prefix, so no link reaches it")
    link = tmp_path_factory.mktemp("spaced") / "my python"
    _link_directory(link, Path(sys.prefix))
    yield link / inside
    # rmdir removes a junction and unlink a symlink, never what either reaches.
    (os.rmdir if os.name == "nt" else os.unlink)(link)


def _advised_by(interpreter: Path, repo: Path, env: dict) -> str:
    result = hang_guard.run([str(interpreter), "-m", "crapkit", "worklist"], cwd=repo, env=env,
                            text=True, encoding="utf-8", errors="replace")
    found = ADVICE.search(result.stderr)
    assert found, result.stdout + result.stderr
    return found.group(1)


@pytest.mark.parametrize("shell", _shells())
def test_a_spaced_interpreter_runs_in_every_shell_as_printed(unmeasured_repo, tmp_path,
                                                             spaced_interpreter, shell):
    env = _environment(console_script=False)
    advised = _advised_by(spaced_interpreter, unmeasured_repo, env)
    assert not advised.startswith('"'), advised

    result = _run_in(shell, f"{advised} --version", tmp_path, env)

    assert result.returncode == 0, f"{shell}: {advised}\n{result.stdout}{result.stderr}"
    assert result.stdout.startswith("crapkit "), result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell runs on Windows only")
def test_powershell_refuses_the_whole_path_in_double_quotes(tmp_path, spaced_interpreter):
    """0.8.0 printed a spaced interpreter path whole in double quotes. PowerShell
    reads that line as a string and stops at `-m`, so a next step never opens
    with a quote; docs/adr/0003's trade holds only while this refusal does."""
    env = _environment(console_script=False)

    result = _run_in("powershell", f'"{spaced_interpreter}" -m crapkit --version', tmp_path, env)

    assert result.returncode != 0 and "UnexpectedToken" in result.stderr, result.stdout + result.stderr


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("page", ["README.md", "CONTEXT.md"])
def test_a_page_that_says_powershell_runs_the_next_step_names_the_case_cmd_loses(page):
    """Each paragraph that tells a reader PowerShell runs the interpreter
    spelling names docs/adr/0003, the one case cmd.exe loses, and asks for no
    call operator: the line runs in PowerShell as printed."""
    paragraphs = (ROOT / page).read_text(encoding="utf-8").split("\n\n")
    promising = [p for p in paragraphs if "next step" in p.lower() and "PowerShell" in p]

    assert promising, f"{page} no longer says which shells run the next step"
    assert all("docs/adr/0003" in p for p in promising), promising
    assert "`& `" not in "\n\n".join(promising), promising
