"""A server that outlives an upgrade of its own package says to restart it.

`pip install -U crapkit` under a running `crapkit mcp` replaces the package's
files while the process keeps the old code in memory. The server imports some
modules only at its first tools/call (the Windows Job, the process family, the
measurement owner it starts with runpy from the package directory), so that
call loaded the new files into the old process and answered an opaque
JSON-RPC -32603: `TypeError: _operation() takes 2 positional arguments but 3
were given`, `Job.__init__() missing 1 required positional argument`, or
`ToolError: measurement owner stopped before confirming ownership`. A session
whose first call came before the upgrade kept working, so the failure looked
random, and nothing named the restart that fixes it.

Every tools/call now reads the version the package directory holds and, when
it is not the version the server loaded, answers a tool result that names both
and the restart, before anything is imported or spawned.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import crapkit
from crapkit import _package, mcp_server
from hang_guard import exited, next_line

_CONFIG = '[crapkit]\ntarget = 6\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'


def _no_cli(tool, arguments, repo):
    raise AssertionError(f"the server spawned {tool['name']} after its package changed")


def _measured(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "crapkit.toml").write_text(_CONFIG, encoding="utf-8")
    return path


def _installed_init(tmp_path: Path, text: str) -> Path:
    init = tmp_path / "site" / "crapkit" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text(text, encoding="utf-8")
    return init


def test_a_changed_version_on_disk_answers_the_restart_and_spawns_nothing(monkeypatch, tmp_path):
    init = _installed_init(tmp_path, '"""crapkit."""\n__version__ = "99.0.0"\n')
    monkeypatch.setattr(_package, "_INIT", init)

    result = mcp_server._call_tool(_measured(tmp_path / "repo"), "list_runs", {}, run_cli=_no_cli)

    assert result["isError"] is True
    assert result["content"][0]["text"] == (
        f"crapkit was upgraded from {crapkit.__version__} to 99.0.0 while this MCP server ran, "
        f"and the server still runs {crapkit.__version__}'s code, which cannot load the new "
        "files. Restart the crapkit MCP server (reconnect it in your client, or start a new "
        "session), then call list_runs again.")


def test_every_tool_and_an_unknown_name_answer_the_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(_package, "_INIT",
                        _installed_init(tmp_path, '__version__ = "99.0.0"\n'))
    repo = _measured(tmp_path / "repo")

    for name in [tool["name"] for tool in mcp_server.TOOLS] + ["worklist", "no_such_tool"]:
        text = mcp_server._call_tool(repo, name, {}, run_cli=_no_cli)["content"][0]["text"]
        assert "Restart the crapkit MCP server" in text, (name, text)


def test_the_version_the_server_loaded_runs_the_call(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(_package, "_INIT",
                        _installed_init(tmp_path, f'__version__ = "{crapkit.__version__}"\n'))

    mcp_server._call_tool(_measured(tmp_path / "repo"), "list_runs", {},
                          run_cli=lambda tool, arguments, repo: ran.append(tool["name"]))

    assert ran == ["list_runs"]


def test_a_package_file_it_cannot_read_runs_the_call(monkeypatch, tmp_path):
    """A package imported from a zip, or a directory mid-install, has no
    readable __init__.py: that is no evidence of an upgrade, and refusing every
    call on it would break a server that works."""
    ran = []
    monkeypatch.setattr(_package, "_INIT", tmp_path / "gone" / "__init__.py")

    mcp_server._call_tool(_measured(tmp_path / "repo"), "list_runs", {},
                          run_cli=lambda tool, arguments, repo: ran.append(tool["name"]))

    assert ran == ["list_runs"]


def test_an_upgrade_during_a_call_still_names_the_command_for_the_whole_answer(monkeypatch,
                                                                             tmp_path):
    """The check before the spawn cannot see an upgrade that lands while the
    command runs. packet.py spells the `full` command a cut answer carries, and
    the server imported it after the run, so the next release's packet.py
    loaded into the old process and a long call answered JSON-RPC -32603."""
    payload = {"runs": [{"id": n, "note": "x" * 200} for n in range(60)], "schema": 1}

    def upgraded_while_running(argv, **_):
        monkeypatch.setitem(sys.modules, "crapkit.packet", None)
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload) + "\n", "")

    monkeypatch.setattr(mcp_server, "run_owned", upgraded_while_running)
    repo = _measured(tmp_path / "repo")

    result = mcp_server._call_tool(repo, "list_runs", {})

    full = json.loads(result["content"][0]["text"])["truncated"]["full"]
    assert full.endswith(f'runs "--repo={repo.resolve()}" --json'), full


def test_the_server_reads_its_own_package_directory():
    assert _package._INIT == Path(crapkit.__file__)
    assert _package.installed_version() == crapkit.__version__


# --- the real process ---------------------------------------------------------

def _copy_package(tmp_path: Path) -> Path:
    site = tmp_path / "site"
    shutil.copytree(Path(crapkit.__file__).parent, site / "crapkit",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return site


def _upgrade(site: Path) -> None:
    """What `pip install -U` leaves on disk: a new version, and every module the
    old process has not imported yet written for another API."""
    package = site / "crapkit"
    for module in package.rglob("*.py"):
        if module.name != "__init__.py" or module.parent != package:
            module.write_text("raise ImportError('written by the next release')\n", encoding="utf-8")
    (package / "__init__.py").write_text('__version__ = "99.0.0"\n', encoding="utf-8")


def _ask(server, message: dict) -> dict:
    server.stdin.write(json.dumps(message) + "\n")
    server.stdin.flush()
    return json.loads(next_line(server))


def test_a_live_server_answers_its_first_call_after_an_upgrade_with_the_restart(tmp_path):
    site = _copy_package(tmp_path)
    repo = _measured(tmp_path / "repo")
    env = {**os.environ, "PYTHONPATH": str(site), "PYTHONDONTWRITEBYTECODE": "1"}
    server = subprocess.Popen([sys.executable, "-m", "crapkit", "mcp", "--repo", str(repo)],
                              cwd=repo, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    try:
        init = _ask(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"}}})
        assert init["result"]["serverInfo"]["version"] == crapkit.__version__
        _ask(server, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        _upgrade(site)

        reply = _ask(server, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "list_claims", "arguments": {}}})
    finally:
        server.stdin.close()
        exited(server)
        server.stdout.close()

    assert "error" not in reply, reply
    assert reply["result"]["isError"] is True
    text = reply["result"]["content"][0]["text"]
    assert f"upgraded from {crapkit.__version__} to 99.0.0" in text
    assert "Restart the crapkit MCP server" in text
