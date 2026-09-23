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

from crapkit import procs
from crapkit.cli import admin, main

_REPORT = "CRAPKIT_RUNNER_REPORT"


def _forget_probes() -> None:
    """Both probes are memoized, and every test here asks about the same words."""
    admin._start_probe.cache_clear()
    admin._runner_report.cache_clear()


@pytest.fixture(autouse=True)
def _forget_probed_words():
    _forget_probes()
    yield
    _forget_probes()


@pytest.fixture
def git_only_path(monkeypatch):
    """doctor's own PATH cut down to git's directory, so a word only the lane's
    PATH carries is missing from doctor's."""
    git_only = os.path.dirname(shutil.which("git"))
    if shutil.which("uv", path=git_only):
        pytest.skip("uv sits beside git here, so doctor's own PATH cannot leave it out")
    monkeypatch.setenv("PATH", git_only)
    return git_only


def _toml(command: str, lane_extra: str = "") -> str:
    # TOML literal strings: a Windows path keeps its backslashes as written.
    return ("[crapkit]\ntarget = 6\n\n"
            "[[scope]]\nname = \"src\"\npaths = [\"src\"]\nlanguages = [\"python\"]\n\n"
            f"[[lane]]\nname = \"py\"\ncommand = '{command}'\n"
            "artifact = \".crapkit/cov/py.json\"\nparser = \"coveragepy\"\nscopes = [\"src\"]\n"
            "results_artifact = \".crapkit/cov/junit.xml\"\n" + lane_extra)


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


def _shim(directory, name: str, body_nt: str, body_sh: str) -> None:
    """`name` in `directory`, the way the lane's shell will find it there."""
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        (directory / f"{name}.bat").write_text(f"@{body_nt}\n", encoding="utf-8")
        return
    shim = directory / name
    shim.write_text(f"#!/bin/sh\n{body_sh}\n", encoding="utf-8")
    shim.chmod(0o755)


def _doctor(argv: list[str], capsys) -> tuple[int, str]:
    code = main(argv)
    return code, capsys.readouterr().out


def _doctor_json_from(where, monkeypatch, capsys) -> tuple[int, list[str]]:
    """doctor --json run from `where`, with no probe answer carried over from
    another directory."""
    _forget_probes()
    monkeypatch.chdir(where)
    code, out = _doctor(["doctor", "--json"], capsys)
    return code, json.loads(out)["problems"]


def test_a_python_the_lanes_own_path_supplies_is_the_one_probed(tmp_path, monkeypatch, capsys):
    """doctor's PATH leads to a python without pytest-cov; the lane's leads to one
    that reports it. The lane runs on its own PATH, so that is the python to ask."""
    good, bad = tmp_path / "lane-bin", tmp_path / "doctor-bin"
    line = f"{_REPORT} {sys.executable} 9.9.1 9.9.2"
    _shim(good, "python", f"echo {line}", f"echo '{line}'")
    _shim(bad, "python", "exit /b 1", "exit 1")
    monkeypatch.setenv("PATH", os.pathsep.join([str(bad), os.environ.get("PATH", "")]))
    repo = _repo(tmp_path, _toml("python -m pytest --cov=src",
                                 f"\n[lane.env]\nPATH = '{good}'\n"))

    code, out = _doctor(["doctor", "--repo", str(repo)], capsys)

    assert code == 0, out
    assert "cannot import pytest_cov" not in out
    assert "lane 'py': python -> " in out and "(pytest 9.9.1, pytest-cov 9.9.2)" in out, out


def _record_launches(monkeypatch) -> list[tuple[str, dict]]:
    """Every command doctor hands procs.run_bounded, with the cwd and env it
    asked for, still run for real."""
    started: list[tuple[str, dict]] = []
    real = procs.run_bounded

    def recording(command, timeout, **kwargs):
        started.append((command, kwargs))
        return real(command, timeout, **kwargs)

    monkeypatch.setattr(procs, "run_bounded", recording)
    return started


def _launch(started: list[tuple[str, dict]], marker: str) -> tuple:
    """Where the probe whose command holds `marker` started, and the mark it saw."""
    kwargs = next(kwargs for command, kwargs in started if marker in command)
    return kwargs.get("cwd"), (kwargs.get("env") or {}).get("CRAPKIT_PROBE_MARK")


def test_every_probe_starts_from_the_lanes_cwd_with_the_lanes_env(tmp_path, monkeypatch, capsys):
    """A probe that cannot find its python exits 1 under cmd.exe and 127 under
    sh, and doctor reads either as 'cannot import pytest_cov', so the FAIL reads
    the same whether the probe started as the lane does or not. Pinned here at
    the launch each probe asks for. The lane's python exits 1 to everything, so
    the version report comes back empty and the import probe runs too."""
    lane_bin = tmp_path / "lane-bin"
    _shim(lane_bin, "python", "exit /b 1", "exit 1")
    repo = _repo(tmp_path, _toml("python -m pytest --cov=src",
                                 f"cwd = \"web\"\n\n[lane.env]\nPATH = '{lane_bin}'\n"
                                 "CRAPKIT_PROBE_MARK = \"lane\"\n"))
    (repo / "web").mkdir()
    started = _record_launches(monkeypatch)

    _doctor(["doctor", "--repo", str(repo)], capsys)

    lane = (repo / "web", "lane")
    assert _launch(started, "python --version") == lane, started
    assert _launch(started, _REPORT) == lane, started
    assert _launch(started, '-c "import pytest_cov"') == lane, started


def _venv_launcher() -> str:
    """The launcher word init writes for a repo's own venv, spelled for the
    shell this platform runs lanes under."""
    return os.path.join(".venv", "Scripts", "python.exe") if os.name == "nt" else ".venv/bin/python"


def test_a_repo_venv_launcher_gets_one_answer_from_any_directory(tmp_path, monkeypatch, capsys):
    """The repo's `.venv` holds no pytest-cov. From the root doctor FAILed the
    lane; from src/pkg its probes looked for the launcher under src/pkg, found
    nothing, read that as nothing to ask, and exited 0 with no lane line. The
    FAIL names the launcher's file under the root from both."""
    word = _venv_launcher()
    repo = _repo(tmp_path, _toml(f"{word} -m pytest --cov=src"))
    venv.EnvBuilder(with_pip=False).create(repo / ".venv")

    answers = [_doctor_json_from(where, monkeypatch, capsys) for where in (repo, repo / "src" / "pkg")]

    assert answers[1] == answers[0], answers
    code, problems = answers[1]
    named = _cannot_import(problems, word)
    assert code == 1 and len(named) == 1, problems
    assert f"resolves here to {repo / word} and" in named[0], named


def _cannot_import(problems: list[str], word: str) -> list[str]:
    """The FAILs saying the python `word` names cannot import pytest_cov."""
    return [p for p in problems if f"names `{word}`" in p and "cannot import pytest_cov" in p]


def test_a_bare_word_beside_the_lane_gets_one_answer_from_any_directory(tmp_path, monkeypatch, capsys):
    """`runcov` sits at the root, where the lane starts. cmd.exe looks there
    before PATH and runs it; sh never looks there, and the lane cannot start.
    Either way the answer is the lane's shell's: doctor passed it from the root
    and FAILed it from src/pkg, because which() read doctor's own directory.
    The opt-out that stops cmd.exe looking there is cleared, whatever this
    machine sets."""
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    repo = _repo(tmp_path, _toml("runcov"))
    _shim(repo, "runcov", "exit /b 0", "exit 0")

    answers = [_doctor_json_from(where, monkeypatch, capsys) for where in (repo, repo / "src" / "pkg")]

    unresolved = [] if os.name == "nt" else ["lane 'py': executable 'runcov' does not resolve on PATH"]
    assert answers == [(1 if unresolved else 0, unresolved)] * 2, answers


def test_a_manager_the_lanes_own_path_carries_is_not_called_absent(
        tmp_path, monkeypatch, capsys, git_only_path):
    """`uv sync && python -m pytest --cov` starts with a manager. The lane's
    PATH carries `uv` and doctor's does not, so the lane can start and the gap
    to name is the python's missing pytest-cov, not a manager to install.
    doctor's PATH leads to a python that answers yes to everything, so a probe
    run under doctor's environment loses the FAIL."""
    lane_bin, doctor_bin = tmp_path / "lane-bin", tmp_path / "doctor-bin"
    _shim(lane_bin, "python", "exit /b 1", "exit 1")
    _shim(lane_bin, "uv", "exit /b 0", "exit 0")
    _shim(doctor_bin, "python", "exit /b 0", "exit 0")
    repo = _repo(tmp_path, _toml("uv sync && python -m pytest --cov=src",
                                 f"\n[lane.env]\nPATH = '{lane_bin}'\n"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(doctor_bin), git_only_path]))

    code, out = _doctor(["doctor", "--repo", str(repo), "--json"], capsys)

    problems = json.loads(out)["problems"]
    assert code == 1
    assert [p for p in problems if "cannot import pytest_cov" in p], problems
    assert not [p for p in problems if "runs through `uv`" in p], problems
