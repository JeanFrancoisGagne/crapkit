"""lin-cursor-claude-import: what the Cursor agent makes of a Claude Code
plugin already installed in the same home.

The Cursor agent bundles a loader for Claude Code plugins: it reads
~/.claude/plugins/installed_plugins.json and converts each plugin's hooks,
keeping `command`, `matcher` and `timeout` only (the evidence in the cursor
profile). This cell installs crapkit's plugin with the README's two lines,
then asks the agent what it lists. In 2026.09.23 `agent mcp list` shows no
server from the Claude plugin, so a Cursor user still needs the
.cursor/mcp.json config; the cell then adds that config and checks the agent
lists crapkit's twelve tools once.
"""
from __future__ import annotations

import re
from pathlib import Path

from kit import docsnip, gitmirror, profiles
from kit.cells import cell

PACKET = "deploy-harnesses"
TOOLS = 12
NOTHING = "No MCP servers configured"


def install_claude_plugin(box, repo: Path, candidate) -> None:
    gitmirror.make(box).publish(candidate.staged, candidate.version)
    for line in docsnip.commands(docsnip.fence("README.md", "The Claude Code plugin")):
        box.script(line, cwd=repo, expect=0)


def cursor_servers(box, repo: Path) -> str:
    return box.run(["cursor-agent", "mcp", "list"], cwd=repo, expect=0).stdout


@cell("lin-cursor-claude-import", channel="Claude plugin in sandbox ~/.claude", harness="Cursor agent",
      scenario="fresh: agent mcp list and hook listing show the imported plugin", use_cases="hook portability",
      os="linux", image="core", cadence="nightly")
def test_cursor_claude_import(box, templates, candidate, record_property):
    repo = profiles.real_cli_box(box, templates)
    install_claude_plugin(box, repo, candidate)
    installed = Path(box.env["CLAUDE_CONFIG_DIR"]) / "plugins" / "installed_plugins.json"
    assert "crapkit@crapkit" in installed.read_text(encoding="utf-8")

    listed = cursor_servers(box, repo)
    record_property("cursor_imports_claude_mcp", str(NOTHING not in listed).lower())
    assert NOTHING in listed, f"the agent now imports the Claude plugin's server; update the cursor profile:\n{listed}"

    profiles.doc_server(box, repo, "cursor")
    box.run(["cursor-agent", "mcp", "enable", "crapkit"], cwd=repo, expect=0)
    tools = box.run(["cursor-agent", "mcp", "list-tools", "crapkit"], cwd=repo, expect=0).stdout
    assert len(re.findall(r"^- (\w+) \(", tools, re.M)) == TOOLS
    assert len(re.findall(r"^crapkit\b", cursor_servers(box, repo), re.M)) == 1
