"""A globally registered MCP server starts wherever the client opened, and that
is usually not a crapkit repo.

`crapkit mcp` used to load the config before serving, so in a directory with no
crapkit.toml the process died on the first line of stdio: the client saw a
server that never answered `initialize`, with the reason on a stderr nobody
reads. The tools are read-only and every one of them names its own repo, so a
missing config is a per-call answer, not a dead server.

Real subprocesses, newline-delimited JSON-RPC, exactly what a client sends.
"""
import json
import shutil
from pathlib import Path

import pytest

from conftest import git_commit_all, git_init_repo, run_cli

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _rpc(msg_id, method, params=None) -> str:
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _call(msg_id, name, arguments=None) -> str:
    return _rpc(msg_id, "tools/call", {"name": name, "arguments": arguments or {}})


def _serve(cwd: Path, requests: list[str]) -> dict:
    """One server process fed every request at once; replies keyed by id.

    No --repo: the server has to resolve its root from the directory it was
    started in, which is the only thing a global client registration gives it.
    """
    proc = run_cli(cwd, "mcp", stdin="\n".join(requests) + "\n")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    return {m["id"]: m for m in map(json.loads, proc.stdout.strip().splitlines())}


@pytest.fixture()
def configured_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    git_init_repo(repo)
    git_commit_all(repo, "init")
    return repo


def test_a_bare_directory_gets_guidance_and_keeps_the_server_alive(tmp_path: Path):
    """Two calls, on purpose: the second one only answers if the first was an
    answer rather than an exit."""
    replies = _serve(tmp_path, [
        _rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        _rpc(2, "tools/list"),
        _call(3, "doctor"),
        _call(4, "worklist"),
    ])

    assert set(replies) == {1, 2, 3, 4}, "a call in a bare directory must still get a reply"
    assert replies[1]["result"]["serverInfo"]["name"] == "crapkit"
    assert len(replies[2]["result"]["tools"]) >= 9, "the listing does not depend on a repo"
    for msg_id in (3, 4):
        reply = replies[msg_id]
        assert "error" not in reply, ("a missing config is a tool result, not a protocol error",
                                      reply)
        text = reply["result"]["content"][0]["text"]
        assert "crapkit.toml" in text and "crapkit init" in text, text
        assert tmp_path.name in text, ("the guidance names the directory it resolved from cwd",
                                       text)


def test_a_configured_repo_answers_normally_with_no_repo_flag(configured_repo: Path):
    """The same tool, one directory over: cwd resolution is what makes a single
    global registration work in every checkout."""
    replies = _serve(configured_repo, [
        _rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}),
        _call(2, "doctor"),
    ])

    call = replies[2]["result"]
    assert call["isError"] is False, call
    text = call["content"][0]["text"]
    assert call["structuredContent"]["problems"] == [], \
        "doctor answers its JSON report; a configured repo with no FAIL is an empty list"
    assert "crapkit init" not in text, "a configured repo must never get the no-config guidance"
