"""user_home() finds the home a process's user has, whatever its environment holds.

Path.home() reads USERPROFILE, or HOMEDRIVE with HOMEPATH, on Windows and raises
when a process starts with none of them. HOME does not count there. The worker
budget, the measurement locks and the Claude Code plugin cache all sit under
the home, so one missing variable took down check_config, `doctor`, `coverage`
and a cold `inventory`.
"""
import os
from pathlib import Path

import pytest

from crapkit import userhome
from crapkit.errors import ToolError


def test_a_process_without_home_variables_finds_the_home_its_other_processes_use(
        without_home_variables):
    assert os.path.normcase(userhome.user_home()) == os.path.normcase(without_home_variables)


def test_the_environment_wins_over_the_operating_system(tmp_path, monkeypatch):
    """A test or a sandbox that points HOME and USERPROFILE elsewhere keeps its
    private caches and locks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert userhome.user_home() == tmp_path


def test_no_home_anywhere_names_the_variable_to_set(monkeypatch):
    def unknown(cls):
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", classmethod(unknown))
    monkeypatch.setattr(userhome, "_system_home", lambda: "")
    with pytest.raises(ToolError) as refused:
        userhome.user_home()
    variable = "USERPROFILE" if os.name == "nt" else "HOME"
    assert refused.value.exit_code == 5
    assert str(refused.value).startswith(f"no home directory: {variable} is unset"), refused.value
    assert f"set {variable} to " in str(refused.value), refused.value


def test_posix_asks_the_operating_system_nothing_beyond_path_home(monkeypatch):
    """POSIX Path.home() already reads the password database once HOME is gone,
    so there is nothing further to ask."""
    monkeypatch.setattr(userhome.os, "name", "posix")
    assert userhome._system_home() == ""


@pytest.mark.skipif(os.name != "nt", reason="the profile folder comes from the Windows shell")
def test_windows_names_the_profile_folder_with_no_variable_set(without_home_variables):
    assert os.path.normcase(userhome._windows_profile()) == os.path.normcase(without_home_variables)
