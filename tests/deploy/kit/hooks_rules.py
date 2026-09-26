"""What a harness does with the plugin's hooks/hooks.json, per its profile's [hooks].

The plugin ships one PostToolUse handler, `crapkit claude-hook --protocol 1`
in shell form, on Edit and Write; claude-hook itself skips a file no lane
measures. Releases up to 0.8.0 shipped 50 exec-form handlers instead: `crapkit`
in `command`, the rest in `args`, and an `if` pattern per file type. A harness
that drops `args` from one of those runs a bare `crapkit`, which prints its
usage and exits 2; a harness that drops `if` runs every handler on every
matched edit. A harness that reads exit 2 as "block" or "deny" stops the edit
the advisory was only meant to comment on.

    handlers = hooks_rules.handlers(plugin_root)
    spawned = hooks_rules.spawns(profile, plugin_root, [("Edit", "calc/grade.py")])
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
import shlex
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


def parse(text: str) -> list[Handler]:
    """Every handler a hooks.json text declares, in file order."""
    return _groups_to_handlers(json.loads(text))


def handlers(plugin_root: Path, relative: str = DEFAULT) -> list[Handler]:
    """Every handler the file declares, in file order."""
    return parse((plugin_root / relative).read_text(encoding="utf-8"))


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
    """What the harness spawns: the command line as its shell splits it, plus
    `args` only when it keeps them."""
    kept = handler.entry.get("args", []) if "args" in profile.hooks["fields"] else []
    return [*shlex.split(handler.entry["command"]), *kept]


def spawns(profile, plugin_root: Path, edits) -> list[tuple[str, str, list[str]]]:
    """(tool, path, argv) for every handler the harness runs on each
    (tool, path) edit, in edit order then file order."""
    return [(tool, path, argv(profile, handler)) for tool, path in edits
            for handler in fired(profile, plugin_root, tool=tool, path=path)]


def problems(profile, spawned: list[str], exit_code: int) -> list[str]:
    found = []
    if spawned == ["crapkit"]:
        found.append(f"{profile.name} spawns a bare `crapkit`: it prints its usage and exits {exit_code}")
    if exit_code == 2 and profile.hooks["exit2"] in BLOCKING:
        found.append(f"{profile.name} reads exit 2 as {profile.hooks['exit2']}: the edit is stopped")
    return found


def payload(repo: Path, relative: str, tool: str = "Edit", profile=None) -> dict:
    """A PostToolUse event for an edit of repo/relative, as the profile's
    harness sends it; Claude Code's shape when it has no dialect of its own."""
    path = str(repo / relative)
    event = {"session_id": "deploy-cell", "transcript_path": str(repo / ".transcript.jsonl"), "cwd": str(repo),
             "hook_event_name": "PostToolUse", "tool_name": tool,
             "tool_input": {"file_path": path, "old_string": "", "new_string": ""},
             "tool_response": {"filePath": path, "success": True}}
    return {**event, **DIALECTS[profile.key](path)} if profile is not None and profile.key in DIALECTS else event


# Where a harness's PostToolUse event differs from Claude Code's, read off the
# harness: Copilot CLI 1.0.88 (a captured payload), Cursor 2026.09.23 and
# VS Code 1.139.0 (their bundles).
DIALECTS = {
    "copilot-cli": lambda path: {"tool_name": "Edit", "tool_input": {"path": path, "old_str": "", "new_str": ""},
                                 "tool_result": {"result_type": "success"}},
    "cursor": lambda path: {"hook_event_name": "postToolUse", "tool_name": "Write",
                            "tool_input": {"file_path": path, "content": ""}},
    "vscode-copilot": lambda path: {"tool_name": "replace_string_in_file",
                                    "tool_input": {"filePath": path, "oldString": "", "newString": ""}},
}


def heard(profile, step) -> bool:
    """Whether the advisory went out where the harness hands text to its model.
    A harness with a dialect of its own reads it as JSON on exit 0: Cursor
    denies on exit 2, VS Code blocks, and Copilot CLI 1.0.88 shows exit 2's
    stderr to the user alone (a stub-model session heard only a top-level
    additionalContext). Claude Code reads exit 2's stderr. True where exit 2 is
    ignored and no dialect is known, since that harness reads neither."""
    if profile.key in DIALECTS:
        return step.exit == 0 and "crapkit advisory" in _json_text(step.stdout)
    return profile.hooks["exit2"] != "feedback" or (step.exit == 2 and "crapkit advisory" in step.stderr)


def _json_text(stdout: str) -> str:
    """stdout read as the one JSON object a harness parses; a line that is not JSON raises."""
    return json.dumps(json.loads(stdout or "{}"))
