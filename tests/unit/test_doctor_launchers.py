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

import json
import os
import sys
from pathlib import Path

import pytest

from cli_inproc_repo import repo, template_repo  # noqa: F401
from crapkit.cli import admin, main
from test_launchers import joined, shim, uv_cache

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

    assert finding.level == "WARN", "a git hook and the shell can record marks under two versions"
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

    assert finding.level == "WARN", "a launcher that cannot answer is one a hook cannot run"
    assert f"{broken} (no version answered)" in finding.text


def test_doctor_reports_the_skew_among_its_warnings(repo, tmp_path, monkeypatch, capsys):  # noqa: F811
    """Four launchers at three versions printed a note and then `doctor: no
    problems found`, and --json carried nothing a wrapper reads as a warning.
    The shims go first on the real PATH, which git and the lanes still need."""
    new, old = shim(tmp_path / "new", "0.8.1"), shim(tmp_path / "old", "0.7.6")
    monkeypatch.setenv("PATH", joined(tmp_path / "new", tmp_path / "old", os.environ["PATH"]))

    assert main(["doctor", "--json", "--repo", str(repo)]) in (0, 1)

    warnings = json.loads(capsys.readouterr().out)["warnings"]
    (skew,) = [w for w in warnings if w.startswith("PATH holds ")]
    assert f"{new} (0.8.1), {old} (0.7.6)" in skew, skew


# --- doctor run from an environment built for one command ----------------------------

def _under_uvx(tmp_path: Path, monkeypatch) -> Path:
    """This process running from a uvx cache environment whose launcher sits
    first on PATH, the way uvx starts its command."""
    cached = uv_cache(tmp_path / "cache" / "uv") / "archive-v0" / "ePz6wC7F"
    shim(cached / BIN, admin.__version__)
    monkeypatch.setattr(sys, "prefix", str(cached))
    return cached


def _under_uv_run_with(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """`uv run --with crapkit crapkit doctor`: this process runs from the
    environment uv built for the run, in its cache's builds-v0, and PATH lists
    that environment's launcher, then the `--with` layer's from archive-v0."""
    cache = uv_cache(tmp_path / "cache" / "uv")
    run, layer = cache / "builds-v0" / ".tmp4THQRy", cache / "archive-v0" / "Pfax6h9m"
    shim(run / BIN, admin.__version__), shim(layer / BIN, admin.__version__)
    monkeypatch.setattr(sys, "prefix", str(run))
    return run, layer


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
        "crapkit doctor: FAIL no `crapkit` on PATH outside the environment uv built for this "
        f"one command ({cached}), and the plugin's hooks never inherit that one: its "
        "hooks/hooks.json and .mcp.json both spawn the bare name, so every PostToolUse edit "
        "fires a command that cannot start and the MCP server never comes up. Install crapkit "
        "where the hook's PATH can see it (`uv tool install crapkit`, or `pipx install crapkit` "
        "if you ran doctor through `pipx run`), then run this check again.")


def test_the_upgrade_guide_quotes_the_fail_a_one_command_runner_now_draws(tmp_path, monkeypatch,
                                                                          capsys):
    """0.8.0 counted the launcher uvx put on PATH and exited 0; this FAIL exits
    1, so the guide's plugin section quotes its opening and names both exits."""
    cached = _under_uvx(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", joined(cached / BIN))

    main(["doctor", "--plugin-root", str(PLUGIN)])
    opening = capsys.readouterr().out.split("crapkit doctor: ", 1)[1].split(" (", 1)[0]
    guide = (PLUGIN.parent / "docs" / "upgrading.md").read_text(encoding="utf-8")
    section = " ".join(guide.split("\n## Plugin and MCP clients\n", 1)[1].split("\n## ", 1)[0].split())

    assert opening.startswith("FAIL no `crapkit` on PATH outside"), opening
    assert f"``{opening}``" in section
    assert "exited 0" in section and "exits 1" in section


def test_under_pipx_run_on_its_pip_backend_the_fail_names_pipx(tmp_path, monkeypatch, capsys):
    cached = tmp_path / ".cache" / "pipx" / "7200333e4116883"
    shim(cached / BIN, admin.__version__)
    monkeypatch.setattr(sys, "prefix", str(cached))
    monkeypatch.setenv("PATH", joined(cached / BIN))

    assert main(["doctor", "--plugin-root", str(PLUGIN)]) == 1
    (line,) = capsys.readouterr().out.splitlines()
    assert f"outside the environment pipx built for this one command ({cached})" in line
    assert line.endswith("(`pipx install crapkit`), then run this check again."), line


def test_under_uvx_the_fail_sends_no_one_to_put_uv_s_environment_on_path(tmp_path, monkeypatch, capsys):
    """uvx's environment holds this crapkit's launcher, and uv deletes or
    rebuilds that environment on its own. The FAIL names the install that
    stays, not the launcher's directory as one to add to PATH."""
    cached = _under_uvx(tmp_path, monkeypatch)
    (cached / BIN / ("crapkit.exe" if os.name == "nt" else "crapkit")).touch()
    monkeypatch.setattr(admin, "_launcher_dirs", lambda: [cached / BIN])
    monkeypatch.setenv("PATH", joined(cached / BIN))

    assert main(["doctor", "--plugin-root", str(PLUGIN)]) == 1
    (line,) = capsys.readouterr().out.splitlines()
    assert "which PATH does not list" not in line, line
    assert line.endswith("then run this check again."), line


def test_under_uvx_an_install_further_down_path_is_the_one_checked(tmp_path, monkeypatch):
    cached = _under_uvx(tmp_path, monkeypatch)
    kept = shim(tmp_path / "tools", "0.7.6")
    monkeypatch.setenv("PATH", joined(cached / BIN, tmp_path / "tools"))

    assert admin._spawned_cli() == (str(kept), "0.7.6")


def test_under_uv_run_with_neither_environment_uv_put_on_path_is_the_plugin_s(tmp_path, monkeypatch,
                                                                             capsys):
    """`uv run --with crapkit crapkit doctor --plugin-root` exited 0 while
    `claude mcp list` failed with ENOENT: doctor knew uvx's archive-v0 only, and
    this run's environment sits in builds-v0 with the `--with` layer behind it."""
    run, layer = _under_uv_run_with(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", joined(run / BIN, layer / BIN))

    assert main(["doctor", "--plugin-root", str(PLUGIN)]) == 1
    (line,) = capsys.readouterr().out.splitlines()
    assert line.startswith("crapkit doctor: FAIL no `crapkit` on PATH outside the environment uv "
                           f"built for this one command ({run}), "), line


def test_under_uv_run_with_the_launcher_count_leaves_uv_s_environments_out(tmp_path, monkeypatch):
    """The repo doctor counted both cache environments as installs beside the
    uv tool one. At another version that WARN told the reader to uninstall or
    upgrade environments uv deletes or rebuilds on its own."""
    run, layer = _under_uv_run_with(tmp_path, monkeypatch)
    kept = shim(tmp_path / "tools", "0.7.6")
    monkeypatch.setenv("PATH", joined(run / BIN, layer / BIN, tmp_path / "tools"))

    assert admin._doctor_launchers() == []
    assert admin._spawned_cli() == (str(kept), "0.7.6")
