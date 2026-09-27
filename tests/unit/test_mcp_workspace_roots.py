"""A server started outside the workspace serves the folders the client names.

`crapkit mcp` without `--repo` serves the crapkit.toml at or above the
directory the client started it in (ADR 0002). Clients do not all start it in
the workspace: VS Code starts a user-level server in the home directory and a
plugin's server in the plugin directory, and GitHub Copilot CLI starts a
plugin's server in `~/.copilot/installed-plugins/<plugin>`. Every call there
answered `no crapkit.toml in <home>` inside a measured repo, or, with the
plugin loaded from a crapkit checkout, served crapkit's own repo above the
plugin directory.

A client that declares the `roots` capability names its workspace folders on
request. The server asks once the client says it is initialized, and again
when the client says the folders changed, whenever its start directory serves
nothing, and serves the first folder a crapkit.toml claims. A start directory
inside the plugin directory the client names in PLUGIN_ROOT,
COPILOT_PLUGIN_ROOT or CLAUDE_PLUGIN_ROOT is never walked up from, and a client
that names no folders gets an answer that says to pass `repo`.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from crapkit import mcp_server
from hang_guard import exited, next_line

_CONFIG = '[crapkit]\ntarget = 6\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
_PLUGIN_VARS = ("PLUGIN_ROOT", "COPILOT_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT")
# What GitHub Copilot CLI gives its MCP servers that names its session.
_SESSION_VARS = ("COPILOT_AGENT_SESSION_ID", "COPILOT_HOME")
_ROOTS = {"roots": {"listChanged": True}}


def _measured(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "crapkit.toml").write_text(_CONFIG, encoding="utf-8")
    return path


def _plain(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


class Client:
    """A client on the real stdio server, one frame at a time."""

    def __init__(self, cwd: Path, plugin: dict | None = None, capabilities: dict | None = None,
                 args: tuple = ()):
        env = {key: value for key, value in os.environ.items()
               if key not in _PLUGIN_VARS + _SESSION_VARS}
        env.update(plugin or {}, PYTHONDONTWRITEBYTECODE="1")
        self.process = subprocess.Popen([sys.executable, "-m", "crapkit", "mcp", *args], cwd=cwd,
                                        env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.init = self.ask({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": capabilities or {},
            "clientInfo": {"name": "t", "version": "1"}}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, message: dict) -> None:
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def receive(self) -> dict:
        return json.loads(next_line(self.process))

    def ask(self, message: dict) -> dict:
        self.send(message)
        return self.receive()

    def answer_roots(self, *folders: Path) -> dict:
        """Read the server's roots/list request and answer it with `folders`."""
        request = self.receive()
        assert request["method"] == "roots/list", request
        self.send({"jsonrpc": "2.0", "id": request["id"], "result": {"roots": [
            {"uri": folder.resolve().as_uri(), "name": folder.name} for folder in folders]}})
        return request

    def call(self, msg_id: int, name: str = "list_runs") -> dict:
        return self.ask(_call(msg_id, name))

    def close(self) -> None:
        self.process.stdin.close()
        exited(self.process)
        self.process.stdout.close()


def _call(msg_id: int, name: str = "list_runs") -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
            "params": {"name": name, "arguments": {}}}


def _assert_served_at(reply: dict, root: Path) -> None:
    """The list_runs call ran at `root`. The repos here are measured but never
    scored, so the CLI itself answers, and its refusal names the root it read."""
    text = reply["result"]["content"][0]["text"]
    assert "no crapkit.toml" not in text, text
    message = json.loads(text)["error"]["message"]
    assert message.startswith(f"no snapshot in {root.resolve()} "), message


@pytest.fixture
def client():
    started = []

    def start(*args, **kwargs):
        started.append(Client(*args, **kwargs))
        return started[-1]

    yield start
    for each in started:
        each.close()


def test_a_server_started_in_home_serves_the_workspace_folder_the_client_names(tmp_path, client):
    workspace = _measured(tmp_path / "work" / "app")
    session = client(_plain(tmp_path / "home"), capabilities=_ROOTS)

    session.answer_roots(workspace)

    _assert_served_at(session.call(2), workspace)


def test_a_plugin_server_inside_a_checkout_serves_the_workspace_not_the_checkout(tmp_path, client):
    checkout = _measured(tmp_path / "crapkit-checkout")
    plugin = _plain(checkout / "plugin")
    workspace = _measured(tmp_path / "work" / "app")
    (checkout / ".crapkit").mkdir()
    session = client(plugin, plugin={"CLAUDE_PLUGIN_ROOT": str(plugin), "PLUGIN_ROOT": str(plugin)},
                     capabilities=_ROOTS)

    session.answer_roots(workspace)

    _assert_served_at(session.call(2), workspace)
    assert not (checkout / ".crapkit" / "crap.sqlite").exists()


def test_the_first_folder_a_config_claims_is_served(tmp_path, client):
    plain, workspace = _plain(tmp_path / "docs"), _measured(tmp_path / "app")
    session = client(_plain(tmp_path / "home"), capabilities=_ROOTS)

    session.answer_roots(plain, _plain(workspace / "src"))

    _assert_served_at(session.call(2), workspace)


def test_a_call_sent_before_the_roots_answer_waits_for_it(tmp_path, client):
    workspace = _measured(tmp_path / "app")
    session = client(_plain(tmp_path / "home"), capabilities=_ROOTS)
    request = session.receive()
    session.send(_call(2))

    session.send({"jsonrpc": "2.0", "id": request["id"], "result": {"roots": [
        {"uri": workspace.resolve().as_uri()}]}})

    _assert_served_at(session.receive(), workspace)


def test_changed_folders_are_asked_for_again(tmp_path, client):
    first, second = _plain(tmp_path / "first"), _measured(tmp_path / "second")
    session = client(_plain(tmp_path / "home"), capabilities=_ROOTS)
    session.answer_roots(first)
    assert session.call(2)["result"]["isError"] is True

    session.send({"jsonrpc": "2.0", "method": "notifications/roots/list_changed"})
    session.answer_roots(second)

    _assert_served_at(session.call(3), second)


def test_no_folder_claimed_names_the_start_and_the_folders(tmp_path, client):
    home, folder = _plain(tmp_path / "home"), _plain(tmp_path / "docs")
    session = client(home, capabilities=_ROOTS)
    session.answer_roots(folder)

    result = session.call(2)["result"]

    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert text.startswith(f"no crapkit.toml in {home.resolve()} or in the workspace folders "
                           f"the client named ({folder.resolve()}) - nothing measured here.")


def test_a_measured_start_directory_asks_for_nothing(tmp_path, client):
    app = _measured(tmp_path / "app")
    session = client(app, capabilities=_ROOTS)

    assert session.ask({"jsonrpc": "2.0", "id": 2, "method": "ping"}) == {
        "jsonrpc": "2.0", "id": 2, "result": {}}
    _assert_served_at(session.call(3), app)


def test_a_client_without_roots_is_never_asked(tmp_path, client):
    session = client(_plain(tmp_path / "home"))

    assert session.ask({"jsonrpc": "2.0", "id": 2, "method": "ping"})["id"] == 2
    assert "no crapkit.toml in" in session.call(3)["result"]["content"][0]["text"]


def test_a_copilot_plugin_server_says_it_started_in_the_plugin_directory(tmp_path, client):
    """Copilot CLI starts a plugin's server in its install directory and sends
    no roots, so nothing on the wire names the workspace. The server does not
    walk up from there, and it tells the model to pass `repo`, in the
    instructions and in each answer."""
    installed = _measured(tmp_path / "home" / ".copilot") / "installed-plugins" / "crapkit"
    plugin = _plain(installed / "crapkit")
    session = client(plugin, plugin={"COPILOT_PLUGIN_ROOT": str(plugin), "PLUGIN_ROOT": str(plugin)})

    result = session.call(2)["result"]

    assert result["isError"] is True
    assert result["content"][0]["text"] == (
        f"this crapkit MCP server started in {plugin.resolve()}, the plugin's install directory, "
        "not in your workspace, and the client names no workspace folders. Pass this tool a "
        "`repo` argument with the absolute path of the repo you want scored.")
    assert "pass a `repo` argument" in session.init["result"]["instructions"]


def _copilot_session(tmp_path: Path, cwd_line: str, session: str = "4b1d") -> dict:
    """The environment Copilot CLI gives a plugin's server, and the record its
    session keeps under COPILOT_HOME, which names the folder it works in."""
    home = tmp_path / "home" / ".copilot"
    record = home / "session-state" / session / "workspace.yaml"
    record.parent.mkdir(parents=True)
    record.write_text("\n".join((f"id: {session}", cwd_line, "branch: main",
                                 "client_name: github/cli", "")), encoding="utf-8")
    plugin = _plain(home / "installed-plugins" / "crapkit" / "crapkit")
    return {"COPILOT_HOME": str(home), "COPILOT_AGENT_SESSION_ID": session,
            "COPILOT_PLUGIN_ROOT": str(plugin), "PLUGIN_ROOT": str(plugin),
            "CLAUDE_PLUGIN_ROOT": str(plugin)}


def test_a_copilot_plugin_server_serves_the_folder_its_session_works_in(tmp_path, client):
    """Copilot CLI starts a plugin's server in the plugin's install directory,
    puts any `cwd` the plugin asks for back inside it, and declares no roots.
    It gives the server COPILOT_AGENT_SESSION_ID, and the session's
    workspace.yaml names the folder it works in, which is walked up from."""
    workspace = _measured(tmp_path / "work" / "app")
    env = _copilot_session(tmp_path, f"cwd: {_plain(workspace / 'src')}")
    session = client(Path(env["PLUGIN_ROOT"]), plugin=env)

    _assert_served_at(session.call(2), workspace)
    assert "pass a `repo` argument" not in session.init["result"]["instructions"]


def test_a_copilot_session_folder_without_a_config_is_named(tmp_path, client):
    folder = _plain(tmp_path / "docs")
    env = _copilot_session(tmp_path, f"cwd: {folder}")
    session = client(Path(env["PLUGIN_ROOT"]), plugin=env)

    text = session.call(2)["result"]["content"][0]["text"]

    assert text.startswith(f"no crapkit.toml in the folder the GitHub Copilot CLI session works "
                           f"in ({folder.resolve()}) - nothing measured here."), text


def test_a_copilot_session_without_a_record_asks_for_repo(tmp_path, client):
    """Copilot gives the server a session id whose record names no folder when
    the config directory was set with --config-dir instead of COPILOT_HOME, so
    the instructions ask for `repo` as each answer does."""
    env = {**_copilot_session(tmp_path, "cwd: x"), "COPILOT_AGENT_SESSION_ID": "gone"}
    session = client(Path(env["PLUGIN_ROOT"]), plugin=env)

    text = session.call(2)["result"]["content"][0]["text"]

    assert text.startswith(f"this crapkit MCP server started in {Path(env['PLUGIN_ROOT']).resolve()}, "
                           "the plugin's install directory"), text
    assert "pass a `repo` argument" in session.init["result"]["instructions"]


def test_a_repo_the_server_was_started_with_is_never_replaced(tmp_path, client):
    """`--repo` names an exact root: a directory without a crapkit.toml is
    refused there, never swapped for a folder the client or its session names."""
    given, workspace = _plain(tmp_path / "given"), _measured(tmp_path / "app")
    env = _copilot_session(tmp_path, f"cwd: {workspace}")
    env = {key: value for key, value in env.items() if key not in _PLUGIN_VARS}
    session = client(_plain(tmp_path / "home2"), plugin=env, capabilities=_ROOTS,
                     args=("--repo", str(given)))

    assert session.ask({"jsonrpc": "2.0", "id": 2, "method": "ping"}) == {
        "jsonrpc": "2.0", "id": 2, "result": {}}, "no roots/list request may come first"
    text = session.call(3)["result"]["content"][0]["text"]

    assert text.startswith(f"no crapkit.toml in {given.resolve()} - nothing measured here."), text


def test_a_repo_argument_is_served_wherever_the_server_started(tmp_path, client):
    workspace = _measured(tmp_path / "app")
    plugin = _plain(tmp_path / "plugin")
    session = client(plugin, plugin={"COPILOT_PLUGIN_ROOT": str(plugin)})

    reply = session.ask({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "list_runs", "arguments": {"repo": str(workspace)}}})

    _assert_served_at(reply, workspace)


def test_a_response_the_server_never_asked_for_gets_no_reply(tmp_path, client):
    session = client(_measured(tmp_path / "app"))
    session.send({"jsonrpc": "2.0", "id": "stray", "result": {}})

    assert session.ask({"jsonrpc": "2.0", "id": 2, "method": "ping"})["id"] == 2


# --- the pieces ------------------------------------------------------------------

@pytest.mark.parametrize("uri, expected", [
    ("file:///c%3A/Users/me/app", "c:/Users/me/app"),
    ("file:///C:/Users/me/my%20app", "C:/Users/me/my app"),
])
def test_a_windows_folder_uri_is_a_path(uri, expected):
    if os.name != "nt":
        pytest.skip("a drive letter names a path on Windows only")
    assert mcp_server._folder_path(uri) == Path(expected)


def test_a_posix_folder_uri_is_a_path():
    if os.name == "nt":
        pytest.skip("a rooted path without a drive is POSIX")
    assert mcp_server._folder_path("file:///home/me/my%20app") == Path("/home/me/my app")


@pytest.mark.parametrize("uri", ["vscode-remote://ssh-remote+box/home/me", "", None, 7])
def test_a_folder_that_is_not_a_local_file_is_skipped(uri):
    assert mcp_server._folder_path(uri) is None


@pytest.mark.parametrize("line, expected", [
    (r"cwd: C:\work\app", r"C:\work\app"),
    ("cwd: /home/me/app  ", "/home/me/app"),
    (r"cwd: 'C:\it''s: here'", r"C:\it's: here"),
    (r'cwd: "/home/me/back\\slash"', r"/home/me/back\slash"),
])
def test_the_session_record_cwd_is_read_as_copilot_writes_it(tmp_path, line, expected):
    record = tmp_path / "workspace.yaml"
    record.write_text("\n".join(("id: x", line, "git_root: /elsewhere", "")), encoding="utf-8")

    assert mcp_server._session_cwd(record) == Path(expected)


def test_a_crlf_session_record_names_the_cwd_without_its_cr(tmp_path):
    """The record is read as bytes, so a CRLF line reaches the pattern with its CR."""
    record = tmp_path / "workspace.yaml"
    record.write_bytes(b"id: x\r\ncwd: /home/me/app\r\ngit_root: /elsewhere\r\n")

    assert mcp_server._session_cwd(record) == Path("/home/me/app")


@pytest.mark.parametrize("text", ["id: x\n", "cwd: \n", 'cwd: "unterminated\n', "not yaml at all"])
def test_a_session_record_without_a_cwd_names_nothing(tmp_path, text):
    record = tmp_path / "workspace.yaml"
    record.write_text(text, encoding="utf-8")

    assert mcp_server._session_cwd(record) is None


def test_a_missing_or_unreadable_session_record_names_nothing(tmp_path):
    assert mcp_server._session_cwd(tmp_path / "absent.yaml") is None
    (tmp_path / "bad.yaml").write_bytes(b"cwd: \xff\xfe\n")
    assert mcp_server._session_cwd(tmp_path / "bad.yaml") is None


def test_copilot_home_defaults_to_the_home_directory(monkeypatch, tmp_path):
    record = tmp_path / ".copilot" / "session-state" / "s1" / "workspace.yaml"
    record.parent.mkdir(parents=True)
    record.write_text(f"cwd: {tmp_path / 'app'}\n", encoding="utf-8")
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "s1")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert mcp_server.copilot_workspace() == tmp_path / "app"


def test_a_copilot_session_started_without_home_variables_still_reads_its_home(
        monkeypatch, without_home_variables):
    """A client that builds the server's environment from an allowlist leaves
    out USERPROFILE, and Path.home() then raises on Windows. The session record
    is looked for under the home Windows reports instead."""
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "crapkit-test-no-such-session")

    assert mcp_server.copilot_workspace() is None


def test_no_copilot_session_names_no_folder(monkeypatch):
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)

    assert mcp_server.copilot_workspace() is None


def test_a_client_that_never_answers_gets_the_missing_config_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "ROOTS_SECONDS", 0)
    workspace = mcp_server._Session(_plain(tmp_path / "home"))
    workspace.greet({"capabilities": _ROOTS})
    assert workspace.ask()["method"] == "roots/list"

    result = mcp_server._call_tool(workspace, "list_runs", {})

    assert result["isError"] is True
    assert "the client did not name its workspace folders" in result["content"][0]["text"]


def test_the_mcp_page_states_the_wait_the_server_allows():
    page = (Path(__file__).resolve().parents[2] / "docs" / "agent-json.md").read_text(encoding="utf-8")

    assert f"up to {mcp_server.ROOTS_SECONDS} seconds" in " ".join(page.split())
