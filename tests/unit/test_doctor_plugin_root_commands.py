"""Every line `crapkit doctor --plugin-root` prints names the command that closes it.

`doctor --help` promised that, and three lines named no command: a hook asking
for a protocol this CLI does not answer ("so `claude-hook` exits 0 silent on
every edit", and nothing more), a hooks file doctor cannot read ("reinstall the
plugin or repair that file"), and a root with no manifest. A launcher that
answers no `--version` got "Repair this launcher". Each line now names the
command for the harness, scope and directory the install is in.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import crapkit
from crapkit.cli import admin, main
from crapkit.doctor import InPlace, InstallScope, plugin_handshake
from test_doctor_plugin_root import ON_PATH, _write, plugin
from test_launchers import shim

CLI = crapkit.__version__
UPGRADE = "python -m pip install --upgrade crapkit"
CLAUDE_UPDATE = ("update it with `claude plugin marketplace update crapkit`, then `claude plugin "
                 "update crapkit@crapkit --scope user`, and restart Claude Code's sessions.")
CLAUDE_REINSTALL = ("reinstall it with `claude plugin uninstall crapkit@crapkit --scope user`, then "
                    "`claude plugin install crapkit@crapkit --scope user`, and restart Claude Code's "
                    "sessions")


RESOLVE = admin._spawned_cli


@pytest.fixture(autouse=True)
def _agreeing_path_crapkit(monkeypatch):
    RESOLVE.cache_clear()
    monkeypatch.setattr(admin, "_spawned_cli", lambda: (ON_PATH, CLI))
    yield
    RESOLVE.cache_clear()


def check(root: Path, capsys) -> tuple[int, list[str]]:
    code = main(["doctor", "--plugin-root", str(root)])
    return code, capsys.readouterr().out.splitlines()


def _lines(**kw) -> list[str]:
    args = {"where": "/plugins/crapkit", "version": "0.4.0", "cli_version": "0.4.0",
            "cli_where": ON_PATH, "protocols": ("1",), "supported": "1", "cli_upgrade": UPGRADE}
    return plugin_handshake(**{**args, **kw})


# --- a protocol this CLI does not answer ------------------------------------------------

def test_a_plugin_asking_a_newer_protocol_names_the_cli_upgrade(tmp_path, capsys):
    code, lines = check(plugin(tmp_path / "p", protocol="2"), capsys)

    assert code == 1
    assert lines == [f"crapkit doctor: the plugin at {tmp_path / 'p'} asks for hook protocol 2; "
                     "this crapkit answers 1, so `claude-hook` exits 0 silent on every edit. The "
                     f"CLI is behind; upgrade it with `{UPGRADE}`."]


def test_a_plugin_asking_an_older_protocol_names_the_plugin_update():
    (line,) = _lines(protocols=("0",))

    assert line.endswith("so `claude-hook` exits 0 silent on every edit. The plugin is behind; "
                         + CLAUDE_UPDATE), line


@pytest.mark.parametrize("protocols", [("0", "2"), ("two",)], ids=["both-sides", "not-a-number"])
def test_protocols_that_do_not_order_name_both_repairs(protocols):
    (line,) = _lines(protocols=protocols)

    assert "Update whichever is behind: the plugin with `claude plugin marketplace update" in line
    assert line.endswith(f"the CLI with `{UPGRADE}`."), line


# --- a hooks file doctor cannot read ----------------------------------------------------

def test_a_plugin_with_no_hooks_file_names_the_reinstall(tmp_path, capsys):
    code, lines = check(plugin(tmp_path / "p", protocol=None), capsys)

    assert code == 1
    assert lines == [f"crapkit doctor: the plugin at {tmp_path / 'p'} has no readable "
                     f"hooks/hooks.json; {CLAUDE_REINSTALL} before relying on its advisory hook."]


def test_the_hooks_reinstall_runs_once_per_scope_in_its_project():
    (line,) = _lines(protocols=None, scopes=(InstallScope("project", "/work/app"),
                                             InstallScope("user")))

    assert ("reinstall it with `claude plugin uninstall crapkit@crapkit --scope project`, then "
            "`claude plugin install crapkit@crapkit --scope project` (run in /work/app); `claude "
            "plugin uninstall crapkit@crapkit --scope user`, then `claude plugin install "
            "crapkit@crapkit --scope user`, and restart") in line, line


def test_a_codex_plugin_with_no_readable_hooks_is_reinstalled_through_codex():
    (line,) = _lines(protocols=None, harness="codex")

    assert line.endswith("reinstall it with `codex plugin remove crapkit@crapkit`, then `codex "
                         "plugin add crapkit@crapkit`, and start a new Codex task before relying "
                         "on its advisory hook."), line


def test_a_plugin_loaded_in_place_from_a_checkout_restores_the_file_from_git():
    """Claude Code runs a local directory marketplace's plugin from that
    directory, so a reinstall refreshes a cache copy nobody runs."""
    in_place = InPlace("/src/mk", "git -C /src/mk pull", "git -C /src/mk/plugin checkout --")

    (line,) = _lines(where="/src/mk/plugin", protocols=None, in_place=in_place)

    assert line.endswith("restore it with `git -C /src/mk/plugin checkout -- hooks/hooks.json` "
                         "(Claude Code loads it in place from the local directory marketplace at "
                         "/src/mk, and `claude plugin update` does not change it), and restart "
                         "Claude Code's sessions before relying on its advisory hook."), line


def test_a_plugin_loaded_in_place_from_a_plain_directory_restores_the_file_by_copy():
    (line,) = _lines(where="/src/mk/plugin", protocols=None, in_place=InPlace("/src/mk"))

    assert ("restore it by copying crapkit 0.4.0's plugin/hooks/hooks.json to hooks/hooks.json "
            "under /src/mk/plugin (Claude Code loads it in place") in line, line


# --- a root with no manifest ------------------------------------------------------------

def test_a_directory_with_no_manifest_names_the_search_that_finds_the_installs(tmp_path, capsys):
    code, lines = check(plugin(tmp_path / "p", manifest=False), capsys)

    assert code == 1
    assert lines == [f"crapkit doctor: the plugin at {tmp_path / 'p'} has no "
                     ".claude-plugin/plugin.json, so it is no plugin root; name the plugin root or "
                     "a directory above it, or run `crapkit doctor --plugin-root` with no PATH to "
                     "check the installs Claude Code and Codex recorded."]


def test_a_manifest_doctor_cannot_read_names_the_reinstall(tmp_path, capsys):
    root = plugin(tmp_path / "p")
    _write(root / ".claude-plugin" / "plugin.json", "{not json")

    code, lines = check(root, capsys)

    assert code == 1
    assert lines == [f"crapkit doctor: the plugin at {root} has no readable "
                     f".claude-plugin/plugin.json, so it has no version to compare; "
                     f"{CLAUDE_REINSTALL}."]


# --- a launcher that answers no version -------------------------------------------------

def test_a_launcher_that_answers_no_version_names_the_reinstall_of_its_install(tmp_path, capsys,
                                                                             monkeypatch):
    """`uv tool upgrade` sees crapkit current and leaves a launcher whose
    environment lost its python as broken as it was; the reinstall repairs it."""
    launcher = shim(tmp_path / "uv" / "tools" / "crapkit" / ("Scripts" if os.name == "nt" else "bin"))
    monkeypatch.setattr(admin, "_spawned_cli", lambda: (str(launcher), None))

    code, lines = check(plugin(tmp_path / "p"), capsys)

    assert code == 1
    assert lines == [f"crapkit doctor: FAIL {launcher} did not answer `crapkit --version`. Reinstall "
                     "the crapkit it belongs to with `uv tool install --force crapkit`, then run "
                     "this check again."]
