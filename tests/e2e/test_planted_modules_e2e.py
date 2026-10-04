"""A repo holding a planted crapkit.py never runs it through crapkit's MCP server.

The server ran each tool as `python -m crapkit` from the root of the repo it
serves, and `-m` puts the working directory first on sys.path. A `crapkit.py`
there ran in place of crapkit on every call, and the call answered empty with
isError false. The child now starts with `-P`.

The server itself starts from another directory here, the way an MCP client
starts `crapkit mcp --repo <path>`, so only the tool's child stands in the
repo and the planted file can only run there.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from conftest import cli_runner, git_commit_all, git_init_repo

# The MCP server is a stdio process, and this file tests it as one.
run_cli = cli_runner(spawn=True)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
MARKER = "planted-ran.txt"
# A crapkit.py that records its run in MARKER beside it and does nothing else.
PLANTED = ("import os\n"
           "here = os.path.dirname(os.path.abspath(__file__))\n"
           f"with open(os.path.join(here, {MARKER!r}), 'a') as ran:\n"
           "    ran.write('crapkit.py\\n')\n")


def _rpc(msg_id, method, params=None):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def test_an_mcp_call_in_a_repo_holding_a_planted_crapkit_py_answers_from_crapkit(tmp_path: Path):
    repo = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    git_init_repo(repo)
    git_commit_all(repo, "init")
    (repo / "crapkit.py").write_text(PLANTED, encoding="utf-8")
    elsewhere = tmp_path / "client"
    elsewhere.mkdir()
    frames = [_rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}),
              json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
              _rpc(2, "tools/call", {"name": "check_config", "arguments": {}})]

    proc = run_cli(elsewhere, "mcp", "--repo", str(repo), stdin="\n".join(frames) + "\n")

    assert not (repo / MARKER).exists(), "the repo's crapkit.py ran in place of crapkit"
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    replies = {m["id"]: m for m in map(json.loads, proc.stdout.strip().splitlines())}
    call = replies[2]["result"]
    assert call["isError"] is False, call
    report = json.loads(call["content"][0]["text"])
    assert (report["schema"], report["problems"]) == (1, []), report
