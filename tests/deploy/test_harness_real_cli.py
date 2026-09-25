"""Real-CLI connect cells: the pinned harness binary starts crapkit the way a
user's config tells it to, and the cell reads what the harness reports.

Each cell measures a repo with the candidate, puts the candidate behind the
shim (kit/shim.py) first on PATH, writes the harness's config from its
profile's [doc] (docs/harnesses.md once that page carries the section), then
runs the harness's own headless check: `claude mcp list`, a Codex app-server
thread, `cursor-agent mcp list-tools`, `gemini mcp list`, `amp mcp doctor`,
oh-my-pi's `/mcp test`, or one headless turn against the scripted model stub
for the harnesses that list tools only to a model. A cell asserts the harness
calls the server connected and sees all twelve tools where it lists them, and
reads the shim's record of how the harness started crapkit.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from kit import docsnip, gitmirror, profiles, repos, shim
from kit.cells import cell
from kit.mcp_client import McpClient

PACKET = "deploy-harnesses"
TOOLS = 12
# The release lin-up-cursor-0.7.6 upgrades from.
OLD = "0.7.6"
WINDOWS = os.name == "nt"


def repo_box(box, templates) -> Path:
    return profiles.real_cli_box(box, templates)


last_start = profiles.last_start
initialize_params = profiles.initialize_params


# --- Claude Code: the three `claude mcp add` scopes --------------------------------------

def claude_list(box, repo: Path) -> str:
    return box.run(["claude", "mcp", "list"], cwd=repo, expect=0).stdout


def approve_project_servers(box, repo: Path) -> None:
    """What answering yes to Claude Code's .mcp.json prompt writes: the server
    name under this project in $CLAUDE_CONFIG_DIR/.claude.json."""
    state = Path(box.env["CLAUDE_CONFIG_DIR"]) / ".claude.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    project = data.setdefault("projects", {}).setdefault(str(repo), {})
    project.update(enabledMcpjsonServers=["crapkit"], hasTrustDialogAccepted=True)
    state.write_text(json.dumps(data, indent=2), encoding="utf-8")


def add_scope(box, repo: Path, scope: str, argv: list[str]) -> None:
    box.run(["claude", "mcp", "add", "-s", scope, "crapkit", "--", *argv], cwd=repo, expect=0)


def remove_scope(box, repo: Path, scope: str) -> None:
    box.run(["claude", "mcp", "remove", "-s", scope, "crapkit"], cwd=repo, expect=0)


@cell("lin-claude-mcp-scopes", channel="claude mcp add local/project/user", harness="Claude Code 2.1.281",
      scenario="fresh: three scopes Connected; user entry hides plugin server; shim cwd and CLAUDE_PROJECT_DIR",
      use_cases="MCP client wiring, check_config", os="linux", image="core", cadence="push")
def test_claude_mcp_scopes(box, templates, candidate):
    repo = repo_box(box, templates)
    server = profiles.parsed_doc("claude-code", repo)
    for scope in ("local", "project", "user"):
        add_scope(box, repo, scope, server.argv)
        if scope == "project":
            assert "Pending approval" in claude_list(box, repo)
            approve_project_servers(box, repo)
        assert re.search(r"^crapkit: .* Connected$", claude_list(box, repo), re.M)
        start = last_start(box)
        assert start["cwd"] == str(repo) and start["env"]["CLAUDE_PROJECT_DIR"] == str(repo)
        remove_scope(box, repo, scope)

    install_claude_plugin(box, repo, candidate)
    assert "plugin:crapkit:crapkit: " in claude_list(box, repo)
    assert_user_entry_hides_plugin(box, repo, server.argv)


def assert_user_entry_hides_plugin(box, repo: Path, doc_argv: list[str]) -> None:
    """Claude Code drops the plugin's server when a user entry starts the same
    command; an entry with other args (the doc's --repo) runs beside it."""
    add_scope(box, repo, "user", ["crapkit", "mcp"])
    listed = claude_list(box, repo)
    assert re.search(r"^crapkit: crapkit mcp - .* Connected$", listed, re.M) and "plugin:crapkit:crapkit" not in listed
    remove_scope(box, repo, "user")
    add_scope(box, repo, "user", doc_argv)
    listed = claude_list(box, repo)
    assert "plugin:crapkit:crapkit: crapkit mcp - " in listed and re.search(r"^crapkit: .*--repo.* Connected$", listed, re.M)


def install_claude_plugin(box, repo: Path, candidate) -> None:
    """The README's two plugin lines, run verbatim against the local mirror."""
    gitmirror.make(box).publish(candidate.staged, candidate.version)
    for line in docsnip.commands(docsnip.fence("README.md", "The Claude Code plugin")):
        box.script(line, cwd=repo, expect=0)


# --- Codex: `codex mcp add` and an app-server thread -----------------------------------------

class AppServer(McpClient):
    """`codex app-server`: JSON-RPC over stdio, the same framing an MCP client uses."""

    def start_thread(self, cwd: Path) -> dict:
        self.request("initialize", {"clientInfo": {"name": "deploy-cell", "version": "1"}})
        self.notify("initialized")
        return self.request("thread/start", {"cwd": str(cwd)})["thread"]

    def call_tool(self, thread: str, tool: str, arguments: dict) -> dict:
        return self.request("mcpServer/tool/call",
                            {"server": "crapkit", "tool": tool, "threadId": thread, "arguments": arguments})


def codex_config(box) -> Path:
    return Path(box.env["CODEX_HOME"]) / "config.toml"


@cell("lin-codex-mcp-add", channel="codex mcp add / TOML", harness="Codex 0.156.1",
      scenario="fresh: env allowlist, no VIRTUAL_ENV; call with repo; `required = true` asserted through "
               "app-server thread/start error", use_cases="MCP client wiring", os="linux", image="core",
      cadence="push")
def test_codex_mcp_add(box, templates, candidate):
    repo = repo_box(box, templates)
    server = profiles.parsed_doc("codex", repo)
    box.run(["codex", "mcp", "add", "crapkit", "--", *server.argv], cwd=repo, expect=0)
    box.env["VIRTUAL_ENV"] = str(box.root / "venv-the-user-activated")
    with AppServer.in_box(box, ["codex", "app-server"], cwd=repo) as app:
        thread = app.start_thread(repo)["id"]
        answer = app.call_tool(thread, "get_next_item", {"repo": str(repo)})
    assert not answer["isError"] and answer["structuredContent"]["item"]["path"] == "calc/grade.py"
    start = last_start(box)
    allowed = set(profiles.load("codex").spawn["env_allow"])
    assert start["cwd"] == str(repo) and "VIRTUAL_ENV" not in start["env"]
    assert set(start["env"]) <= allowed, sorted(set(start["env"]) - allowed)
    assert initialize_params(start)["protocolVersion"] == profiles.load("codex").initialize["protocol"]
    assert_required_server_fails_the_thread(box, repo)


def assert_required_server_fails_the_thread(box, repo: Path) -> None:
    """A server marked `required = true` that cannot start stops thread/start."""
    config = codex_config(box)
    text = config.read_text(encoding="utf-8").replace('command = "crapkit"', 'command = "crapkit-missing"')
    config.write_text(text.replace("[mcp_servers.crapkit]\n", "[mcp_servers.crapkit]\nrequired = true\n"),
                      encoding="utf-8")
    with AppServer.in_box(box, ["codex", "app-server"], cwd=repo) as app:
        app.request("initialize", {"clientInfo": {"name": "deploy-cell", "version": "1"}})
        app.notify("initialized")
        with pytest.raises(AssertionError, match="crapkit"):
            app.request("thread/start", {"cwd": str(repo)})


# --- Cursor agent ------------------------------------------------------------------------

def cursor_tools(box, repo: Path) -> list[str]:
    box.run(["cursor-agent", "mcp", "enable", "crapkit"], cwd=repo, expect=0)
    listed = box.run(["cursor-agent", "mcp", "list-tools", "crapkit"], cwd=repo, expect=0).stdout
    return re.findall(r"^- (\w+) \(", listed, re.M)


def assert_cursor_start(box, repo: Path) -> None:
    start = last_start(box)
    allowed = {profiles._folded(name) for name in profiles.allowed_names(profiles.load("cursor"))}
    assert start["cwd"] == str(repo)
    assert {profiles._folded(name) for name in start["env"]} <= allowed | {"PATH"}, sorted(start["env"])


@cell("lin-cursor-cli", channel=".cursor/mcp.json", harness="Cursor agent 2026.09.23",
      scenario="fresh: enable + list-tools 12; shim cwd and env", use_cases="MCP client wiring", os="linux",
      image="core", cadence="push")
def test_cursor_cli(box, templates):
    repo = repo_box(box, templates)
    profiles.doc_server(box, repo, "cursor")
    assert len(cursor_tools(box, repo)) == TOOLS
    assert_cursor_start(box, repo)


def upgrade_command(row: str) -> str:
    """The command docs/upgrading.md's install table gives for `row`, verbatim."""
    text = (docsnip.root() / "docs" / "upgrading.md").read_text(encoding="utf-8")
    match = re.search(rf"^\| {re.escape(row)} \| `([^`]+)` \|$", text, re.M)
    assert match, f"docs/upgrading.md has no install-table row {row!r}"
    return match[1]


def user_venv_crapkit(box, version: str) -> Path:
    """crapkit==version in the user's own venv, behind the shim, with the harness CLIs on PATH."""
    venv = box.root / "user-venv"
    real = profiles.install_crapkit(box, venv, spec=f"crapkit[py]=={version}")
    box.prepend_path(profiles.scripts_dir(venv))
    box.prepend_path(shim.install(box, str(real)))
    profiles.add_harnesses(box)
    return venv


@cell("lin-up-cursor-0.7.6", channel=".cursor/mcp.json", harness="Cursor agent",
      scenario="upgrade from 0.7.6 per docs; list-tools and shim serverInfo show candidate",
      use_cases="MCP client upgrade", os="linux", image="core", cadence="push")
def test_up_cursor_0_7_6(box, templates, candidate):
    user_venv_crapkit(box, OLD)
    repo = repos.checkout(box, "py-pytest", cache=templates)
    profiles.measure(box, repo)
    profiles.doc_server(box, repo, "cursor")
    assert len(cursor_tools(box, repo)) == TOOLS and server_version(box, repo) == OLD

    box.script(upgrade_command("pip with the Python coverage extra"), cwd=repo, expect=0)
    assert len(cursor_tools(box, repo)) == TOOLS
    assert server_version(box, repo) == candidate.version


def server_version(box, repo: Path) -> str:
    """serverInfo.version from the launcher the harness starts (the shim)."""
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        return client.initialize()["serverInfo"]["version"]


# --- the :full harnesses -------------------------------------------------------------------

def gemini_connect(box, repo: Path) -> list[str]:
    profiles.doc_server(box, repo, "gemini-cli")
    untrusted = plain(box.run(["gemini", "mcp", "list"], cwd=repo, expect=0))
    assert "disabled because this folder is untrusted" in untrusted and "Disabled" in untrusted
    trusted = box.run(["gemini", "mcp", "list"], cwd=repo, env={"GEMINI_CLI_TRUST_WORKSPACE": "true"}, expect=0)
    assert re.search(r"crapkit: .* Connected", plain(trusted))
    return []


def opencode_connect(box, repo: Path) -> list[str]:
    profiles.doc_server(box, repo, "opencode")
    assert re.search(r"crapkit\W+connected", plain(box.run(["opencode", "mcp", "list"], cwd=repo, expect=0)))
    return []


def amp_connect(box, repo: Path) -> list[str]:
    profiles.doc_server(box, repo, "amp")
    env = profiles.load("amp").real_cli["env"]
    status = box.run(["amp", "mcp", "doctor", "crapkit"], cwd=repo, env=env).stdout
    match = re.search(r"crapkit \(.*\): connected \((\d+) tools: ([^)]*)\)", status)
    assert match, status
    return [name.strip() for name in match[2].split(",")]


def omp_connect(box, repo: Path) -> list[str]:
    profiles.doc_server(box, repo, "oh-my-pi")
    frames = omp_rpc(box, repo, "/mcp test crapkit")
    output = next(frame["text"] for frame in frames if frame.get("type") == "command_output")
    assert re.match(r'Server "crapkit" connected \(\d+ tools\)', output), output
    return re.findall(r"^  - (\w+)$", output, re.M)


def omp_rpc(box, repo: Path, message: str) -> list[dict]:
    """One prompt through `omp --mode rpc`; the frames it printed before stdin closed."""
    prompt = json.dumps({"type": "prompt", "message": message}) + "\n"
    step = box.run(["omp", "--mode", "rpc", "--no-session"], cwd=repo, input=prompt,
                   env={"OPENAI_API_KEY": profiles.STUB_KEY})
    return [json.loads(line) for line in step.stdout.splitlines() if line.startswith("{")]


def aider_connect(box, repo: Path) -> list[str]:
    """Aider's lint-cmd on a clean file: exit 0, no model call (a key must be set)."""
    profiles.doc_server(box, repo, "aider")
    (repo / "calc" / "clean.py").write_text("def double(x):\n    return 2 * x\n", encoding="utf-8")
    step = box.run(["aider", "--model", profiles.STUB_MODEL, "--lint", "calc/clean.py", "--no-check-update",
                    "--analytics-disable", "--no-gitignore", "--yes-always", "--no-show-model-warnings"], cwd=repo,
                   env={"OPENAI_API_KEY": profiles.STUB_KEY, "OPENAI_API_BASE": "http://127.0.0.1:9/v1"}, expect=0)
    assert "0 over ceiling" in step.stdout and "## See relevant line" not in step.stdout
    return []


def item_path(text: str) -> str | None:
    """get_next_item's path in a tool result the model received."""
    answer = profiles.result_json(text)
    return answer.get("item", {}).get("path") if isinstance(answer, dict) else None


ANSI = re.compile(r"\[[0-9;]*m")


def plain(step) -> str:
    """What a user reads in a terminal: stdout and stderr with colour codes dropped."""
    return ANSI.sub("", step.stdout + step.stderr)


def model_connect(key: str):
    """A harness that lists tools only to its model: one headless turn on the stub."""
    def connect(box, repo: Path) -> list[str]:
        script, bodies, _ = profiles.stub_session(box, repo, key, [("get_next_item", {})])
        box.transcript.attach("offered-tools", script.offered)
        results = profiles.tool_results(bodies)
        assert any(item_path(text) == "calc/grade.py" for text in results), results
        return script.crapkit_tools()
    return connect


CONNECT = {"gemini-cli": gemini_connect, "opencode": opencode_connect, "amp": amp_connect, "oh-my-pi": omp_connect,
           "aider": aider_connect, "copilot-cli": model_connect("copilot-cli"), "cline": model_connect("cline"),
           "crush": model_connect("crush"), "goose": model_connect("goose"), "continue": model_connect("continue"),
           "junie": model_connect("junie")}
LISTS_TOOLS = {"amp", "oh-my-pi", "copilot-cli", "cline", "crush", "goose", "continue", "junie"}
CELLS = {"gemini-cli": "lin-gemini", "opencode": "lin-opencode", "copilot-cli": "lin-copilot-cli", "cline": "lin-cline",
         "amp": "lin-amp", "crush": "lin-crush", "oh-my-pi": "lin-omp", "goose": "lin-goose",
         "continue": "lin-continue", "junie": "lin-junie", "aider": "lin-aider"}


def assert_offer(box, key: str) -> None:
    """The protocol revision the shim saw the harness offer is its profile's."""
    profile = profiles.load(key)
    if profile.mcp:
        assert initialize_params(last_start(box)).get("protocolVersion") == profile.initialize["protocol"] \
            or profile.initialize["discover_first"]


def connect_cell(key: str):
    @cell(CELLS[key], channel="each harness's doc config", harness="pinned real CLIs",
          scenario="fresh: headless connect check; connected + 12 tools where listable; Gemini untrusted folder; "
                   "Junie 2025-03-26 offer; Aider clean lint exit 0",
          use_cases="MCP client wiring", os="linux", image="full", cadence="nightly")
    def test(box, templates):
        repo = repo_box(box, templates)
        tools = CONNECT[key](box, repo)
        if key in LISTS_TOOLS:
            assert len(tools) == TOOLS, tools
        assert_offer(box, key)
    test.__name__ = f"test_{CELLS[key].replace('-', '_')}"
    return test


for _key in CONNECT:
    globals()[f"test_{CELLS[_key].replace('-', '_')}"] = connect_cell(_key)


# --- Windows ---------------------------------------------------------------------------------

def cursor_windows(box) -> Path:
    """The pinned Windows Cursor agent, unpacked beside the toolchain."""
    home = Path(box.toolchain.source).parent / "cursor-agent-windows" / "dist-package"
    assert (home / "cursor-agent.cmd").is_file(), \
        f"kit: toolchain.py does not install cursor-agent-windows-x64 from pins.toml into {home.parent}"
    return home


def assert_exe_start(box, repo: Path) -> None:
    """The harness started the shim's crapkit.exe in the repo."""
    start = last_start(box)
    assert start["cwd"].lower() == str(repo).lower(), start["cwd"]
    assert Path(start["argv"][0]).stem.lower() == "crapkit", start["argv"]


@cell("win-cursor-cli", channel=".cursor/mcp.json", harness="Cursor agent (native install pinned)",
      scenario="fresh: list-tools; Windows argv and cwd in shim", use_cases="MCP wiring", os="windows", image=None,
      cadence="nightly")
def test_win_cursor_cli(box, templates):
    repo = repo_box(box, templates)
    box.env["PATH"] += os.pathsep + str(cursor_windows(box))
    profiles.doc_server(box, repo, "cursor")
    assert len(cursor_tools(box, repo)) == TOOLS
    assert_exe_start(box, repo)


WINDOWS_CONNECT = {"gemini-cli": gemini_connect, "opencode": opencode_connect,
                   "copilot-cli": model_connect("copilot-cli"), "cline": model_connect("cline")}


@cell("win-real-cli", channel="doc configs", harness="Gemini, OpenCode, Copilot CLI, Cline",
      scenario="fresh: connect checks with shim .exe first on PATH", use_cases="MCP wiring", os="windows",
      image=None, cadence="nightly")
@pytest.mark.parametrize("key", sorted(WINDOWS_CONNECT))
def test_win_real_cli(key, box, templates):
    repo = repo_box(box, templates)
    tools = WINDOWS_CONNECT[key](box, repo)
    assert key not in LISTS_TOOLS or len(tools) == TOOLS, tools
    assert_exe_start(box, repo)
