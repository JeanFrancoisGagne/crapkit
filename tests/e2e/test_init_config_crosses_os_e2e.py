r"""The crapkit.toml `init` writes runs on the other OS's checkout.

crapkit.toml is committed. init wrote the launcher of the OS it ran on, so the
file a Windows author committed named `.venv\Scripts\python.exe`, and a Linux
checkout carrying its own `.venv` failed every lane: sh stripped the
backslashes and answered `.venvScriptspython.exe: not found`, exit 5. The Linux
author's `.venv/bin/python` failed the same way under cmd.exe, and a Windows
author's bare `python` failed on an Ubuntu without python-is-python3.

init now writes one launcher token, the same bytes on either OS, and the loader
reads it as the launcher of the OS running the lane. Each test here runs on
every CI job, so the Windows job and the Linux job each prove that the file
init writes runs there; the unit tests in test_launcher_token.py read one
token under both OSes' rules.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo
from crapkit.config import load_config_text
from crapkit.lane_command import expand_launchers

# The lane's python is a child of its own; the in-process runner is enough for
# crapkit itself.
run_cli = cli_runner()

WINDOWS = os.name == "nt"
_VENV_LAUNCHER = ".venv\\Scripts\\python.exe" if WINDOWS else ".venv/bin/python"
_OTHER_VENV_LAUNCHER = ".venv/bin/python" if WINDOWS else ".venv\\Scripts\\python.exe"
_BARE = "python" if WINDOWS else "python3"


def _repo(tmp_path: Path) -> Path:
    """A python repo with a pytest marker and one tested function, so init
    writes a live lane and `crapkit coverage` has a suite to run."""
    repo = tmp_path / "repo"
    files = {
        "calc/__init__.py": "",
        "calc/grade.py": "def classify(n):\n    if n:\n        return 1\n    return 0\n",
        "tests/test_grade.py": ("from calc.grade import classify\n\n\n"
                                "def test_classify():\n    assert classify(1) == 1\n"),
        "pyproject.toml": ('[project]\nname = "demo"\nversion = "0.1.0"\n\n'
                           '[tool.pytest.ini_options]\npythonpath = ["."]\n'),
        ".gitignore": ".venv/\n",
    }
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body, encoding="utf-8")
    git_init_repo(repo)
    git_commit_all(repo, "sources")
    return repo


def _lines(repo: Path, start: str) -> list[str]:
    """The live lines that open with `start`; the commented templates are not
    what runs."""
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith(start)]


def _lane_log_head(repo: Path) -> str:
    """The command the lane actually started, from its log's `$ ` header."""
    log = (repo / ".crapkit" / "lane-py.log").read_text(encoding="utf-8")
    return next(line[2:] for line in log.splitlines() if line.startswith("$ "))


def test_init_writes_the_venv_launcher_as_one_token_every_os_reads(tmp_path, dependency_venv):
    """The lane, the scoped-tests entry, and no OS's own spelling anywhere."""
    repo = _repo(tmp_path)
    dependency_venv(repo / ".venv")

    assert run_cli(repo, "init").returncode == 0

    (command,) = _lines(repo, "command")
    assert command == ('command = "{python:.venv} -m pytest --cov --cov-branch '
                       '--cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/junit-py.xml '
                       '--continue-on-collection-errors"'), command
    (entry,) = _lines(repo, "calc =")
    assert entry.startswith('calc = "{python:.venv} -m pytest '), entry
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    assert "Scripts" not in text and "bin/python" not in text, text


def test_the_venv_lane_init_wrote_runs_on_this_os(tmp_path, dependency_venv):
    """Coverage exits 0, and the lane started this OS's launcher."""
    repo = _repo(tmp_path)
    dependency_venv(repo / ".venv")
    assert run_cli(repo, "init").returncode == 0
    git_commit_all(repo, "adopt crapkit")

    res = run_cli(repo, "coverage")

    assert res.returncode == 0, res.stdout + res.stderr
    assert _lane_log_head(repo).startswith(f"{_VENV_LAUNCHER} -m pytest "), _lane_log_head(repo)


def test_the_file_init_wrote_reads_as_the_other_os_launcher_there(tmp_path, dependency_venv):
    """The bytes are the same on both OSes, so the other OS's checkout reads
    this file the way its own init's file reads: as its own launcher."""
    repo = _repo(tmp_path)
    dependency_venv(repo / ".venv")
    assert run_cli(repo, "init").returncode == 0
    (command,) = _lines(repo, "command")

    assert expand_launchers(command, windows=not WINDOWS).startswith(
        f'command = "{_OTHER_VENV_LAUNCHER} -m pytest ')
    cfg = load_config_text((repo / "crapkit.toml").read_text(encoding="utf-8"))
    assert cfg.lanes[0].command.startswith(f"{_VENV_LAUNCHER} -m pytest ")
    assert dict(cfg.scoped_tests)["calc"].startswith(f"{_VENV_LAUNCHER} -m pytest ")


@pytest.mark.parametrize("written", [
    '.venv\\\\Scripts\\\\python.exe', '.venv/bin/python',
], ids=["0.8.0-windows-init", "0.8.0-posix-init"])
def test_a_0_8_0_launcher_still_runs_only_on_the_os_that_wrote_it(tmp_path, dependency_venv,
                                                                   written):
    """What upgrading repos keep until they swap the line for the token: the
    OS's own spelling is read as written, so the matching OS runs it and the
    other one does not. Nothing is rewritten behind the author's back."""
    repo = _repo(tmp_path)
    dependency_venv(repo / ".venv")
    assert run_cli(repo, "init").returncode == 0
    config = (repo / "crapkit.toml").read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(config.replace("{python:.venv}", written), encoding="utf-8")
    git_commit_all(repo, "a 0.8.0 launcher")

    res = run_cli(repo, "coverage")

    this_os = written.startswith(".venv\\\\") == WINDOWS
    assert (res.returncode == 0) is this_os, res.stdout + res.stderr


def test_a_bare_token_runs_with_the_name_this_os_gives_python(tmp_path):
    """No venv: init writes `{python}`, which reads as `python` on Windows and
    `python3` elsewhere, the name an Ubuntu without python-is-python3 has."""
    repo = _repo(tmp_path)
    assert run_cli(repo, "init").returncode == 0
    (command,) = _lines(repo, "command")
    assert command.startswith('command = "{python} -m pytest '), command
    git_commit_all(repo, "adopt crapkit")

    res = run_cli(repo, "coverage")

    assert res.returncode == 0, res.stdout + res.stderr
    assert _lane_log_head(repo).startswith(f"{_BARE} -m pytest "), _lane_log_head(repo)


def test_doctor_names_the_launcher_the_token_reads_as(tmp_path, dependency_venv):
    """doctor prints the word the lane starts with; a reader who sees the token
    in the file sees what it runs here."""
    repo = _repo(tmp_path)
    dependency_venv(repo / ".venv")
    assert run_cli(repo, "init").returncode == 0

    res = run_cli(repo, "doctor")

    assert res.returncode == 0, res.stdout
    # doctor's runner line also starts `ok   lane 'py':`; the probe line names the launcher.
    runner = "ok   lane 'py': runs pytest (named in its command)"
    (line,) = [ln for ln in res.stdout.splitlines() if ln.startswith("ok   lane 'py':") and ln != runner]
    assert line.startswith(f"ok   lane 'py': {_VENV_LAUNCHER} -> "), line
    assert "{python" not in res.stdout


def test_git_status_stays_clean_after_the_token_lane_runs(tmp_path, dependency_venv):
    repo = _repo(tmp_path)
    dependency_venv(repo / ".venv")
    assert run_cli(repo, "init").returncode == 0
    git_commit_all(repo, "adopt crapkit")

    assert run_cli(repo, "coverage").returncode == 0

    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True,
                            capture_output=True, text=True).stdout
    assert status.strip() == "", status
