"""docs/harnesses.md, held to what live runs found each agent needs before
crapkit's tools reach its model.

crapkit once gave one `mcpServers` block to every agent but Claude Code and
Codex. Pasted as it was, OpenCode 1.18.32 and Amp printed "No MCP servers
configured" and exited 0, VS Code 1.139.0 started nothing from
`.vscode/mcp.json` and logged nothing, and Gemini CLI 0.61.0 connected the
server while `gemini -p` handed the model none of its twelve tools. Goose
1.52.0's `goose plugin install` found no plugin in crapkit's repository. Each
test pins the key, field or sentence one of those runs established, so an edit
that drops it fails here instead of in a reader's agent.
"""
from __future__ import annotations

import argparse
import json
import re
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

from crapkit.cli.parser import build_parser

ROOT = Path(__file__).resolve().parents[2]
PAGE = "docs/harnesses.md"
FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.M | re.S)
PARSE = {"json": json.loads, "yaml": yaml.safe_load}
# The bare mcpServers entry: what an agent that takes the block "as it is" reads.
BARE_ENTRY_KEYS = {"type", "command", "args"}


@lru_cache(maxsize=None)
def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def section(heading: str) -> str:
    """The body under `## heading`, down to the next `## `."""
    parts = re.split(r"^## (.+)$", read(PAGE), flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))[heading]


def first_block(heading: str) -> dict:
    """The section's first fenced block, parsed in its own format."""
    lang, text = FENCE.search(section(heading)).groups()
    return PARSE[lang](text)


# The top-level key each agent reads its servers from. OpenCode, Amp and VS
# Code ignored an `mcpServers` block without a word.
SERVER_KEY = {"OpenCode": "mcp", "Amp": "amp.mcpServers", "VS Code with GitHub Copilot": "servers",
              "Goose": "extensions", "Gemini CLI": "mcpServers", "Cline": "mcpServers"}


@pytest.mark.parametrize("harness", sorted(SERVER_KEY))
def test_each_block_puts_crapkit_under_the_key_its_agent_reads(harness):
    config = first_block(harness)

    assert set(config) - {"$schema"} == {SERVER_KEY[harness]}
    assert "crapkit" in config[SERVER_KEY[harness]]


def test_opencode_takes_the_command_and_its_arguments_as_one_array():
    entry = first_block("OpenCode")["mcp"]["crapkit"]

    assert entry["command"][:2] == ["crapkit", "mcp"] and "args" not in entry


def test_gemini_trusts_the_server_so_a_headless_run_offers_its_tools():
    """`gemini -p` in the default approval mode drops each tool that would ask
    for a confirmation: 0 of 12 reached the model without `trust`, 12 with it."""
    assert first_block("Gemini CLI")["mcpServers"]["crapkit"]["trust"] is True


def test_cline_gives_the_server_a_minute_to_answer_initialize():
    """Cline 3.0.65 waits 3 s for initialize unless `timeout` (seconds) is set."""
    assert first_block("Cline")["mcpServers"]["crapkit"]["timeout"] == 60


# What a live run found, as the words a reader needs in the section.
MEASURED = [
    ("Gemini CLI", "GEMINI_CLI_TRUST_WORKSPACE"),  # an untrusted folder disables every server
    ("Gemini CLI", "subdirectory"),  # a project file is read only from its own directory
    ("Gemini CLI", "gemini mcp add -s user --trust crapkit"),
    ("Gemini CLI", "5 s"),  # `gemini mcp list` gives each server 5 s
    ("VS Code with GitHub Copilot", "`mcpServers`"),  # a block keyed so there starts nothing
    ("VS Code with GitHub Copilot", "spawn crapkit ENOENT"),  # a desktop launch takes the login shell's PATH
    ("OpenCode", "No MCP servers configured"),
    ("Amp", "No MCP servers configured"),
    ("Goose", "No supported plugin format found"),
    ("Cline", "cline.log"),
    ("Qwen Code", "Pending approval"),
    ("Zed", "crapkit: not found"),  # PATH comes from the login shell
    ("Junie", ".output.txt"),
    ("Kiro", "sign in"),
    ("Windsurf", "sign in"),
    # `claude -p` refuses each crapkit call it has no permission for, in both routes
    ("Claude Code", "--allowedTools mcp__crapkit"),
    ("Claude Code", "--allowedTools mcp__plugin_crapkit_crapkit"),
    ("Claude Agent SDK", "allowed_tools=[\"mcp__crapkit\"]"),
]


def test_the_agent_sdk_example_lets_the_model_call_crapkit():
    """The example as printed ran, and each call came back as a permission refusal."""
    example = FENCE.findall(section("Claude Agent SDK"))[1]

    assert example[0] == "ts"
    assert 'allowedTools: ["mcp__crapkit"]' in example[1]


@pytest.mark.parametrize("harness, fact", MEASURED)
def test_each_section_says_what_a_live_run_found(harness, fact):
    assert fact in section(harness)


def rows_named(harness: str, *names: str) -> list[str]:
    """The section's table rows whose first cell is one of `names`."""
    return [line for line in section(harness).splitlines() if line.startswith(names)]


@pytest.mark.parametrize("harness", ["oh-my-pi", "Zed"])
def test_where_the_server_starts_is_measured_not_guessed(harness):
    rows = rows_named(harness, "| Starts in |", "| Environment |")

    assert len(rows) == 2
    assert "Not measured" not in "".join(rows), rows


def pastes_as_is() -> list[str]:
    """The agents agent-json.md says take its mcpServers block unchanged."""
    found = re.search(r"It pastes as it is into (.+?);", read("docs/agent-json.md"), re.S)
    assert found, "agent-json.md no longer names the agents its block pastes into"
    names = re.split(r",\s*|\s+and\s+", " ".join(found[1].split()))
    return [re.sub(r"'s `.*`$", "", name) for name in names]


def test_every_agent_the_client_wiring_names_takes_the_bare_block():
    """An agent named there whose own section needs another key or field gets
    the block that does not work for it."""
    names = pastes_as_is()

    assert names, "the Client wiring paragraph names no agent"
    for name in names:
        servers = first_block(name)["mcpServers"]
        assert set(servers["crapkit"]) <= BARE_ENTRY_KEYS, name


@pytest.mark.parametrize("page", ["README.md", "AGENTS.md", "docs/adoption.md", "docs/upgrading.md"])
def test_the_pages_that_send_other_agents_to_mcp_wiring_send_them_to_the_harness_page(page):
    text = read(page)

    assert "harnesses.md" in text
    assert not re.search(r"\[stdio setup\]\([^)]*agent-json\.md#mcp-server\)", text), \
        "a link that still calls agent-json.md's one block the setup for every client"


def test_mcp_help_names_the_page_with_each_agents_block():
    subparsers = next(action for action in build_parser()._actions
                      if isinstance(action, argparse._SubParsersAction))

    assert "docs/harnesses.md" in subparsers.choices["mcp"].format_help()
