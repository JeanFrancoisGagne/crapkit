"""A repo holding a planted git never makes crapkit run it.

A user who runs crapkit, or whose pre-commit hook runs it, inside a repo with a
`git.exe` at its root used to run that file: Windows' CreateProcess looks in
the current directory before PATH for a bare `git`, unless the caller's
environment sets NoDefaultCurrentDirectoryInExePath, and ordinary setups do
not. On POSIX an empty or `.` PATH entry reads the same directory. crapkit now
starts git from PATH's absolute entries alone (programs.find).

Each case here stands in a repo whose root holds a planted git that records
every run and fails, with PATH leading with an empty entry and `.`. The fixture
first shows a bare `git` started from there running the planted copy, then
runs crapkit through its CLI and checks the record stays empty. CreateProcess
reads NoDefaultCurrentDirectoryInExePath from the caller's own environment, and
a Claude Code shell sets it, so it leaves this process first: with it set the
Windows cases pass with the bug in place.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

run_cli = cli_runner(encoding="utf-8", errors="replace",
                     env_extra={"CRAPKIT_OVERRIDE_REASON": None})

WINDOWS = os.name == "nt"
NO_CWD_SEARCH = "NoDefaultCurrentDirectoryInExePath"
MARKER = "planted-ran.txt"
CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
coverage_optional = true
"""
# A program that records each run in a file beside itself and fails.
_PLANTED_CS = (
    "class Planted { static int Main(string[] args) {"
    " string here = System.IO.Path.GetDirectoryName("
    "System.Reflection.Assembly.GetEntryAssembly().Location);"
    " System.IO.File.AppendAllText(System.IO.Path.Combine(here, \"" + MARKER + "\"),"
    " string.Join(\" \", args) + \"\\n\"); return 1; } }")


def _csc() -> Path | None:
    framework = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET"
    found = (framework / bits / "v4.0.30319" / "csc.exe" for bits in ("Framework64", "Framework"))
    return next((path for path in found if path.is_file()), None)


@pytest.fixture(scope="session")
def planted_exe(tmp_path_factory) -> Path | None:
    """Windows: a compiled program that records its runs, made once a session
    with the C# compiler .NET Framework 4 ships in every Windows 10 and 11."""
    if not WINDOWS:
        return None
    csc = _csc() or pytest.skip("no .NET Framework C# compiler to build a planted program")
    where = tmp_path_factory.mktemp("planted")
    (where / "planted.cs").write_text(_PLANTED_CS, encoding="utf-8")
    subprocess.run([str(csc), "/nologo", "/out:planted.exe", "planted.cs"], cwd=where,
                   check=True, capture_output=True)
    return where / "planted.exe"


def _plant_git(root: Path, planted_exe: Path | None) -> Path:
    """A git in `root` that records each run: `git.exe` on Windows, the one
    name CreateProcess tries for `git`, and a script on POSIX."""
    marker = root / MARKER
    if WINDOWS:
        shutil.copyfile(planted_exe, root / "git.exe")
    else:
        script = root / "git"
        script.write_text(f'#!/bin/sh\necho "$*" >> "{marker}"\nexit 1\n', encoding="utf-8")
        script.chmod(0o755)
    return marker


def _cwd_search_is_on() -> bool:
    """CreateProcess searches the current directory: the variable is unset here."""
    return NO_CWD_SEARCH.upper() not in {key.upper() for key in os.environ}


def _repo(root: Path) -> Path:
    """A committed repo with one staged change for the hook to judge."""
    root.mkdir()
    git_init_repo(root)
    (root / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("def base(n):\n    return n\n", encoding="utf-8")
    git_commit_all(root, "init")
    (root / "src" / "mod.py").write_text("def fn(n):\n    return n + 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture()
def planted_repo(tmp_path, planted_exe, monkeypatch):
    """The repo, built while nothing is planted, then entered with a planted git
    at its root, PATH leading with an empty entry and `.`, and the variable that
    would hide the bug gone. Answers the planted git's record."""
    root = _repo(tmp_path / "repo")
    monkeypatch.delenv(NO_CWD_SEARCH, raising=False)
    marker = _plant_git(root, planted_exe)
    monkeypatch.chdir(root)
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", os.environ["PATH"]]))
    subprocess.run(["git", "--version"], cwd=root, capture_output=True)
    assert marker.exists(), "a bare `git` started here runs the planted copy"
    marker.unlink()
    return root, marker


@pytest.mark.parametrize("command", ["hook-precommit", "coverage"])
def test_crapkit_run_from_a_repo_root_holding_a_planted_git_runs_paths_git(command, planted_repo):
    root, marker = planted_repo
    assert _cwd_search_is_on()

    done = run_cli(root, command)

    assert done.returncode == 0, done.stdout + done.stderr
    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_git_on_no_absolute_entry_answers_as_a_missing_git_did(tmp_path, planted_exe, monkeypatch):
    """With git on no absolute PATH entry, a planted copy changes nothing: the
    hook says and exits what it does on a machine with no git at all."""
    root = _repo(tmp_path / "repo")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.delenv(NO_CWD_SEARCH, raising=False)
    missing = run_cli(root, "hook-precommit", env_extra={"PATH": str(empty)})
    marker = _plant_git(root, planted_exe)
    monkeypatch.chdir(root)
    assert _cwd_search_is_on()

    planted = run_cli(root, "hook-precommit", env_extra={"PATH": os.pathsep.join(["", ".", str(empty)])})

    assert "git executable not found" in missing.stdout + missing.stderr
    assert (planted.returncode, planted.stdout, planted.stderr) == \
        (missing.returncode, missing.stdout, missing.stderr)
    assert not marker.exists()
