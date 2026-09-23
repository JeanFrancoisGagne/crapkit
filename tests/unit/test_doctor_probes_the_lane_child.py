"""doctor asks its interpreter questions of the child the lane really starts.

lanes.py starts a lane from `root / cwd`, with `[lane.env]` merged over the
process environment. doctor's static check already read both, but its start
check and its three probes (the version report, the pytest-cov import, the
first-run note) ran in doctor's own directory with doctor's own PATH. A lane
whose `[lane.env] PATH` leads to a python holding pytest-cov FAILed at exit 1
while the lane itself ran; a lane naming its repo's `.venv` launcher FAILed
from the root and passed silently from any subdirectory.
"""
import os
import sys

import pytest

from cli_inproc_repo import commit_all, git

from crapkit.cli import admin, main

_REPORT = "CRAPKIT_RUNNER_REPORT"


@pytest.fixture(autouse=True)
def _forget_probed_words():
    """Both probes are memoized, and every test here asks about `python`."""
    admin._start_probe.cache_clear()
    admin._runner_report.cache_clear()
    yield
    admin._start_probe.cache_clear()
    admin._runner_report.cache_clear()


def _toml(command: str, lane_env: str = "") -> str:
    # TOML literal strings: a Windows path keeps its backslashes as written.
    return ("[crapkit]\ntarget = 6\n\n"
            "[[scope]]\nname = \"src\"\npaths = [\"src\"]\nlanguages = [\"python\"]\n\n"
            f"[[lane]]\nname = \"py\"\ncommand = '{command}'\n"
            "artifact = \".crapkit/cov/py.json\"\nparser = \"coveragepy\"\nscopes = [\"src\"]\n"
            "results_artifact = \".crapkit/cov/junit.xml\"\n" + lane_env)


def _repo(tmp_path, toml: str):
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\n.venv/\n", encoding="utf-8")
    (repo / "crapkit.toml").write_text(toml, encoding="utf-8")
    git(repo, "init", "-q")
    commit_all(repo, "init")
    return repo


def _python_shim(directory, body_nt: str, body_sh: str) -> None:
    """A `python` in `directory`, the way the lane's shell will find one there."""
    directory.mkdir(parents=True)
    if os.name == "nt":
        (directory / "python.bat").write_text(f"@{body_nt}\n", encoding="utf-8")
        return
    shim = directory / "python"
    shim.write_text(f"#!/bin/sh\n{body_sh}\n", encoding="utf-8")
    shim.chmod(0o755)


def _doctor(argv: list[str], capsys) -> tuple[int, str]:
    code = main(argv)
    return code, capsys.readouterr().out


def test_a_python_the_lanes_own_path_supplies_is_the_one_probed(tmp_path, monkeypatch, capsys):
    """doctor's PATH leads to a python without pytest-cov; the lane's leads to one
    that reports it. The lane runs on its own PATH, so that is the python to ask."""
    good, bad = tmp_path / "lane-bin", tmp_path / "doctor-bin"
    line = f"{_REPORT} {sys.executable} 9.9.1 9.9.2"
    _python_shim(good, f"echo {line}", f"echo '{line}'")
    _python_shim(bad, "exit /b 1", "exit 1")
    monkeypatch.setenv("PATH", os.pathsep.join([str(bad), os.environ.get("PATH", "")]))
    repo = _repo(tmp_path, _toml("python -m pytest --cov=src",
                                 f"\n[lane.env]\nPATH = '{good}'\n"))

    code, out = _doctor(["doctor", "--repo", str(repo)], capsys)

    assert code == 0, out
    assert "cannot import pytest_cov" not in out
    assert "lane 'py': python -> " in out and "(pytest 9.9.1, pytest-cov 9.9.2)" in out, out
