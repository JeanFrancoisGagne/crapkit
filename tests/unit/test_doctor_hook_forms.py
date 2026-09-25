"""`doctor --plugin-root` reads a hook in either form the plugin may ship.

Exec form puts the protocol in `args` (`command: crapkit`, `args: [claude-hook,
--protocol, 1]`), and Claude Code passes `args` only from 2.1.139 on. Shell form
puts it in the command string (`command: crapkit claude-hook --protocol 1`),
which every Claude Code release runs as written. The protocol check read `args`
alone, so a shell-form handler asking for a protocol this CLI does not answer
passed; and the 2.1.139 floor line applies to exec form only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import crapkit
from crapkit.cli import admin, main
from test_doctor_plugin_root import ON_PATH, _write

CLI = crapkit.__version__
RESOLVE, PROBE = admin._spawned_cli, admin._claude_code_version
OLD_CLAUDE = ("/usr/local/bin/claude", "2.1.138 (Claude Code)")


@pytest.fixture(autouse=True)
def _agreeing_path_crapkit(monkeypatch):
    RESOLVE.cache_clear()
    PROBE.cache_clear()
    monkeypatch.setattr(admin, "_spawned_cli", lambda: (ON_PATH, CLI))
    monkeypatch.setattr(admin, "_claude_code_version", lambda: None)
    yield
    RESOLVE.cache_clear()
    PROBE.cache_clear()


def shell_form(protocol: str) -> dict:
    return {"type": "command", "command": f"crapkit claude-hook --protocol {protocol}", "timeout": 20}


def exec_form(protocol: str) -> dict:
    return {"type": "command", "command": "crapkit", "args": ["claude-hook", "--protocol", protocol]}


def plugin_with(root: Path, *handlers: dict) -> Path:
    _write(root / ".claude-plugin" / "plugin.json", {"name": "crapkit", "version": CLI})
    _write(root / "hooks" / "hooks.json",
           {"hooks": {"PostToolUse": [{"matcher": "Edit|Write", "hooks": list(handlers)}]}})
    return root


def check(root: Path, capsys) -> tuple[int, list[str]]:
    code = main(["doctor", "--plugin-root", str(root)])
    return code, capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("form", [shell_form, exec_form], ids=["shell", "exec"])
def test_a_protocol_this_cli_answers_is_silent_in_either_form(tmp_path, capsys, form):
    assert check(plugin_with(tmp_path / "p", form("1")), capsys) == (0, [])


@pytest.mark.parametrize("form", [shell_form, exec_form], ids=["shell", "exec"])
def test_a_protocol_this_cli_does_not_answer_is_named_in_either_form(tmp_path, capsys, form):
    code, lines = check(plugin_with(tmp_path / "p", form("99")), capsys)

    assert code == 1
    assert lines == [f"crapkit doctor: the plugin at {tmp_path / 'p'} asks for hook protocol 99; "
                     "this crapkit answers 1, so `claude-hook` exits 0 silent on every edit."]


@pytest.mark.parametrize("command", ["crapkit claude-hook --protocol=99",
                                     "crapkit claude-hook --protocol '99'"])
def test_the_shell_form_is_read_the_way_the_shell_splits_it(tmp_path, capsys, command):
    handler = {"type": "command", "command": command}

    code, lines = check(plugin_with(tmp_path / "p", handler), capsys)

    assert code == 1 and "protocol 99" in lines[0], lines


def test_a_shell_form_command_the_shell_cannot_split_is_an_unreadable_hooks_file(tmp_path, capsys):
    handler = {"type": "command", "command": "crapkit claude-hook --protocol '1"}

    code, lines = check(plugin_with(tmp_path / "p", handler), capsys)

    assert code == 1 and "no readable hooks/hooks.json" in lines[0], lines


def test_below_the_floor_an_exec_form_plugin_gets_the_floor_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(admin, "_claude_code_version", lambda: OLD_CLAUDE)

    code, lines = check(plugin_with(tmp_path / "p", exec_form("1")), capsys)

    assert code == 1
    assert lines == [f"crapkit doctor: Claude Code 2.1.138 ({OLD_CLAUDE[0]}) predates 2.1.139, the "
                     "first release that passes a hook's args, so each of the plugin's hooks starts "
                     "a bare `crapkit`, which exits 2 with its usage on every edit. Update Claude "
                     "Code (`claude update`), then restart its sessions."]


def test_below_the_floor_a_shell_form_plugin_runs_as_written_and_says_nothing(tmp_path, capsys,
                                                                             monkeypatch):
    """Shell form carries no args for an old Claude Code to drop."""
    monkeypatch.setattr(admin, "_claude_code_version", lambda: OLD_CLAUDE)

    assert check(plugin_with(tmp_path / "p", shell_form("1")), capsys) == (0, [])
