"""A key a client adds to every tool for its own scheduler is not refused.

Gemini CLI 0.61.0 adds a `wait_for_previous` boolean to the input schema of
every MCP tool it shows its model, tells the model to set it on a call that
depends on an earlier one, reads it to order the calls, and then forwards the
arguments unchanged. crapkit refuses an undeclared key (ADR 0001), so every
Gemini call that carried it came back as `get_next_item does not take
'wait_for_previous'`. The key is the client's, never crapkit's: it is dropped
before the tool's table checks the call, and the CLI runs as it would without
it. Every other undeclared key is still refused.
"""
from pathlib import Path

import pytest

from crapkit import mcp_server
from crapkit.mcp_server import TOOLS, build_argv, tool_listing

# build_argv spells the repo into the argv; both sides of each comparison use this one.
REPO = "R"


def _measured(tmp_path: Path) -> Path:
    (tmp_path / "crapkit.toml").write_text("[crapkit]\ntarget = 6\n", encoding="utf-8")
    return tmp_path


def _arguments(tool: dict) -> dict:
    """The smallest call the tool's table accepts."""
    return {key: "a.py" if key == "path" else "f" for key in tool["positional"]}


def _ran(tmp_path: Path, tool: dict, arguments: dict) -> list:
    calls = []

    def run_cli(tool, arguments, repo):
        calls.append(build_argv(tool, arguments, REPO))
        return mcp_server._result("{}", is_error=False)

    result = mcp_server._call_tool(_measured(tmp_path), tool["name"], arguments, run_cli=run_cli)
    assert result["isError"] is False, result
    return calls


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t["name"])
@pytest.mark.parametrize("value", [True, False])
def test_every_tool_runs_a_call_that_carries_wait_for_previous(tmp_path, tool, value):
    plain = _arguments(tool)

    carried = _ran(tmp_path, tool, {**plain, "wait_for_previous": value})

    assert carried == [build_argv(tool, plain, REPO)]


def test_the_calls_gemini_sent_run_as_the_same_calls_without_the_key(tmp_path):
    """The three calls replayed from a Gemini CLI 0.61.0 session."""
    for name, arguments in (("get_next_item", {"wait_for_previous": True}),
                            ("list_worklist", {"top": 1, "wait_for_previous": False}),
                            ("check_config", {"wait_for_previous": True})):
        tool = mcp_server._tool_named(name)
        expected = {k: v for k, v in arguments.items() if k != "wait_for_previous"}

        assert _ran(tmp_path, tool, arguments) == [build_argv(tool, expected, REPO)]


def test_another_undeclared_key_is_still_refused_and_the_list_does_not_offer_the_client_key(
        tmp_path):
    result = mcp_server._call_tool(_measured(tmp_path), "list_worklist",
                                   {"wait_for_previous": True, "bogus": 1})

    assert result["isError"] is True
    assert result["content"][0]["text"] == (
        "list_worklist does not take 'bogus'; accepted: repo, top, scope")


def test_no_served_schema_declares_the_client_key():
    for entry in tool_listing():
        assert "wait_for_previous" not in entry["inputSchema"]["properties"]
