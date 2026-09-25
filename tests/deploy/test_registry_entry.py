"""The MCP registry entry: server.json as a registry client reads it.

A client that installs servers from the registry composes the start command
from the entry's package: the runtime (`runtimeHint`, uvx for a PyPI package
that names none), the identifier pinned to the entry's version, then the
package arguments. Clients spell the pin two ways, `crapkit@<v>` and
`crapkit==<v>`; the cell starts the server both ways from the stamped
server.json, offline, in a measured repo, and drives initialize and one call.
"""
from __future__ import annotations

import json
from pathlib import Path

from kit import docsnip, installers, repos
from kit.cells import cell
from kit.mcp_client import McpClient

PACKET = "deploy-channels"
PINS = ("{identifier}@{version}", "{identifier}=={version}")


def entry() -> dict:
    return json.loads((docsnip.root() / "server.json").read_text(encoding="utf-8"))


def start_command(package: dict, pin: str) -> list[str]:
    """What a registry client runs for a PyPI package entry."""
    runtime = package.get("runtimeHint", "uvx")
    arguments = [argument["value"] for argument in package.get("packageArguments", [])]
    return [runtime, pin.format(**package), *arguments]


def _measured(box, templates) -> Path:
    installers.pip_extra(box, "3.12")
    repo = repos.checkout(box, "py-pytest", cache=templates)
    box.run(["crapkit", "init"], cwd=repo, expect=0)
    installers.allow_containers_here(repo)
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    return repo


def _session(box, repo: Path, argv: list[str]) -> dict:
    with McpClient.in_box(box, argv, cwd=repo) as client:
        info = client.initialize()
        item = client.call("get_next_item", {})
        client.close()
    return {"info": info, "item": item}


@cell("lin-registry-entry", channel="server.json", harness="registry clients",
      scenario="fresh: `uvx crapkit@<v> mcp` and `uvx crapkit==<v> mcp` composed from server.json; initialize and "
               "one call", use_cases="registry start", os="linux", image="core", cadence="nightly", real_cli=False)
def test_the_registry_entry_starts_the_candidate_both_ways(box, templates, candidate):
    server = entry()
    package = server["packages"][0]
    repo = _measured(box, templates)
    seen = {pin: _session(box, repo, start_command(package, pin)) for pin in PINS}

    assert server["version"] == package["version"] == candidate.version
    assert (package["registryType"], package["transport"]) == ("pypi", {"type": "stdio"})
    for pin, session in seen.items():
        assert session["info"]["serverInfo"] == {"name": "crapkit", "version": candidate.version}, pin
        assert session["item"]["isError"] is False, (pin, session["item"])
        assert session["item"]["structuredContent"]["item"]["path"] == "calc/grade.py", pin
