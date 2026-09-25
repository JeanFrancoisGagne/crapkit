"""A tools/call whose parts have the wrong JSON type answers as a refusal.

ADR 0001 keeps bad arguments as tool results the caller can read and correct.
A call whose `arguments` was a list, a string, a number or a boolean reached
code that read it as an object: `list_runs` with `["x"]` answered JSON-RPC
-32603 `AttributeError: 'list' object has no attribute 'items'`, and a tool
with a positional failed the same way on `.get`. A tool `name` that was a list
failed on the renamed-tool lookup with `TypeError: unhashable type`. `params`
that is not an object is malformed JSON-RPC, so it answers -32602, the code
the spec reserves for invalid params, never an internal error.
"""
from pathlib import Path

import pytest

from crapkit import mcp_server


def _measured(tmp_path: Path) -> Path:
    (tmp_path / "crapkit.toml").write_text("[crapkit]\ntarget = 6\n", encoding="utf-8")
    return tmp_path


def _call(tmp_path: Path, params, method: str = "tools/call") -> dict:
    session = mcp_server._Session(_measured(tmp_path))
    ran = []
    reply = mcp_server._reply(session, {"jsonrpc": "2.0", "id": 7, "method": method,
                                        "params": params},
                              run_cli=lambda *a, **k: ran.append(a) or {"ran": True})
    assert not ran, "a refused call must never reach the CLI"
    return reply


@pytest.mark.parametrize("name", ["list_runs", "get_function_brief"])
@pytest.mark.parametrize("arguments, shown", [
    (["x"], 'an array (["x"])'), ("x", 'a string ("x")'), (5, "a number (5)"),
    (True, "a boolean (true)")])
def test_arguments_that_are_not_an_object_answer_a_tool_result(tmp_path, name, arguments, shown):
    reply = _call(tmp_path, {"name": name, "arguments": arguments})

    assert "error" not in reply, reply
    assert reply["result"]["isError"] is True
    assert reply["result"]["content"][0]["text"] == (
        f"{name} takes its arguments as a JSON object of argument name to value, got {shown}; "
        "see inputSchema")


@pytest.mark.parametrize("name, shown", [(["list_runs"], "['list_runs']"), ({"a": 1}, "{'a': 1}"),
                                         (5, "5")])
def test_a_tool_name_that_is_not_a_string_answers_unknown_tool(tmp_path, name, shown):
    reply = _call(tmp_path, {"name": name, "arguments": {}})

    assert "error" not in reply, reply
    assert reply["result"]["isError"] is True
    assert reply["result"]["content"][0]["text"] == f"unknown tool {shown}"


@pytest.mark.parametrize("method", ["tools/call", "initialize", "tools/list"])
@pytest.mark.parametrize("params, shown", [([1], "an array"), ("x", "a string"), (3, "a number")])
def test_params_that_are_not_an_object_answer_invalid_params(tmp_path, method, params, shown):
    reply = _call(tmp_path, params, method)

    assert reply["error"]["code"] == -32602, reply
    assert reply["error"]["message"] == f"{method} takes params as a JSON object, got {shown}"


def test_absent_or_null_arguments_still_run_the_call(tmp_path):
    for params in ({"name": "list_runs"}, {"name": "list_runs", "arguments": None}):
        session = mcp_server._Session(_measured(tmp_path))
        reply = mcp_server._reply(session, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                            "params": params},
                                  run_cli=lambda *a, **k: mcp_server._result("{}", is_error=False))

        assert reply["result"]["isError"] is False, reply
