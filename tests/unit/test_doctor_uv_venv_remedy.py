"""The install lines doctor prints work in a venv uv made.

`uv venv` writes `uv = VERSION` into pyvenv.cfg and installs no pip, so a line
reading `.venv/bin/python -m pip install pytest-cov` fails there with "No module
named pip" and the gap it names stays open. doctor now names `uv pip install
--python <that python>` for such a venv: the missing pytest-cov note, the
coverage.py floor, and the CLI upgrade `--plugin-root` prints.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from crapkit.cli import admin
from crapkit.lane_command import LaunchSpec
from crapkit.launchers import pip_install

WINDOWS = os.name == "nt"
PYTHON = Path(".venv") / ("Scripts" if WINDOWS else "bin") / ("python.exe" if WINDOWS else "python")


def venv(root: Path, *, uv: bool) -> Path:
    """A venv's interpreter under `root`, made by uv or by `python -m venv`."""
    python = root / PYTHON
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    made_by = "uv = 0.9.2\n" if uv else "include-system-site-packages = false\n"
    (root / ".venv" / "pyvenv.cfg").write_text(f"home = /usr/bin\n{made_by}", encoding="utf-8")
    return python


@pytest.mark.parametrize("uv, line", [
    (True, "uv pip install --python .venv/bin/python pytest-cov"),
    (False, ".venv/bin/python -m pip install pytest-cov"),
], ids=["uv-made", "venv-made"])
def test_the_install_line_is_the_one_that_venv_can_run(tmp_path, uv, line):
    python = venv(tmp_path, uv=uv)

    assert pip_install(str(python), "pytest-cov", spelled=".venv/bin/python") == line


def test_a_python_with_no_venv_around_it_gets_pip(tmp_path):
    assert pip_install(str(tmp_path / "python3"), "pytest-cov") == f"{tmp_path / 'python3'} -m pip install pytest-cov"


def test_the_missing_pytest_cov_note_names_uv_pip_in_a_venv_uv_made(tmp_path):
    """The install line names the file the lane's word resolves to, spelled as
    a next step spells an interpreter, so it pastes from any directory."""
    from crapkit.invocation import interpreter_word

    python = venv(tmp_path, uv=True)
    word = PYTHON.as_posix()

    note = admin._missing_pytest_cov_note("py", word, LaunchSpec(tmp_path))

    assert (f"run `uv pip install --python {interpreter_word(str(python))} pytest-cov` in the "
            "environment the suite runs in") in note


def test_the_coverage_floor_names_uv_pip_in_a_venv_uv_made(tmp_path):
    python = venv(tmp_path, uv=True)

    (finding,) = admin._coverage_floor("py", str(python), "7.4.4")

    assert (f'`uv pip install --python {admin._shell_quote(str(python))} "coverage>=7.13.1"`'
            in finding.text), finding.text


# --- lizard missing from the environment running crapkit ---------------------------------
#
# The FAIL said `pip install lizard`, which lands in whatever environment the
# shell's pip belongs to. crapkit installed with `uv tool install` runs in a venv
# uv made, where no pip lives, so the shell's pip is some other environment's.

@pytest.mark.parametrize("uv", [True, False], ids=["uv-made", "venv-made"])
def test_a_missing_lizard_names_the_install_for_the_python_running_crapkit(tmp_path, monkeypatch,
                                                                          uv):
    python = venv(tmp_path, uv=uv)
    monkeypatch.setattr(admin, "_lizard_version", lambda: None)
    monkeypatch.setattr(admin.sys, "executable", str(python))

    (finding,) = admin._doctor_tools()

    assert finding.level == "FAIL"
    install = pip_install(str(python), "lizard", admin._shell_quote(str(python)))
    assert finding.text == (f"lizard is not importable by the python running crapkit ({python}) "
                            f"- run `{install}`, or reinstall crapkit"), finding.text
    assert install.startswith("uv pip install --python" if uv else admin._shell_quote(str(python)))
