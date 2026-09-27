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


# --- every shape a client can send ------------------------------------------------

_KEYS = ["name", "arguments", "repo", "path", "top", "capabilities", "protocolVersion", "roots"]
_STRINGS = ["", "x", "list_runs", "a.py", "worklist", "${x}", "~", "2025-06-18"]


def _json_value(rng, depth: int = 0):
    """One JSON value of any type, nested up to three levels."""
    makers = {
        "n": lambda: None, "b": lambda: rng.random() < .5, "i": lambda: rng.randint(-3, 3),
        "s": lambda: rng.choice(_STRINGS),
        "l": lambda: [_json_value(rng, depth + 1) for _ in range(rng.randint(0, 2))],
        "o": lambda: {rng.choice(_KEYS): _json_value(rng, depth + 1)
                      for _ in range(rng.randint(0, 3))}}
    return makers[rng.choice("nbislo" if depth < 3 else "nbis")]()


def _message(rng, msg_id: int) -> dict:
    tool = rng.choice(["list_runs", "get_function_brief", "check_gate", "worklist", None])
    params = (_json_value(rng) if tool is None
              else {"name": tool, "arguments": _json_value(rng)})
    method = rng.choice(["tools/call", "tools/call", "initialize", "tools/list", "ping", "x"])
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}


def test_no_request_a_client_can_shape_answers_an_internal_error(tmp_path):
    """-32603 is for a fault inside the server, never for what a client sent:
    arguments, params and tool names of every JSON type, drawn from one seed."""
    import random
    rng, root = random.Random(7), _measured(tmp_path)

    def ran(*_, **__):
        return mcp_server._result("{}", is_error=False)

    faults = [message for message in (_message(rng, n) for n in range(3000))
              if mcp_server._reply(mcp_server._Session(root), message, run_cli=ran)
              .get("error", {}).get("code") == -32603]

    assert faults == []


def test_the_mcp_page_quotes_both_answers_as_the_server_gives_them(tmp_path):
    page = Path(__file__).resolve().parents[2] / "docs" / "agent-json.md"
    text = " ".join(page.read_text(encoding="utf-8").split())
    refused = _call(tmp_path, {"name": "list_runs", "arguments": ["x"]})
    invalid = _call(tmp_path, ["x"])

    assert refused["result"]["content"][0]["text"] in text
    assert invalid["error"]["message"] in text
