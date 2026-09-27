"""plugin/hooks/hooks.json as every harness that loads it reads it.

Claude Code is one reader of the plugin's hooks file among several. Codex
installs the same plugin directory, Cursor imports every Claude Code plugin
enabled under ~/.claude, and GitHub Copilot CLI and VS Code load Claude
plugins as well. Each one keeps the handler fields it knows and drops the rest
without a warning, so an invocation split across fields runs as something else
wherever a field goes missing.

The exec-form handlers did exactly that. `command: "crapkit"` carried the
program and `args` carried `claude-hook --protocol 1`, one handler per file
type behind an `if` filter. Only Claude Code 2.1.139 and later read `args`;
every other reader started a bare `crapkit` per handler, all 50 of them on one
edit where `if` was dropped too, each printing argparse usage and exiting 2.

READERS holds what each harness keeps, read off the harness itself at the
version named. `spawns` replays one edit of a Python file through those rules
and returns the argv of every process the harness would start.
"""
from __future__ import annotations

import fnmatch
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugin"
HOOKS = "hooks/hooks.json"
INVOCATION = ["crapkit", "claude-hook", "--protocol", "1"]
EDITED = "calc/grade.py"


@dataclass(frozen=True)
class Reader:
    name: str
    keeps: frozenset          # the handler fields the harness reads
    tool: str                 # the tool name an edit reaches the matcher as
    matcher: bool = True      # False: the harness runs every handler on every tool
    manifest: str = ""        # a manifest whose "hooks" value replaces hooks/hooks.json


_CLAUDE = frozenset({"type", "command", "args", "if", "timeout", "async", "asyncRewake",
                     "statusMessage"})

READERS = (
    Reader("Claude Code 2.1.139+", _CLAUDE, "Edit"),
    # exec-form `args` arrived in 2.1.139; 2.1.138 honours `if` and drops `args`.
    Reader("Claude Code 2.1.138", _CLAUDE - {"args"}, "Edit"),
    # Codex 0.156.1 reads .codex-plugin/plugin.json first; without a "hooks" key
    # it falls back to hooks/hooks.json and keeps command, timeout, async and
    # statusMessage.
    Reader("Codex 0.156.1", frozenset({"type", "command", "timeout", "async", "statusMessage"}),
           "Edit", manifest=".codex-plugin/plugin.json"),
    # Cursor's Claude Code importer keeps type, command, timeout and matcher and
    # maps Edit and Write both onto its Write tool.
    Reader("Cursor 2026.09.23", frozenset({"type", "command", "timeout"}), "Write"),
    Reader("Copilot CLI 1.0.88", frozenset({"type", "command", "timeout"}), "Edit"),
    # VS Code keeps command, env, timeout and cwd and runs every handler of a
    # matcher group on every tool call.
    Reader("VS Code 1.139.0", frozenset({"type", "command", "timeout", "env", "cwd"}),
           "replace_string_in_file", matcher=False),
)
IDS = [reader.name for reader in READERS]


def _groups(plugin: Path, reader: Reader) -> dict:
    """The event -> matcher groups the harness registers from the plugin."""
    manifest = plugin / reader.manifest if reader.manifest else None
    if manifest is not None and manifest.is_file():
        declared = json.loads(manifest.read_text(encoding="utf-8")).get("hooks")
        if isinstance(declared, dict):
            return declared.get("hooks", declared)
    return json.loads((plugin / HOOKS).read_text(encoding="utf-8"))["hooks"]


def _matches(reader: Reader, matcher: str) -> bool:
    if not reader.matcher or matcher in ("", "*"):
        return True
    return re.fullmatch(matcher, reader.tool) is not None


def _if_allows(reader: Reader, handler: dict) -> bool:
    """Claude Code's `if`: "Edit(*.py)" fires for that tool on a matching file."""
    rule = handler.get("if")
    if not rule or "if" not in reader.keeps:
        return True
    tool, _, pattern = rule.partition("(")
    return tool == reader.tool and fnmatch.fnmatch(Path(EDITED).name, pattern.rstrip(")"))


def _argv(reader: Reader, handler: dict) -> list[str]:
    """What the harness starts: the command line, plus `args` where it keeps them."""
    kept = handler.get("args", []) if "args" in reader.keeps else []
    return [*shlex.split(handler["command"]), *kept]


def spawns(reader: Reader, plugin: Path = PLUGIN) -> list[list[str]]:
    """Every process one edit of EDITED starts in this harness."""
    return [_argv(reader, handler)
            for group in _groups(plugin, reader).get("PostToolUse", [])
            if _matches(reader, group.get("matcher", ""))
            for handler in group["hooks"] if _if_allows(reader, handler)]


@pytest.mark.parametrize("reader", READERS, ids=IDS)
def test_one_edit_starts_at_most_one_claude_hook_in_every_harness(reader):
    started = spawns(reader)

    assert len(started) <= 1, f"{reader.name} starts {len(started)} processes per edit"
    assert all(argv == INVOCATION for argv in started), started


@pytest.mark.parametrize("reader", [r for r in READERS if not r.manifest], ids=lambda r: r.name)
def test_every_harness_that_loads_the_hooks_file_runs_the_advisory(reader):
    """One process per edit, and it is the advisory rather than nothing."""
    assert spawns(reader) == [INVOCATION]


def test_codex_registers_no_crapkit_hook():
    """Codex hands a hook apply_patch's patch text, which names no file_path, so
    the advisory has nothing to judge there. Its manifest's empty `hooks` keeps
    the default hooks/hooks.json out."""
    (codex,) = [reader for reader in READERS if reader.manifest]

    assert spawns(codex) == []


def _exec_form_plugin(root: Path) -> Path:
    """The shape the plugin shipped through 0.8.0: exec form, one `if` per file type."""
    handlers = [{"type": "command", "command": "crapkit",
                 "args": ["claude-hook", "--protocol", "1"], "timeout": 20,
                 "async": True, "asyncRewake": True, "if": f"{tool}(*{ext})"}
                for ext in (".py", ".ts", ".go", ".rs", ".sh") for tool in ("Edit", "Write")]
    (root / "hooks").mkdir(parents=True)
    (root / HOOKS).write_text(json.dumps({"hooks": {"PostToolUse": [
        {"matcher": "Edit|Write", "hooks": handlers}]}}), encoding="utf-8")
    return root


@pytest.mark.parametrize("name,count,argv", [
    ("Claude Code 2.1.139+", 1, INVOCATION),
    ("Claude Code 2.1.138", 1, ["crapkit"]),
    ("Codex 0.156.1", 10, ["crapkit"]),
    ("Cursor 2026.09.23", 10, ["crapkit"]),
    ("VS Code 1.139.0", 10, ["crapkit"]),
])
def test_the_model_sees_what_the_exec_form_shipped(name, count, argv, tmp_path):
    """Guards the tests above: a model that dropped nothing would pass them on
    any hooks file. Replayed through the same rules, the exec-form shape spawns
    the bare `crapkit` every field-dropping reader ran."""
    (reader,) = [r for r in READERS if r.name == name]

    started = spawns(reader, _exec_form_plugin(tmp_path / "plugin"))

    assert (len(started), started[0]) == (count, argv)
