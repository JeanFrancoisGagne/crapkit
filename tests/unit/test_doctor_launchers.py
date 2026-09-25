"""doctor names every `crapkit` launcher on PATH, and `--plugin-root` looks for
the one the plugin starts outside the environment uvx built for doctor itself.

Two installs on one PATH: the shell, a git hook, the plugin's hooks and an MCP
client each start whichever their own PATH lists first, so they run different
versions, and doctor named only its own. Under `uvx crapkit doctor
--plugin-root`, PATH starts with uvx's cached environment, which the plugin's
hooks never inherit: doctor found crapkit there and passed while `claude mcp
list` failed with ENOENT.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from crapkit.cli import admin, main
from test_launchers import joined, shim

PLUGIN = Path(__file__).resolve().parents[2] / "plugin"
BIN = "Scripts" if os.name == "nt" else "bin"


@pytest.fixture(autouse=True)
def _fresh_answers():
    admin._spawned_cli.cache_clear()
    admin._launcher_report.cache_clear()
    yield
    admin._spawned_cli.cache_clear()
    admin._launcher_report.cache_clear()


# --- two installs on PATH -------------------------------------------------------------

def test_two_launchers_at_different_versions_are_named_each_with_its_version(tmp_path, monkeypatch):
    new, old = shim(tmp_path / "new", "0.8.1"), shim(tmp_path / "old", "0.7.6")
    monkeypatch.setenv("PATH", joined(tmp_path / "new", tmp_path / "old"))

    (finding,) = admin._doctor_launchers()

    assert finding.level == "note", "the machine, not this repo's setup"
    assert finding.text == (
        f"PATH holds 2 crapkit launchers: {new} (0.8.1), {old} (0.7.6). The shell, a git hook, "
        "the plugin's hooks and an MCP client each run the first one their own PATH lists, so "
        "they can run different versions; uninstall the copies you do not use, or upgrade them "
        "to one version")


def test_two_launchers_at_one_version_are_a_note(tmp_path, monkeypatch):
    first, second = shim(tmp_path / "a", "0.8.1"), shim(tmp_path / "b", "0.8.1")
    monkeypatch.setenv("PATH", joined(tmp_path / "a", tmp_path / "b"))

    (finding,) = admin._doctor_launchers()

    assert finding.level == "note"
    assert finding.text == (
        f"PATH holds 2 crapkit launchers, all 0.8.1: {first}, {second}; an upgrade has to "
        "reach each of them, or they drift apart")


def test_one_launcher_or_none_says_nothing(tmp_path, monkeypatch):
    shim(tmp_path / "a")
    monkeypatch.setenv("PATH", joined(tmp_path / "a"))
    assert admin._doctor_launchers() == []

    monkeypatch.setenv("PATH", joined(tmp_path / "empty"))
    assert admin._doctor_launchers() == []


def test_a_launcher_that_answers_no_version_is_named_as_such(tmp_path, monkeypatch):
    shim(tmp_path / "a", "0.8.1")
    broken = tmp_path / "b" / ("crapkit.bat" if os.name == "nt" else "crapkit")
    broken.parent.mkdir()
    broken.write_text("@exit /b 2\n" if os.name == "nt" else "#!/bin/sh\nexit 2\n", encoding="utf-8")
    broken.chmod(0o755)
    monkeypatch.setenv("PATH", joined(tmp_path / "a", tmp_path / "b"))

    (finding,) = admin._doctor_launchers()

    assert f"{broken} (no version answered)" in finding.text


# --- doctor run from an environment built for one command ----------------------------

def _under_uvx(tmp_path: Path, monkeypatch) -> Path:
    """This process running from a uvx cache environment whose launcher sits
    first on PATH, the way uvx starts its command."""
    cached = tmp_path / "cache" / "uv" / "archive-v0" / "ePz6wC7F"
    shim(cached / BIN, admin.__version__)
    monkeypatch.setattr(sys, "prefix", str(cached))
    return cached


def test_under_uvx_the_launcher_uvx_put_on_path_is_not_the_plugin_s(tmp_path, monkeypatch):
    cached = _under_uvx(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", joined(cached / BIN))

    assert admin._spawned_cli() is None


def test_under_uvx_plugin_root_fails_naming_uvx_and_the_install_that_stays(tmp_path, monkeypatch,
                                                                          capsys):
    cached = _under_uvx(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", joined(cached / BIN))

    assert main(["doctor", "--plugin-root", str(PLUGIN)]) == 1
    (line,) = capsys.readouterr().out.splitlines()
    assert line == (
        "crapkit doctor: FAIL no `crapkit` on PATH outside the environment uvx built for this "
        f"one command ({cached}), and the plugin's hooks never inherit that one: its "
        "hooks/hooks.json and .mcp.json both spawn the bare name, so every PostToolUse edit "
        "fires a command that cannot start and the MCP server never comes up. Install crapkit "
        "where the hook's PATH can see it (`uv tool install crapkit`), then run this check again.")


def test_under_uvx_an_install_further_down_path_is_the_one_checked(tmp_path, monkeypatch):
    cached = _under_uvx(tmp_path, monkeypatch)
    kept = shim(tmp_path / "tools", "0.7.6")
    monkeypatch.setenv("PATH", joined(cached / BIN, tmp_path / "tools"))

    assert admin._spawned_cli() == (str(kept), "0.7.6")
