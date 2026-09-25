"""`crapkit mcp` from a pip venv, driven the way a client on protocol
2025-06-18 drives it: all twelve tools on a measured repo, each result held
to the outputSchema tools/list declared, and the four answers that are not
data (an unmeasured directory, an unknown tool, a bad argument, a ping).

Two clients read it. The kit's Python client speaks the wire and checks each
structuredContent against its declared properties; the TypeScript SDK a
harness ships validates the same calls with its own AJV, which is what
rejects a result whole in Claude Code, Cursor and every other TS client.
"""
from __future__ import annotations

import json
from pathlib import Path

from kit import installers, repos
from kit.cells import cell
from kit.mcp_client import McpClient

PACKET = "deploy-channels"
NODE_CLIENT = Path(__file__).resolve().parent / "kit" / "mcp_node_client.mjs"
PROTOCOL = "2025-06-18"
NEWEST_SDK = "sdk-1-29"
FUNCTION = {"path": "calc/grade.py", "name": "grade"}
CALLS = {"get_next_item": {}, "list_worklist": {}, "list_runs": {}, "get_trend": {},
         "get_function_brief": FUNCTION, "get_function_history": FUNCTION, "check_config": {},
         "list_coupled_files": {}, "list_duplicate_functions": {}, "get_ratchet_report": {},
         "check_gate": {"path": "calc/grade.py"}, "list_claims": {}}
JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list,
              "object": dict, "null": type(None)}


def _measured(box, templates) -> Path:
    """py-pytest adopted from the README start's commands: a run, marks, a commit."""
    installers.pip_extra(box, "3.12")
    repo = repos.checkout(box, "py-pytest", cache=templates)
    box.run(["crapkit", "init"], cwd=repo, expect=0)
    installers.allow_containers_here(repo)
    for step in (["coverage"], ["ratchet", "seed"]):
        box.run(["crapkit", *step], cwd=repo, expect=0)
    installers.commit(box, repo, "adopt crapkit")
    return repo


def _types(spec: dict) -> tuple:
    kinds = spec.get("type", [])
    return tuple(JSON_TYPES[kind] for kind in ([kinds] if isinstance(kinds, str) else kinds))


def _mismatches(content: dict, schema: dict) -> list[str]:
    """Top-level keys the schema does not declare, and declared keys of another type."""
    declared = schema.get("properties", {})
    unknown = [f"{key}: not in outputSchema" for key in content if key not in declared]
    wrong = [f"{key}: {type(value).__name__} is not {declared[key].get('type')}" for key, value in content.items()
             if key in declared and _types(declared[key]) and not _typed(value, declared[key])]
    return unknown + wrong


def _typed(value, spec: dict) -> bool:
    """JSON types, with a bool never passing for an integer or a number."""
    kinds = _types(spec)
    return isinstance(value, kinds) and not (isinstance(value, bool) and bool not in kinds)


def _python_client(box, repo: Path) -> dict:
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        info = client.initialize(PROTOCOL)
        tools = {tool["name"]: tool for tool in client.tools()}
        results = {name: client.call(name, arguments) for name, arguments in CALLS.items()}
        pong = client.request("ping")
        unknown = client.call("worklist_of_nothing", {})
        bad = client.call("get_function_brief", {"path": 3})
        client.close()
    return {"info": info, "tools": tools, "results": results, "pong": pong, "unknown": unknown, "bad": bad}


def _node_call(box, fixtures: Path, repo: Path, name: str, arguments: dict) -> dict:
    step = box.run(["node", str(NODE_CLIENT), "--fixtures", str(fixtures), "--sdk", NEWEST_SDK,
                    "--command", box.which("crapkit"), "--arg", "mcp", "--cwd", str(repo), "--env", "inherit",
                    "--call", name, "--arguments", json.dumps(arguments)], expect=0)
    return json.loads(step.stdout)["call"]


@cell("lin-mcp-all-tools", channel="pip venv `crapkit mcp`", harness="spec client 2025-06-18",
      scenario="fresh: 12 tools checked against outputSchema; missing-config, unknown tool, bad argument, ping",
      use_cases="all 12 MCP tools", os="linux", image="core", cadence="push")
def test_every_tool_answers_within_its_output_schema(box, templates, candidate):
    repo = _measured(box, templates)
    seen = _python_client(box, repo)
    fixtures = repos.npm_fixtures(box, templates)
    validated = {name: _node_call(box, fixtures, repo, name, arguments) for name, arguments in CALLS.items()}

    assert seen["info"]["protocolVersion"] == PROTOCOL
    assert seen["info"]["serverInfo"] == {"name": "crapkit", "version": candidate.version}
    assert sorted(seen["tools"]) == sorted(CALLS)
    for name, result in seen["results"].items():
        assert result["isError"] is False, (name, result)
        assert _mismatches(result["structuredContent"], seen["tools"][name]["outputSchema"]) == [], name
    assert all(result.get("isError") is False and "structuredContent" in result for result in validated.values())
    assert seen["results"]["get_function_brief"]["structuredContent"]["path"] == "calc/grade.py"
    assert seen["pong"] == {}
    assert seen["unknown"]["isError"] and "unknown tool 'worklist_of_nothing'" in seen["unknown"]["content"][0]["text"]
    assert seen["bad"]["isError"] and "get_function_brief needs name" in seen["bad"]["content"][0]["text"]


@cell("lin-mcp-all-tools", channel="pip venv `crapkit mcp`", harness="spec client 2025-06-18",
      scenario="fresh: every tool in an unmeasured directory answers the setup pointer, not a transport error",
      use_cases="all 12 MCP tools", os="linux", image="core", cadence="push")
def test_an_unmeasured_directory_answers_every_tool_with_the_setup_pointer(box, templates):
    installers.pip_venv(box, "3.12")
    repo = repos.checkout(box, "py-pytest", cache=templates)
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        client.initialize(PROTOCOL)
        results = {name: client.call(name, arguments) for name, arguments in CALLS.items()}
        client.close()

    for name, result in results.items():
        assert result["isError"] is True, name
        assert result["content"][0]["text"].startswith(f"no crapkit.toml in {repo}"), name
        assert "Run `crapkit init`" in result["content"][0]["text"], name
