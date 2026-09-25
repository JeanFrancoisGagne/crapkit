"""An MCP server started without HOME or USERPROFILE answers check_config.

A client may build the server's environment from an allowlist (an SDK's env
option replaces the environment), and a Windows service or scheduled task can
start without USERPROFILE. Path.home() raises on Windows when USERPROFILE,
HOMEDRIVE and HOMEPATH are all unset, and check_config answered that traceback
while list_runs, check_gate and get_function_brief answered normally.

Real subprocess, newline-delimited JSON-RPC, exactly what a client sends. The
`without_home_variables` fixture strips the variables from this process, and
the server inherits that environment.
"""
import json
import os
import shutil
from pathlib import Path

from conftest import cli_runner, git_commit_all, git_init_repo

# The MCP server is a stdio process, and this file tests it as one.
run_cli = cli_runner(spawn=True)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _call(msg_id, name) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                       "params": {"name": name, "arguments": {}}})


def test_check_config_answers_the_doctor_report_without_home_variables(tmp_path,
                                                                       without_home_variables):
    repo = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    git_init_repo(repo)
    git_commit_all(repo, "init")
    initialize = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2024-11-05", "capabilities": {}}})

    proc = run_cli(repo, "mcp", stdin="\n".join([initialize, _call(2, "check_config")]) + "\n")

    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    call = [json.loads(line) for line in proc.stdout.splitlines()][-1]["result"]
    text = call["content"][0]["text"]
    assert "Traceback" not in text, text
    budget = Path(json.loads(text)["resources"]["budget_directory"])
    expected = without_home_variables / ".cache" / "crapkit" / "workers"
    assert os.path.normcase(budget.parent) == os.path.normcase(expected), budget
