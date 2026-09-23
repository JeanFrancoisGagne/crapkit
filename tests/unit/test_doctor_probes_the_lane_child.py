"""doctor asks its interpreter questions of the child the lane really starts.

lanes.py starts a lane from `root / cwd`, with `[lane.env]` merged over the
process environment. doctor's static check already read both, but its start
check and its three probes (the version report, the pytest-cov import, the
first-run note) ran in doctor's own directory with doctor's own PATH. A lane
whose `[lane.env] PATH` leads to a python holding pytest-cov FAILed at exit 1
while the lane itself ran; a lane naming its repo's `.venv` launcher FAILed
from the root and passed silently from any subdirectory.
"""
import json
import os
import shutil
import sys
import venv

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


def _venv_launcher() -> str:
    """The launcher word init writes for a repo's own venv, spelled for the
    shell this platform runs lanes under."""
    return os.path.join(".venv", "Scripts", "python.exe") if os.name == "nt" else ".venv/bin/python"


def test_a_repo_venv_launcher_gets_one_answer_from_any_directory(tmp_path, monkeypatch, capsys):
    """The repo's `.venv` holds no pytest-cov. From the root doctor FAILed the
    lane; from src/pkg its probes looked for the launcher under src/pkg, found
    nothing, read that as nothing to ask, and exited 0 with no lane line."""
    word = _venv_launcher()
    repo = _repo(tmp_path, _toml(f"{word} -m pytest --cov=src"))
    venv.EnvBuilder(with_pip=False).create(repo / ".venv")
    answers = []
    for where in (repo, repo / "src" / "pkg"):
        monkeypatch.chdir(where)
        admin._start_probe.cache_clear()
        admin._runner_report.cache_clear()
        code, out = _doctor(["doctor", "--json"], capsys)
        answers.append((code, json.loads(out)["problems"]))

    assert answers[1] == answers[0], answers
    code, problems = answers[1]
    named = [p for p in problems if f"names `{word}`" in p and "cannot import pytest_cov" in p]
    assert code == 1 and len(named) == 1, problems


def test_a_manager_the_lanes_own_path_carries_is_not_called_absent(tmp_path, monkeypatch, capsys):
    """`uv sync && python -m pytest --cov` starts with a manager. The lane's
    PATH carries `uv` and doctor's does not, so the lane can start and the gap
    to name is the python's missing pytest-cov, not a manager to install."""
    lane_bin = tmp_path / "lane-bin"
    _python_shim(lane_bin, "exit /b 1", "exit 1")
    if os.name == "nt":
        (lane_bin / "uv.bat").write_text("@exit /b 0\n", encoding="utf-8")
    else:
        (lane_bin / "uv").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (lane_bin / "uv").chmod(0o755)
    repo = _repo(tmp_path, _toml("uv sync && python -m pytest --cov=src",
                                 f"\n[lane.env]\nPATH = '{lane_bin}'\n"))
    git_only = os.path.dirname(shutil.which("git"))
    if shutil.which("uv", path=git_only):
        pytest.skip("uv sits beside git here, so doctor's own PATH cannot leave it out")
    monkeypatch.setenv("PATH", git_only)

    code, out = _doctor(["doctor", "--repo", str(repo), "--json"], capsys)

    problems = json.loads(out)["problems"]
    assert code == 1
    assert [p for p in problems if "cannot import pytest_cov" in p], problems
    assert not [p for p in problems if "runs through `uv`" in p], problems
