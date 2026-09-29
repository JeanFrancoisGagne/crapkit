"""The next step crapkit prints under `python -m crapkit` runs in every shell a
reader pastes it into.

Every refusal and next step names the command to run next, and when PATH finds
no console script this installation owns, as in a source checkout, that command
opens with the interpreter's path (`invocation._self`). On Windows the line used to print that path as it is: bare
backslashes, which Git Bash reads as escapes and answers with exit 127, and a
path holding a space in double quotes, which PowerShell reads at the start of a
line as a string and stops at `-m` with a parse error.

Each case pastes `<the spelling> coverage` into cmd.exe, Windows PowerShell,
pwsh and Git Bash on Windows, sh and bash elsewhere, with a stand-in crapkit on
PYTHONPATH that prints the argv it received. A stand-in pip does the same for
the install line a pytest-cov note prints, and adds the interpreter that ran it.
A shell this machine lacks is skipped.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sys
from pathlib import Path

import hang_guard
import pytest

from crapkit import invocation
from crapkit.invocation import _self, shell_path

STUB = "import json, sys\nprint(json.dumps(sys.argv[1:]))\n"
PIP_STUB = "import json, sys\nprint(json.dumps([sys.executable, *sys.argv[1:]]))\n"
SHELLS = ("cmd", "powershell", "pwsh", "bash") if os.name == "nt" else ("sh", "bash")


def _git_bash() -> str | None:
    """Git for Windows' bash, never the WSL one System32 puts on PATH."""
    git = shutil.which("git")
    candidates = [Path(git).resolve().parents[1] / "bin" / "bash.exe"] if git else []
    candidates.append(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin"
                      / "bash.exe")
    return next((str(path) for path in candidates if path.is_file()), None)


FINDERS = {"cmd": lambda: os.environ.get("COMSPEC") or shutil.which("cmd")}
if os.name == "nt":
    FINDERS["bash"] = _git_bash


def _shell(name: str) -> str:
    found = FINDERS.get(name, lambda: shutil.which(name))()
    if not found:
        pytest.skip(f"{name} is not installed here")
    return found


def _argv(name: str, line: str, scratch: Path):
    """How `line` reaches `name` as if typed at its prompt: cmd.exe runs it
    after /s strips the outer quotes, PowerShell decodes it, bash reads it from
    a script file."""
    shell = _shell(name)
    if name == "cmd":
        return f'"{shell}" /d /s /c "{line}"'
    if name in ("powershell", "pwsh"):
        script = (line + "; exit $LASTEXITCODE").encode("utf-16-le")
        return [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                base64.b64encode(script).decode("ascii")]
    (scratch / "paste.sh").write_bytes(line.encode("utf-8") + b"\n")
    return [shell, str(scratch / "paste.sh")]


def _paste(name: str, line: str, scratch: Path) -> tuple[int, str]:
    for module, main in (("crapkit", STUB), ("pip", PIP_STUB)):
        stub = scratch / "stub" / module
        stub.mkdir(parents=True, exist_ok=True)
        (stub / "__init__.py").write_bytes(b"")
        (stub / "__main__.py").write_bytes(main.encode("utf-8"))
    env = {**os.environ, "PYTHONPATH": str(scratch / "stub"), "PYTHONDONTWRITEBYTECODE": "1"}
    done = hang_guard.run(_argv(name, line, scratch), cwd=scratch, env=env, text=True,
                          encoding="utf-8", errors="replace")
    lines = done.stdout.strip().splitlines()
    return done.returncode, (lines[-1] if lines else done.stderr.strip()[-300:])


@pytest.fixture()
def linked(tmp_path):
    """link(directory): `directory` reached again through a link whose name holds
    a space. A junction on Windows, which needs no privilege; each link is
    removed alone afterwards, never what it points at."""
    made = []

    def link(target: Path) -> Path:
        path = tmp_path / "with space" / f"link{len(made)}"
        path.parent.mkdir(exist_ok=True)
        if os.name == "nt":
            hang_guard.run(["cmd", "/c", "mklink", "/J", str(path), str(target)])
        else:
            path.symlink_to(target, target_is_directory=True)
        made.append(path)
        return path

    yield link
    for path in made:
        path.rmdir() if os.name == "nt" else path.unlink()


def _install_root(executable: Path) -> Path:
    """A venv's root (it holds pyvenv.cfg), or the directory the interpreter is in."""
    venv = executable.parent.parent
    return venv if (venv / "pyvenv.cfg").is_file() else executable.parent


def _relinked(executable: Path, link) -> Path:
    root = _install_root(executable)
    return link(root) / executable.relative_to(root)


def _off_path(monkeypatch) -> None:
    """PATH finds no crapkit this interpreter installed, so a next step names the
    interpreter. The suite's own venv puts one on PATH under CI."""
    monkeypatch.setattr(invocation, "_runs_here", lambda found: False)


def _printed(monkeypatch, executable: Path) -> str:
    """The next step crapkit prints from `executable` with no console script of
    its own on PATH."""
    _off_path(monkeypatch)
    monkeypatch.setattr(sys, "executable", str(executable))
    return f"{_self()} coverage"


@pytest.mark.parametrize("shell", SHELLS)
def test_the_next_step_runs_from_the_interpreter_path_as_it_is(shell, tmp_path, monkeypatch):
    """Git Bash exited 127 on the bare backslash path."""
    line = _printed(monkeypatch, Path(sys.executable))

    assert _paste(shell, line, tmp_path) == (0, '["coverage"]'), line


@pytest.mark.parametrize("shell", SHELLS)
def test_the_next_step_runs_from_an_interpreter_path_holding_a_space(shell, tmp_path, linked,
                                                                    monkeypatch):
    """PowerShell stopped at `-m` after the double-quoted path."""
    line = _printed(monkeypatch, _relinked(Path(sys.executable), linked))

    assert _paste(shell, line, tmp_path) == (0, '["coverage"]'), line


@pytest.mark.skipif(os.name != "nt", reason="the quoted-segment spelling is the Windows one")
@pytest.mark.parametrize("shell", SHELLS)
def test_a_quoted_segment_in_the_interpreter_path_runs_in_every_shell(shell, tmp_path, linked):
    """When no spelling of the file drops the space, the segment holding it is
    quoted. A base interpreter, because a venv's launcher run from cmd.exe ends
    its own name at the first space unless the line opens with a quote."""
    base = Path(getattr(sys, "_base_executable", sys.executable))
    line = f"{shell_path(str(_relinked(base, linked)))} -m crapkit coverage"

    assert '"with space"' in line
    assert _paste(shell, line, tmp_path) == (0, '["coverage"]'), line


@pytest.mark.skipif(os.name != "nt", reason="the venv launcher is the Windows one")
@pytest.mark.parametrize("shell", SHELLS)
def test_a_venv_in_a_spaced_directory_runs_everywhere_but_cmd(shell, tmp_path, monkeypatch,
                                                              request):
    """No one line runs a venv's python.exe from a directory whose name holds a
    space in every shell (docs/adr/0003). The venv launcher, started from
    cmd.exe, ends its own name at the first space unless the line opens with a
    quote, and PowerShell reads a line that opens with one as a string. crapkit
    takes PowerShell, pwsh and Git Bash. A volume that keeps 8.3 short names
    spells the directory with no space, and then cmd.exe runs it too, so the
    cmd.exe case is a strict xfail only while the printed line holds a quote."""
    venv = tmp_path / "real space" / "venv"
    hang_guard.run([sys.executable, "-m", "venv", "--without-pip", str(venv)])
    line = _printed(monkeypatch, venv / "Scripts" / "python.exe")
    if shell == "cmd" and '"' in line:
        request.applymarker(pytest.mark.xfail(strict=True, reason="docs/adr/0003"))

    assert _paste(shell, line, tmp_path) == (0, '["coverage"]'), line


@pytest.mark.parametrize("typed", ["absolute", "relative"])
@pytest.mark.parametrize("shell", SHELLS)
def test_the_repo_path_a_refusal_prints_reaches_crapkit_as_one_argument(shell, typed, tmp_path,
                                                                        monkeypatch, capsys):
    """`crapkit <path>` is refused with the command to run instead, `... inventory
    --repo <path>`. The path printed as it came split at its space in every
    shell, and `"my repos"/app`, its spaced segment quoted alone, still split in
    PowerShell and pwsh: a word that opens with a quote ends at the closing one.
    The reader pastes from the directory they typed the path in."""
    from crapkit.cli import main

    _off_path(monkeypatch)
    repo = tmp_path / "my repos" / "app"
    main([str(repo) if typed == "absolute" else "my repos/app"])
    line = re.search(r"e\.g\. `([^`]+)`", capsys.readouterr().err).group(1)

    code, printed = _paste(shell, line, tmp_path)

    assert code == 0, line
    command, flag, path = json.loads(printed)
    assert (command, flag, tmp_path / path) == ("inventory", "--repo", repo), line


@pytest.mark.parametrize("shell", SHELLS)
def test_the_install_line_a_pytest_cov_note_prints_runs_the_lanes_python(shell, tmp_path):
    r"""A lane that names its python by path, as init writes a repo's venv on
    Windows (`.venv\Scripts\python.exe`), got the install line
    `.venv\Scripts\python.exe -m pip install pytest-cov`. Git Bash ran that as
    `.venvScriptspython.exe` and exited 127, and from any directory but the
    lane's no shell found the file. Pasted here from another directory.

    The lane's venv is one `python -m venv` made, so the line runs its pip. A
    venv uv made gets `uv pip install --python` with the same interpreter word
    (tests/unit/test_doctor_uv_venv_remedy.py)."""
    from crapkit.cli.admin import _missing_pytest_cov_note
    from crapkit.lane_command import LaunchSpec

    root = tmp_path / "lane" / "venv"
    hang_guard.run([sys.executable, "-m", "venv", "--without-pip", str(root)])
    executable = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    word = str(executable.relative_to(root.parent))
    note = _missing_pytest_cov_note("py", word, LaunchSpec(root.parent))
    line = re.search(r"run `([^`]+)` in the environment", note).group(1)

    code, printed = _paste(shell, line, tmp_path)

    assert code == 0, line
    ran, *argv = json.loads(printed)
    assert os.path.samefile(ran, executable) and argv == ["install", "pytest-cov"], line
