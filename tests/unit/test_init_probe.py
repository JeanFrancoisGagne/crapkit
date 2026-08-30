"""init's pytest-cov probe: the first-run trap named before the first run.

The py lane shells out to `pytest --cov`, and those flags come from pytest-cov
— a package of the REPO's interpreter, which a dependency on crapkit could
never guarantee. Only a probe of the python the lane will actually run can say
whether the first `crapkit coverage` survives, and only a clean "no" may warn:
a probe that cannot run is doctor's finding (a dead interpreter), not this one's.
"""
import os
import sys
import time
import types

import pytest

from crapkit import config, mutate_pool
from crapkit.cli import _pytest_cov_probe, _warn_missing_pytest_cov
from crapkit.cli import admin
from crapkit.mutate import Mutant
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


def _name_only_on_path(tmp_path, monkeypatch, *names: str) -> None:
    """A PATH holding these interpreter names and nothing else. Never the
    machine's own PATH: the answer has to come from the fixture."""
    only = tmp_path / "onlypath"
    only.mkdir()
    for name in names:
        if os.name == "nt":
            (only / f"{name}.bat").write_text("@exit /b 0\n", encoding="utf-8")
        else:
            shim = only / name
            shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(only))


def test_the_interpreter_falls_back_to_the_windows_launcher(tmp_path, monkeypatch):
    """Where `python` does not resolve on Windows, `python3` does not either:
    both names come from the one WindowsApps alias. init used to write the
    python3 lane anyway, so the config named an interpreter that does not exist
    on the machine that wrote it and the first `crapkit coverage` exited 5.
    py.exe installs to C:\\Windows and is on PATH without Add to PATH ticked."""
    _name_only_on_path(tmp_path, monkeypatch, "py")
    assert admin._interpreter() == "py"


@pytest.mark.parametrize("present, chosen", [
    (("python", "python3", "py"), "python"),
    (("python3", "py"), "python3"),
])
def test_the_launcher_is_the_last_resort_not_the_first_choice(
        tmp_path, monkeypatch, present, chosen):
    """`py` is Windows-only, and the config it writes gets committed. It may
    only be reached where no portable name resolves at all."""
    _name_only_on_path(tmp_path, monkeypatch, *present)
    assert admin._interpreter() == chosen


@pytest.mark.skipif(os.name != "nt", reason="9009 is cmd.exe's own exit code")
def test_the_windows_store_python_alias_earns_no_pytest_cov_warning(tmp_path, monkeypatch):
    """%LOCALAPPDATA%\\Microsoft\\WindowsApps\\python.exe is on a stock Windows 11
    PATH, so which() finds a `python` even where none is installed. With no Store
    app behind it, that stub prints "Python was not found" and exits 9009. The
    lane has no interpreter at all, and `pip install pytest-cov` fixes none of it."""
    _interpreter_shim(tmp_path, monkeypatch, 9009)
    assert _pytest_cov_probe("python -m pytest --cov") is True


@pytest.mark.parametrize("cmd_shell, answered", [(True, False), (False, True)])
def test_only_cmd_reads_9009_as_nothing_ran(monkeypatch, cmd_shell: bool, answered: bool):
    """cmd.exe returns 9009 for a command it could not run. sh has no such code
    (it truncates an exit status to a byte), so under sh 9009 is a real answer."""
    monkeypatch.setattr(config, "SHELL_IS_CMD", cmd_shell)
    assert admin._probe_answered_no(9009) is answered
    assert admin._probe_answered_no(1) is True, "an ordinary failure is still a clean no"
    assert admin._probe_answered_no(0) is False


def test_the_probe_quotes_an_interpreter_path_with_a_space():
    path = r"C:\Program Files\Python\python.exe" if os.name == "nt" else "/opt/py thon/bin/python"
    quoted = admin._shell_quote(path)
    assert quoted[0] == quoted[-1] and quoted[0] in "\"'" and path in quoted


def test_a_probe_that_cannot_run_says_yes():
    """A missing interpreter must not warn about pytest-cov: the message would
    name the wrong gap, and doctor already flags the executable itself."""
    assert _pytest_cov_probe("no-such-interpreter-7f3a -m pytest --cov") is True


def test_a_probe_the_shell_itself_cannot_start_says_yes(monkeypatch):
    """The other half of "a probe that cannot run is doctor's finding". The
    which() gate answers for a missing interpreter; nothing answered for a shell
    that will not start, and shell=True builds `{COMSPEC} /c ...`, so a COMSPEC
    pointing at nothing makes CreateProcess raise before any interpreter runs."""
    if os.name != "nt":
        pytest.skip("COMSPEC is cmd.exe's; POSIX shell=True is a hardcoded /bin/sh")
    monkeypatch.setenv("COMSPEC", r"C:\no\such\shell-7f3a.exe")
    assert _pytest_cov_probe(f"{sys.executable} -m pytest --cov") is True


def test_a_probe_that_raises_oserror_says_yes(monkeypatch):
    """The COMSPEC route above is Windows-only, and CI reads coverage on Linux.
    This reaches the same two lines on either OS, at the raise itself."""
    import subprocess

    def boom(*args, **kwargs):
        raise OSError(2, "The system cannot find the file specified")

    monkeypatch.setattr(subprocess, "run", boom)
    assert _pytest_cov_probe(f"{sys.executable} -m pytest --cov") is True


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


# --- timeout= has to bound the wall clock, here and in the mutant runner -----
#
# Both calls run under shell=True, so the shell is the child and the program is
# a grandchild. subprocess.run's timeout kills the shell and then drains the
# pipes with no deadline at all, and the grandchild inherited those pipes: the
# call returns when that process dies, not when the timeout expires. Neither
# call reads the output, so neither needs the pipes.

_SLEEP = 8      # the program outlives the timeout by a wide margin
_TIMEOUT = 2
_CEILING = 5    # between the two: only the pipe wait can push past it


def _sleep_command() -> str:
    return f'"{sys.executable}" -c "import time; time.sleep({_SLEEP})"'


def _sleeping_interpreter(tmp_path, monkeypatch) -> None:
    """A `python` first on PATH that outlives the probe's timeout. It does not
    exec, so the shell stays between the probe and the sleeper: killing the
    shell leaves the sleeper holding whatever the shell handed it."""
    shim_dir = tmp_path / "slow"
    shim_dir.mkdir()
    if os.name == "nt":
        (shim_dir / "python.bat").write_text(f"@echo off\n{_sleep_command()}\n", encoding="utf-8")
    else:
        shim = shim_dir / "python"
        shim.write_text(f"#!/bin/sh\n{_sleep_command()}\n", encoding="utf-8")
        shim.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(shim_dir), os.environ.get("PATH", "")]))


def test_the_probe_gives_up_when_its_timeout_expires(tmp_path, monkeypatch):
    """init writes crapkit.toml, prints the summary, probes, and only then
    extends .gitignore — and re-running init refuses once crapkit.toml exists.
    A probe that waits on the interpreter instead of on its own timeout strands
    the user there with a half-scaffolded repo."""
    _sleeping_interpreter(tmp_path, monkeypatch)
    monkeypatch.setattr(admin, "_PROBE_TIMEOUT_SECONDS", _TIMEOUT)
    start = time.perf_counter()
    assert _pytest_cov_probe("python -m pytest --cov") is True
    assert time.perf_counter() - start < _CEILING


def test_a_mutant_that_outlives_its_timeout_dies_at_the_timeout(tmp_path):
    """`except TimeoutExpired: return True` says "a mutant that loops forever is
    dead". A run that only returns when the mutant's suite ends is not that."""
    source = tmp_path / "m.py"
    source.write_text("flag = True\n", encoding="utf-8")
    cfg = types.SimpleNamespace(mutation_command=_sleep_command(),
                                mutation_timeout_seconds=_TIMEOUT)
    mutant = Mutant("m.py", 1, "flag = True", "flag = False", "True -> False")
    start = time.perf_counter()
    assert mutate_pool.run_one(tmp_path, cfg, mutant) is True
    assert time.perf_counter() - start < _CEILING
    assert source.read_text(encoding="utf-8") == "flag = True\n"
