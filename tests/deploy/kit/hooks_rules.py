"""What a harness does with the plugin's hooks/hooks.json, per its profile's [hooks].

The plugin ships one PostToolUse group whose handlers each run
`crapkit claude-hook --protocol 1` through `command` plus `args`, filtered by
an `if` pattern per file type. A harness that drops `args` runs a bare
`crapkit`, which prints its usage and exits 2; a harness that drops `if` runs
every handler on every matched edit; a harness that reads exit 2 as "block"
or "deny" stops the edit the advisory was only meant to comment on.

    handlers = hooks_rules.handlers(plugin_root)
    fired = hooks_rules.fired(profile, plugin_root, tool="Edit", path="calc/grade.py")
    hooks_rules.problems(profile, argv, exit_code)

[hooks] keys: `loads` (plugin, import-claude or none), `manifest` (a
manifest whose "hooks" key replaces the default file), `fields` (the handler
keys the harness keeps), `events`, `matcher` (kept, mapped or dropped) and
`exit2` (feedback, block, deny or ignored).
"""
from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT = "hooks/hooks.json"
BLOCKING = ("block", "deny")


@dataclass(frozen=True)
class Handler:
    event: str
    matcher: str
    entry: dict


def _groups_to_handlers(config: dict) -> list[Handler]:
    return [Handler(event, group.get("matcher", ""), entry)
            for event, groups in config.get("hooks", config).items()
            for group in groups for entry in group.get("hooks", [])]


def handlers(plugin_root: Path, relative: str = DEFAULT) -> list[Handler]:
    """Every handler the file declares, in file order."""
    return _groups_to_handlers(json.loads((plugin_root / relative).read_text(encoding="utf-8")))


def _declared(rules: dict, plugin_root: Path):
    """The manifest's "hooks" value when the harness reads one and it names
    hooks; None sends the harness to the default file."""
    manifest = plugin_root / rules.get("manifest", "") if rules.get("manifest") else None
    if manifest is None or not manifest.is_file():
        return None
    return json.loads(manifest.read_text(encoding="utf-8")).get("hooks")


def registered(profile, plugin_root: Path) -> list[Handler]:
    """The handlers the harness registers from the plugin."""
    rules = profile.hooks
    if rules["loads"] == "none":
        return []
    declared = _declared(rules, plugin_root)
    if isinstance(declared, dict):
        return _groups_to_handlers(declared)
    return handlers(plugin_root, declared.lstrip("./") if isinstance(declared, str) else DEFAULT)


def _matches(profile, handler: Handler, tool: str) -> bool:
    if profile.hooks["matcher"] == "dropped" or handler.matcher in ("", "*"):
        return True
    return re.fullmatch(handler.matcher, tool) is not None


def _if_allows(profile, handler: Handler, tool: str, path: str) -> bool:
    """Claude Code's `if`: "Edit(*.py)" fires for that tool on a matching file."""
    rule = handler.entry.get("if")
    if not rule or "if" not in profile.hooks["fields"]:
        return True
    name, _, pattern = rule.partition("(")
    return name == tool and fnmatch.fnmatch(Path(path).name, pattern.rstrip(")"))


def fired(profile, plugin_root: Path, *, tool: str, path: str, event: str = "PostToolUse") -> list[Handler]:
    """The handlers that run when the harness's agent uses `tool` on `path`."""
    if event not in profile.hooks["events"]:
        return []
    return [handler for handler in registered(profile, plugin_root) if _fires(profile, handler, event, tool, path)]


def _fires(profile, handler: Handler, event: str, tool: str, path: str) -> bool:
    if handler.event != event:
        return False
    return _matches(profile, handler, tool) and _if_allows(profile, handler, tool, path)


def argv(profile, handler: Handler) -> list[str]:
    """What the harness spawns: the command, plus `args` only when it keeps them."""
    kept = handler.entry.get("args", []) if "args" in profile.hooks["fields"] else []
    return [handler.entry["command"], *kept]


def problems(profile, spawned: list[str], exit_code: int) -> list[str]:
    found = []
    if spawned == ["crapkit"]:
        found.append(f"{profile.name} spawns a bare `crapkit`: it prints its usage and exits {exit_code}")
    if exit_code == 2 and profile.hooks["exit2"] in BLOCKING:
        found.append(f"{profile.name} reads exit 2 as {profile.hooks['exit2']}: the edit is stopped")
    return found


def payload(repo: Path, relative: str, tool: str = "Edit") -> dict:
    """A PostToolUse event for an edit of repo/relative, as Claude Code sends it."""
    path = str(repo / relative)
    return {"session_id": "deploy-cell", "transcript_path": str(repo / ".transcript.jsonl"), "cwd": str(repo),
            "hook_event_name": "PostToolUse", "tool_name": tool,
            "tool_input": {"file_path": path, "old_string": "", "new_string": ""},
            "tool_response": {"filePath": path, "success": True}}
