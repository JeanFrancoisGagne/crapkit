"""The pages quote the MCP argument refusals the server prints, word for word.

An agent matches a refusal against the documented sentence. The pages quoted
`brief needs name (see inputSchema.required)` and `worklist does not take
'bogus'`, CLI command names the server never prints: it names the MCP tool,
`get_function_brief` and `list_worklist`. ADR 0001 quoted the same CLI name.
Every quote below is compared with
the text `tools/call` answers, or with the -32602 message tools/call and
initialize answer for params that are not an object.
"""
import re
from pathlib import Path

import pytest

from crapkit import mcp_server

ROOT = Path(__file__).resolve().parent.parent.parent
PAGES = {"AGENTS.md": "## The MCP server", "docs/agent-json.md": "## MCP server",
         "docs/adr/0001-mcp-invalid-arguments-are-tool-results.md":
             "# Invalid MCP tool arguments answer as a tool result, not a protocol error"}
_REFUSAL = re.compile(r"`([^`]*(?:\(see inputSchema\.required\)|does not take '[^`]*|"
                      r"must be an? [^`]*\(got [^`]*\)))`")
_REQUIRES = re.compile(r"\(((?:`[a-z_]+`(?:,| and)? ?)+) require `path` and `name`\)")


def _section(page: str) -> str:
    text = (ROOT / page).read_text(encoding="utf-8")
    return text.split(f"\n{PAGES[page]}\n", 1)[1].split("\n## ", 1)[0]


def _answer(tmp_path: Path, tool: str, arguments) -> str:
    result = mcp_server._call_tool(tmp_path, tool, arguments)
    assert result["isError"] is True
    return result["content"][0]["text"]


def _probes() -> list[tuple[str, dict]]:
    """A missing first positional and a missing `name` on every tool that takes
    them, then an undeclared key and a wrong type."""
    missing = [(tool["name"], {}) for tool in mcp_server.TOOLS if tool["positional"]]
    unnamed = [(tool["name"], {"path": "a.py"}) for tool in mcp_server.TOOLS
               if "name" in tool["positional"]]
    return missing + unnamed + [("list_worklist", {"bogus": 1}),
                                ("list_worklist", {"top": "three"}),
                                ("list_runs", 3), ("list_runs", [1]), ("list_runs", ""),
                                ("list_runs", 0), ("list_runs", False), ("list_runs", [])]


def _invalid_params(root: Path, method: str, params) -> str:
    reply = mcp_server._handle(root, {"jsonrpc": "2.0", "id": 1, "method": method,
                                      "params": params})
    assert reply["error"]["code"] == -32602, reply
    return reply["error"]["message"]


@pytest.fixture(scope="module")
def spoken(tmp_path_factory) -> set[str]:
    """Every refusal sentence the pages could be quoting, as the server says it."""
    root = tmp_path_factory.mktemp("mcp")
    invalid = {_invalid_params(root, method, params)
               for method in ("tools/call", "initialize") for params in ([1], "x", 7, False)}
    return {_answer(root, tool, arguments) for tool, arguments in _probes()} | invalid


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


# ADR 0001 listed "an unparsable frame" among the protocol errors, but the
# server answers such a frame with nothing and reads the next line. A client
# that waits on an error for a junk line waits forever, so each page says what
# the server does, and the 0.8.1 amendment corrects the ADR's own list.
_FRAME_SENTENCE = re.compile(r"[^.]*\bframe\b[^.]*\.")


def _frame_sentences(text: str) -> list[str]:
    return _FRAME_SENTENCE.findall(" ".join(text.split()))


@pytest.mark.parametrize("frame", ["junk", "[1, 2]", '"ping"', "{", "null"])
def test_a_frame_that_is_not_one_json_object_gets_no_reply(frame):
    assert mcp_server._parse(frame) is None


@pytest.mark.parametrize("page", sorted(PAGES))
def test_every_page_says_a_frame_that_is_not_one_json_object_gets_no_reply(page):
    text = _section(page)
    if page.startswith("docs/adr/"):
        text = text.split("Amended in 0.8.1.", 1)[1]

    sentences = _frame_sentences(text)

    assert sentences, f"{page} says nothing about a frame that is not JSON"
    assert [s for s in sentences if "no reply" not in s] == []
