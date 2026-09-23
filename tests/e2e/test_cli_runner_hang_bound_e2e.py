"""A CLI child with no bound of its own waits the hang bound, and a miss shows
what the child printed. The MCP path, which feeds stdin frames and waits for each
reply, reports a miss the same way."""
import json

import pytest

import hang_guard
from conftest import run_cli

PING = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"


def test_a_cli_run_without_its_own_bound_waits_the_hang_bound(tmp_path, monkeypatch):
    """The spawn path's kill. In process a 0 s bound can lose the race to a
    fast --version; test_in_process_call_waits_the_hang_bound.py covers that path."""
    monkeypatch.setattr(hang_guard, "HANG_SECONDS", 0)

    with pytest.raises(AssertionError, match="so the child was killed"):
        run_cli(tmp_path, "--version", spawn=True)


def test_a_cli_run_inside_the_bound_returns_its_output(tmp_path):
    done = run_cli(tmp_path, "--version")

    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.startswith("crapkit ")


def test_an_mcp_reply_that_never_comes_kills_the_server_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(hang_guard, "HANG_SECONDS", 0)

    with pytest.raises(AssertionError) as missed:
        run_cli(tmp_path, "mcp", stdin=PING)

    assert str(missed.value).startswith(
        "never saw a reply from the MCP server within 0 s, so the child was killed\n"
        "--- the child printed ---\n")


def test_an_mcp_server_that_exits_before_replying_shows_its_stderr(tmp_path):
    with pytest.raises(AssertionError) as missed:
        run_cli(tmp_path, "mcp", "--no-such-flag", stdin=PING)

    report = str(missed.value)
    assert "never saw a reply from the MCP server before the child exited with code 2" in report
    assert "--no-such-flag" in report.split("--- the child printed ---")[1]


def test_an_mcp_server_that_outlives_its_input_is_killed_and_reported(tmp_path, monkeypatch):
    notice = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
    monkeypatch.setattr(hang_guard, "HANG_SECONDS", 0)

    with pytest.raises(AssertionError, match="never saw the MCP server exit within 0 s, so the"):
        run_cli(tmp_path, "mcp", stdin=notice)


def test_an_mcp_exchange_inside_the_bound_returns_the_reply(tmp_path):
    done = run_cli(tmp_path, "mcp", stdin=PING)

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"jsonrpc": "2.0", "id": 1, "result": {}}
