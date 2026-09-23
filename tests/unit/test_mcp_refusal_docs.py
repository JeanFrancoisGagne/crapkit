"""The pages quote the MCP argument refusals the server prints, word for word.

An agent matches a refusal against the documented sentence. The pages quoted
`brief needs name (see inputSchema.required)` and `worklist does not take
'bogus'`, CLI command names the server never prints: it names the MCP tool,
`get_function_brief` and `list_worklist`. Every quote below is compared with
the text `tools/call` answers.
"""
import re
from pathlib import Path

import pytest

from crapkit import mcp_server

ROOT = Path(__file__).resolve().parent.parent.parent
PAGES = {"AGENTS.md": "## The MCP server", "docs/agent-json.md": "## MCP server"}
_REFUSAL = re.compile(r"`([^`]*(?:\(see inputSchema\.required\)|does not take '[^`]*|"
                      r"must be an? [^`]*\(got [^`]*\)))`")
_REQUIRES = re.compile(r"\(((?:`[a-z_]+`(?:,| and)? ?)+) require `path` and `name`\)")


def _section(page: str) -> str:
    text = (ROOT / page).read_text(encoding="utf-8")
    return text.split(f"\n{PAGES[page]}\n", 1)[1].split("\n## ", 1)[0]


def _answer(tmp_path: Path, tool: str, arguments: dict) -> str:
    result = mcp_server._call_tool(tmp_path, tool, arguments)
    assert result["isError"] is True
    return result["content"][0]["text"]


@pytest.fixture(scope="module")
def spoken(tmp_path_factory) -> set[str]:
    """Every refusal sentence the pages could be quoting, as the server says it."""
    root = tmp_path_factory.mktemp("mcp")
    probes = [(tool["name"], {}) for tool in mcp_server.TOOLS if tool["positional"]]
    probes += [(tool["name"], {"path": "a.py"}) for tool in mcp_server.TOOLS
               if "name" in tool["positional"]]
    probes += [("list_worklist", {"bogus": 1}), ("list_worklist", {"top": "three"})]
    return {_answer(root, tool, arguments) for tool, arguments in probes}


@pytest.mark.parametrize("page", sorted(PAGES))
def test_every_quoted_refusal_is_one_the_server_prints(page, spoken):
    quoted = _REFUSAL.findall(_section(page))

    assert quoted, f"{page} quotes no refusal; the pattern lost it"
    assert [q for q in quoted if q not in spoken] == []


def test_the_tools_named_as_requiring_path_and_name_are_the_ones_that_do():
    named = _REQUIRES.search(" ".join(_section("docs/agent-json.md").split()))
    requiring = {t["name"] for t in mcp_server.tool_listing()
                 if t["inputSchema"].get("required") == ["path", "name"]}

    assert named, "the page no longer says which tools require path and name"
    assert set(re.findall(r"`([a-z_]+)`", named.group(1))) == requiring
