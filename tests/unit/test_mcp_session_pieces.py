"""The per-session pieces of the MCP server, driven in process.

test_mcp_workspace_roots.py drives them through a real stdio server; these
reach the branches a live client rarely takes: a cancellation while a call
waits for the roots answer, a client that names no folder, a stray response,
a notification that raises, and a server started inside its plugin directory.
"""
import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit import mcp_server
from crapkit.cli import analyses
from crapkit.procs import CommandCancelled

_ROOTS = {"capabilities": {"roots": {"listChanged": True}}}


def _session(tmp_path: Path, **kwargs) -> mcp_server._Session:
    start = tmp_path / "home"
    start.mkdir(exist_ok=True)
    session = mcp_server._Session(start, **kwargs)
    session.greet(_ROOTS)
    return session


def test_a_cancelled_call_stops_waiting_for_the_roots_answer(tmp_path):
    session = _session(tmp_path)
    session.ask()

    with pytest.raises(CommandCancelled):
        session.run(mcp_server._tool_named("list_runs"), {}, SimpleNamespace(cancelled=True))


def test_a_client_that_names_no_folder_is_told_so(tmp_path):
    session = _session(tmp_path)
    request = session.ask()
    session.take({"jsonrpc": "2.0", "id": request["id"], "result": {"roots": []}})

    text = session.missing()["content"][0]["text"]

    assert text.startswith(f"no crapkit.toml in {session.start}, and the client named no "
                           "workspace folder - nothing measured here.")


@pytest.mark.parametrize("answer", [{"id": "stray", "result": {"roots": []}}, {"result": {}}])
def test_a_response_to_no_pending_request_changes_nothing(tmp_path, answer):
    session = _session(tmp_path)
    session.ask()

    assert mcp_server._reply(session, {"jsonrpc": "2.0", **answer}) is None
    assert session.served() is session, "the server still waits for its own answer"


@pytest.mark.parametrize("roots", [None, "x", [7, {"uri": 7}, {"uri": "https://example.com/a"}]])
def test_an_answer_with_no_local_folder_names_none(tmp_path, roots):
    session = _session(tmp_path)
    request = session.ask()

    session.take({"id": request["id"], "result": {"roots": roots}})

    assert session.folders == [] and session.served() is None


def test_a_notification_that_raises_gets_no_reply(monkeypatch, tmp_path):
    session = _session(tmp_path)
    monkeypatch.setattr(session, "ask", lambda: 1 / 0)

    assert mcp_server._reply(session, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_a_method_that_is_not_a_string_is_unknown(tmp_path):
    reply = mcp_server._reply(_session(tmp_path), {"jsonrpc": "2.0", "id": 1, "method": 7})

    assert reply["error"]["code"] == -32601


def test_a_plugin_session_without_roots_says_to_pass_repo(tmp_path):
    session = mcp_server._Session(tmp_path, plugin=True)
    session.greet({"capabilities": {}})

    assert "pass a `repo` argument" in session.hint()
    assert session.ask() is None
    assert session.missing()["content"][0]["text"].startswith(
        f"this crapkit MCP server started in {tmp_path}, the plugin's install directory")


def test_a_server_started_in_its_plugin_directory_is_not_walked_up_from(monkeypatch, tmp_path):
    """A plugin loaded from a crapkit checkout sits below crapkit's own
    crapkit.toml, which the walk would have served."""
    (tmp_path / "crapkit.toml").write_text("not toml at all [", encoding="utf-8")
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    for name in mcp_server.PLUGIN_ROOT_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.chdir(plugin)
    served = []
    monkeypatch.setattr(mcp_server, "serve", lambda root, **kw: served.append((root, kw)) or 0)

    assert analyses.cmd_mcp(argparse.Namespace(repo=None)) == 0

    assert served == [(plugin.resolve(), {"plugin": True})]


def test_a_cwd_outside_every_plugin_directory_is_no_plugin_start(monkeypatch, tmp_path):
    for name in mcp_server.PLUGIN_ROOT_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PLUGIN_ROOT", str(tmp_path / "plugin"))

    assert mcp_server.started_in_plugin(tmp_path.resolve()) is False
    assert mcp_server.started_in_plugin((tmp_path / "plugin" / "skills").resolve()) is True
