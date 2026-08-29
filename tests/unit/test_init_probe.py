"""init's pytest-cov probe: the first-run trap named before the first run.

The py lane shells out to `pytest --cov`, and those flags come from pytest-cov
— a package of the REPO's interpreter, which a dependency on crapkit could
never guarantee. Only a probe of the python the lane will actually run can say
whether the first `crapkit coverage` survives, and only a clean "no" may warn:
a probe that cannot run is doctor's finding (a dead interpreter), not this one's.
"""
import os
import sys

import pytest

from crapkit.cli import _pytest_cov_probe, _warn_missing_pytest_cov
from crapkit.cli import admin
from crapkit.scaffold import LaneSpec


def _lane(command: str, parser: str = "coveragepy") -> LaneSpec:
    return LaneSpec("py", command, ".crapkit/cov/py.json", parser, ("python",))


def test_the_probe_says_yes_where_pytest_cov_imports():
    assert _pytest_cov_probe(f"{sys.executable} -m pytest --cov") is True


def test_the_probe_says_no_where_the_import_fails(tmp_path, monkeypatch):
    shim = tmp_path / "pytest_cov.py"
    shim.write_text('raise ImportError("shimmed out")\n', encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    assert _pytest_cov_probe(f"{sys.executable} -m pytest --cov") is False


def _interpreter_shim(tmp_path, monkeypatch, exit_code: int) -> None:
    """A `python` first on PATH, which the shell resolves before the real one."""
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    if os.name == "nt":
        (shim_dir / "python.bat").write_text(f"@exit /b {exit_code}\n", encoding="utf-8")
    else:
        shim = shim_dir / "python"
        shim.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
        shim.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(shim_dir), os.environ.get("PATH", "")]))


@pytest.mark.parametrize("exit_code, answer", [(1, False), (0, True)])
def test_a_bare_name_resolves_through_the_shell_the_lane_runs_under(
        tmp_path, monkeypatch, exit_code, answer):
    """init writes `python -m pytest --cov`, a bare name. The lane runs it under
    the shell, and the shell's PATH search (a .bat shim included) is the only
    resolution whose answer means anything for that lane."""
    _interpreter_shim(tmp_path, monkeypatch, exit_code)
    assert _pytest_cov_probe("python -m pytest --cov") is answer


def test_the_probe_quotes_an_interpreter_path_with_a_space():
    path = r"C:\Program Files\Python\python.exe" if os.name == "nt" else "/opt/py thon/bin/python"
    quoted = admin._shell_quote(path)
    assert quoted[0] == quoted[-1] and quoted[0] in "\"'" and path in quoted


def test_a_probe_that_cannot_run_says_yes():
    """A missing interpreter must not warn about pytest-cov: the message would
    name the wrong gap, and doctor already flags the executable itself."""
    assert _pytest_cov_probe("no-such-interpreter-7f3a -m pytest --cov") is True


def test_a_failing_probe_prints_both_install_commands(monkeypatch, capsys):
    monkeypatch.setattr(admin, "_pytest_cov_probe", lambda command: False)
    _warn_missing_pytest_cov((_lane("python -m pytest --cov"),))
    err = capsys.readouterr().err
    assert "pytest_cov" in err
    assert "pip install pytest-cov" in err and "crapkit[py]" in err


def test_a_passing_probe_prints_nothing(monkeypatch, capsys):
    monkeypatch.setattr(admin, "_pytest_cov_probe", lambda command: True)
    _warn_missing_pytest_cov((_lane("python -m pytest --cov"),))
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("lane", [
    _lane("npx vitest run --coverage", parser="istanbul"),
    _lane("python -m pytest"),  # no --cov: nothing for pytest-cov to reject
])
def test_only_a_cov_flagged_coveragepy_lane_is_probed(lane, monkeypatch, capsys):
    def boom(command):
        raise AssertionError("this lane must not be probed")

    monkeypatch.setattr(admin, "_pytest_cov_probe", boom)
    _warn_missing_pytest_cov((lane,))
    assert capsys.readouterr().err == ""
