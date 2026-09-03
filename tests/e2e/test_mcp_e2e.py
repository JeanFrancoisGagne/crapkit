"""MCP stdio handshake against the real server process: initialize, list the
tools, call one. Newline-delimited JSON-RPC, exactly what an MCP client sends."""
import json
import shutil
from pathlib import Path

import pytest

from conftest import git_commit_all, git_init_repo, run_cli

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", r)
    git_init_repo(r)
    git_commit_all(r, "init")
    return r


@pytest.fixture()
def inventoried_repo(repo: Path) -> Path:
    """One inventory run, so explain and worklist have a store to answer from."""
    res = run_cli(repo, "inventory")
    assert res.returncode == 0, res.stdout + res.stderr
    return repo


def _rpc(msg_id, method, params=None):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _call(msg_id, name, arguments=None):
    return _rpc(msg_id, "tools/call", {"name": name, "arguments": arguments or {}})


def _serve(repo: Path, requests: list[str]) -> dict:
    proc = run_cli(repo, "mcp", "--repo", str(repo), stdin="\n".join(requests) + "\n")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    return {m["id"]: m for m in map(json.loads, proc.stdout.strip().splitlines())}


def test_initialize_list_and_call(repo: Path):
    responses = _serve(repo, [
        _rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        _rpc(2, "tools/list"),
        _call(3, "doctor"),
    ])

    assert responses[1]["result"]["serverInfo"]["name"] == "crapkit"
    tool_names = {t["name"] for t in responses[2]["result"]["tools"]}
    assert "next_item" in tool_names and "doctor" in tool_names
    call = responses[3]["result"]
    assert call["isError"] is False, call
    assert call["structuredContent"]["problems"] == [], \
        "doctor answers the JSON report: a passing doctor is an empty problems list"
    assert json.loads(call["content"][0]["text"])["schema"] == 1


def test_a_bad_call_is_a_tool_error_and_the_server_keeps_answering(repo: Path):
    """Four mistakes a coding agent makes, then a good call. Each mistake is a
    tool result in the tool's words (ADR 0001); the good call still answers."""
    replies = _serve(repo, [
        _rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}),
        _call(2, "brief", {"path": "pylib/mod.py"}),
        _call(3, "worklist", {"bogus": 1}),
        _call(4, "worklist", {"top": "three"}),
        _rpc(5, "ping"),
        _call(6, "doctor"),
    ])

    assert set(replies) == {1, 2, 3, 4, 5, 6}, "no mistake ends the session"
    texts = {i: replies[i]["result"]["content"][0]["text"] for i in (2, 3, 4)}
    assert all(replies[i]["result"]["isError"] is True for i in (2, 3, 4)), texts
    assert texts[2] == "brief needs name (see inputSchema.required)"
    assert texts[3] == "worklist does not take 'bogus'; accepted: repo, top, scope"
    assert texts[4] == 'top must be an integer (got "three")'
    assert "usage:" not in texts[4], "the refusal speaks the tool's vocabulary, not argparse's"
    assert replies[5]["result"] == {}
    assert replies[6]["result"]["isError"] is False


def test_tools_list_names_the_required_arguments(repo: Path):
    replies = _serve(repo, [_rpc(1, "tools/list")])

    schemas = {t["name"]: t["inputSchema"] for t in replies[1]["result"]["tools"]}
    assert schemas["brief"]["required"] == ["path", "name"]
    assert schemas["explain"]["required"] == ["path", "name"]
    assert "required" not in schemas["worklist"]


def test_explain_answers_structured_json_and_history_reaches_the_cli(inventoried_repo: Path):
    replies = _serve(inventoried_repo, [
        _call(1, "explain", {"path": "pylib/mod.py", "name": "guarded"}),
        _call(2, "explain", {"path": "pylib/mod.py", "name": "guarded", "history": True}),
    ])

    plain = replies[1]["result"]
    assert plain["isError"] is False, plain
    assert plain["structuredContent"]["name"] == "guarded"
    assert "commits" not in plain["structuredContent"]["functions"][0]
    with_history = replies[2]["result"]["structuredContent"]["functions"][0]
    assert [c["subject"] for c in with_history["commits"]] == ["init"], \
        "history: true reaches the CLI as --history"


def test_worklist_and_next_item_take_a_scope_array(inventoried_repo: Path):
    replies = _serve(inventoried_repo, [
        _call(1, "worklist", {"scope": ["py"]}),
        _call(2, "worklist", {"scope": ["src"]}),
    ])

    py = replies[1]["result"]
    assert py["isError"] is False, py
    assert py["structuredContent"]["active"], "the py scope holds the fixture's one admitted row"
    assert {r["scope"] for r in py["structuredContent"]["active"]} == {"py"}
    src = replies[2]["result"]["structuredContent"]
    assert all(r["scope"] == "src" for r in src["active"]), src["active"]
