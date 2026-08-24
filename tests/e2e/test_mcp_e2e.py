"""MCP stdio handshake against the real server process: initialize, list the
tools, call one. Newline-delimited JSON-RPC, exactly what an MCP client sends."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", r)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"]):
        subprocess.run(cmd, cwd=r, check=True, capture_output=True)
    return r


def _rpc(msg_id, method, params=None):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def test_initialize_list_and_call(repo: Path):
    requests = "\n".join([
        _rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        _rpc(2, "tools/list"),
        _rpc(3, "tools/call", {"name": "doctor", "arguments": {}}),
    ]) + "\n"
    proc = subprocess.run([sys.executable, "-m", "crapkit", "mcp", "--repo", str(repo)],
                          input=requests, capture_output=True, text=True, timeout=120,
                          cwd=repo, env=dict(os.environ))
    responses = {m["id"]: m for m in map(json.loads, proc.stdout.strip().splitlines())}

    assert responses[1]["result"]["serverInfo"]["name"] == "crapkit"
    tool_names = {t["name"] for t in responses[2]["result"]["tools"]}
    assert "next_item" in tool_names and "doctor" in tool_names
    call = responses[3]["result"]
    assert call["isError"] is False, call
    assert "no problems found" in call["content"][0]["text"]
