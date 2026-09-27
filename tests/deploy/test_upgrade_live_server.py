"""An MCP server keeps running while its user upgrades crapkit underneath it.

A client holds a 0.7.6 `crapkit mcp` session open (initialized, one call
made) while the user pip-upgrades the venv the server runs from and walks the
guide's reseed fence: coverage, prune, seed. Then the client, still on the old
server, calls get_next_item, list_claims and check_gate. Each call must answer
with data or name the restart the guide asks for; none may surface a SQLite
error, and no run, claim or override the state held may be lost.
"""
from __future__ import annotations

import json

from kit import state, state_manifest
from kit.cells import cell
from kit.mcp_client import McpClient

PACKET = "deploy-upgrade"
OLD = state.source_version("0.7.6")
SQLITE_WORDS = ("sqlite", "database is locked", "no such column", "no such table", "malformed")


def text_of(result: dict) -> str:
    return "\n".join(part.get("text", "") for part in result.get("content", []))


def answered(name: str, result: dict) -> str:
    """The call's text, held to: data, or a line that names a restart; never a SQLite error."""
    text = text_of(result)
    lowered = text.lower()
    assert not [word for word in SQLITE_WORDS if word in lowered], f"{name} surfaced a store error: {text}"
    assert not result.get("isError") or "restart" in lowered, f"{name} failed without naming a restart: {text}"
    return text


@cell("lin-up-live-server", channel="pip venv", harness="spec client holding 0.7.6 `crapkit mcp`",
      scenario="upgrade while serving: coverage, prune, seed; old server answers or names the restart; "
               "no sqlite error, no lost rows",
      use_cases="store migration, MCP launcher", os="linux", image="core", cadence="push")
def test_lin_up_live_server(box, templates, candidate):
    source = state.build(box, OLD, cache=templates)
    repo = source.checkout(box)
    state.pip_venv(box)
    state.pip_install(box, f"crapkit[py]=={OLD}")
    before = state_manifest.take(repo)

    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        assert client.initialize()["serverInfo"]["version"] == OLD
        answered("get_next_item before the upgrade", client.call("get_next_item"))
        state.run_line(box, repo, state.upgrade_line("pip with the Python coverage extra"))
        state.reseed(box, repo)
        texts = {name: answered(name, client.call(name, arguments))
                 for name, arguments in (("get_next_item", {}), ("list_claims", {}),
                                         ("check_gate", {"path": "calc/grade.py"}))}

    claims = json.loads(texts["list_claims"])["claims"]
    assert set(source.claims) <= {(claim["path"], claim["long_name"]) for claim in claims}, texts["list_claims"]
    state.kept(box, repo, source)
    state_manifest.check(box, before, state_manifest.take(repo))
    item = json.loads(texts["get_next_item"]).get("item") or {}
    assert not state.same_function(item, source.facts["claimed"]), (
        f"the old server handed out the function a claim holds, as {item.get('function')!r}")
