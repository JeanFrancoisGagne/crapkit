"""The Claude Agent SDKs start crapkit the two ways an SDK host wires it: the
plugin, loaded from its installed directory, and an `mcpServers` entry pasted
from docs/agent-json.md's client wiring with the repo path filled in.

Neither run makes a model turn or needs a key. kit/sdk_status.mjs and
kit/sdk_status.py open a session whose prompt never arrives and read the
SDK's own MCP status until no server is pending.

    lin-agent-sdk      TypeScript SDK: both servers connected with 12 tools; with options.env
                       built without PATH, what the host sees
    lin-agent-sdk-py   Python SDK: get_mcp_status reports both servers connected with 12 tools
"""
from __future__ import annotations

import json
from pathlib import Path

from kit import docsnip
from kit.cells import cell
from test_claude_plugin import (CLAUDE, TOOLS, cli_venv, github, harness_on_path, installed, measured_repo,
                                page_lines, run_lines)

PACKET = "deploy-plugins"
KIT = Path(__file__).resolve().parent / "kit"
PLACEHOLDER = "/absolute/path/to/your/repo"
PLUGIN_SERVER = "plugin:crapkit:crapkit"


def wiring(repo: Path) -> dict:
    """docs/agent-json.md's client wiring, the placeholder replaced by the repo."""
    block = docsnip.fence("docs/agent-json.md", "MCP server", contains='"mcpServers"')
    return json.loads(block.text.replace(PLACEHOLDER, repo.as_posix()))["mcpServers"]


def sdk_host(box, candidate, templates) -> tuple[Path, Path]:
    """A measured repo and the plugin installed by the README lines: (repo, plugin root)."""
    cli_venv(box)
    repo = measured_repo(box, templates)
    github(box, candidate)
    harness_on_path(box)
    run_lines(box, page_lines(CLAUDE), cwd=repo)
    return repo, Path(installed(box)["installPath"])


def write_options(box, options: dict) -> Path:
    path = box.root / "sdk-options.json"
    path.write_text(json.dumps(options, indent=2), encoding="utf-8")
    return path


def ts_sdk(box) -> Path:
    """The pinned @anthropic-ai/claude-agent-sdk under the full image's harness prefix."""
    found = [Path(bin_dir).parent / "node_modules" / "@anthropic-ai" / "claude-agent-sdk"
             for bin_dir in box.toolchain["harness_bin"]]
    return next(path for path in found if (path / "sdk.mjs").exists())


def ts_status(box, repo: Path, options: dict, env: str = "inherit") -> dict[str, dict]:
    step = box.run(["node", str(KIT / "sdk_status.mjs"), "--sdk", str(ts_sdk(box)), "--cwd", str(repo),
                    "--options", str(write_options(box, options)), "--env", env], cwd=repo, expect=0)
    return {server["name"]: server for server in json.loads(step.stdout)}


def connected(server: dict) -> tuple[str, int]:
    return server["status"], len(server["tools"])


@cell("lin-agent-sdk", channel="SDK mcpServers + plugins", harness="Agent SDK TS",
      scenario="fresh: 12 tools from plugin:crapkit:crapkit and from the docs' mcpServers entry; options.env "
      "without PATH", use_cases="MCP start", os="linux", image="full", cadence="nightly")
def test_typescript_sdk_connects_crapkit(box, candidate, templates):
    repo, plugin = sdk_host(box, candidate, templates)
    options = {"plugins": [{"type": "local", "path": str(plugin)}], "mcpServers": wiring(repo)}
    servers = ts_status(box, repo, options)
    bare = ts_status(box, repo, options, env="no-path")
    box.transcript.attach("sdk-status", {"inherit": servers, "no-path": bare})

    assert connected(servers[PLUGIN_SERVER]) == ("connected", TOOLS)
    assert connected(servers["crapkit"]) == ("connected", TOOLS)
    assert {name: server["status"] for name, server in bare.items() if "crapkit" in name} == {
        PLUGIN_SERVER: "failed", "crapkit": "failed"}


def py_sdk(box) -> Path:
    """The interpreter holding the pinned claude-agent-sdk (the full image's /opt/agent-sdk-py)."""
    python = Path("/opt/agent-sdk-py/bin/python")
    assert python.exists(), "the image holds no /opt/agent-sdk-py"
    return python


@cell("lin-agent-sdk-py", channel="Python SDK", harness="claude-agent-sdk (Python)",
      scenario="fresh: get_mcp_status connected, 12 tools, for the plugin and the docs' mcpServers entry",
      use_cases="MCP start", os="linux", image="full", cadence="nightly")
def test_python_sdk_connects_crapkit(box, candidate, templates):
    repo, plugin = sdk_host(box, candidate, templates)
    options = {"plugins": [{"type": "local", "path": str(plugin)}], "mcp_servers": wiring(repo)}
    step = box.run([str(py_sdk(box)), str(KIT / "sdk_status.py"), "--cwd", str(repo),
                    "--options", str(write_options(box, options))], cwd=repo, expect=0)
    servers = {server["name"]: server for server in json.loads(step.stdout)}

    assert connected(servers[PLUGIN_SERVER]) == ("connected", TOOLS)
    assert connected(servers["crapkit"]) == ("connected", TOOLS)
