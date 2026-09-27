"""`doctor --plugin-root` against the hook form the plugin now ships.

plugin/hooks/hooks.json carries the whole invocation in one shell-form
`command`, `crapkit claude-hook --protocol 1`, because every agent that loads
the plugin keeps `command` and most drop `args`. doctor's protocol check reads
`--protocol` from either form: a check that read `args` alone found no protocol
in the shipped form, took argparse's default, and passed a plugin that asks for
a protocol this CLI does not answer.
"""
from __future__ import annotations

import json
from pathlib import Path

from crapkit.cli import admin

ROOT = Path(__file__).resolve().parents[2]


def _hooks(root: Path, handler: dict) -> Path:
    path = root / "hooks" / "hooks.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"hooks": {"PostToolUse": [
        {"matcher": "Edit|Write", "hooks": [handler]}]}}), encoding="utf-8")
    return root


def test_an_exec_form_handler_names_its_protocol(tmp_path):
    handler = {"type": "command", "command": "crapkit", "args": ["claude-hook", "--protocol", "2"]}

    assert admin._hook_protocols(_hooks(tmp_path, handler)) == ("2",)


def test_a_shell_form_handler_names_its_protocol(tmp_path):
    handler = {"type": "command", "command": "crapkit claude-hook --protocol 2"}

    assert admin._hook_protocols(_hooks(tmp_path, handler)) == ("2",)


def test_doctor_finds_protocol_1_in_the_hooks_file_the_plugin_ships():
    assert admin._hook_protocols(ROOT / "plugin") == ("1",)
