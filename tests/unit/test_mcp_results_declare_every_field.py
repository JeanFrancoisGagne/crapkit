"""Every field an MCP tool's result carries is declared in its outputSchema.

README and docs/agent-json.md promise an outputSchema whose fields are described
one by one. Rows carried `occurrence` and worklist rows `handle` that no schema
named, and check_gate's path-keyed `ceilings` map declared no value type.
Validation still passed, because no schema sets additionalProperties, so the
check here is stricter than a validating client: it walks a real result and
names each key its schema leaves out.
"""
import json
from pathlib import Path

import pytest

from cli_inproc_repo import (KNOTTY, add_knotty, commit_all, repo, seed_artifacts,  # noqa: F401
                             template_repo)

from crapkit import mcp_server
from crapkit.cli import main


def _extra_schema(schema: dict):
    extra = schema.get("additionalProperties")
    return extra if isinstance(extra, dict) else None


def _object_gaps(value: dict, schema: dict, where: str) -> list[str]:
    properties, extra = schema["properties"], _extra_schema(schema)
    gaps = []
    for key, item in value.items():
        sub = properties.get(key, extra)
        if sub is None:
            gaps.append(f"{where}.{key}")
        else:
            gaps += undeclared(item, sub, f"{where}.{key}")
    return gaps


def undeclared(value, schema: dict, where: str = "") -> list[str]:
    if isinstance(value, dict) and "properties" in schema:
        return _object_gaps(value, schema, where)
    if isinstance(value, list) and "items" in schema:
        return sorted({gap for item in value for gap in undeclared(item, schema["items"],
                                                                   f"{where}[]")})
    return []


def test_the_walk_names_a_key_no_schema_declares():
    schema = {"type": "object", "properties": {"rows": {"type": "array", "items": {
        "type": "object", "properties": {"ccn": {"type": "integer"}}}},
        "map": {"type": "object", "properties": {}, "additionalProperties": {"type": "integer"}}}}

    assert undeclared({"rows": [{"ccn": 1}], "map": {"a.py": 6}}, schema) == []
    assert undeclared({"rows": [{"ccn": 1, "handle": "f"}], "top": 1}, schema) == [
        ".rows[].handle", ".top"]


@pytest.fixture()
def scored(repo, capsys):
    """knotty (ccn 8) measured, then edited again in the working tree so check_gate
    has a changed function over its ceiling to judge."""
    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    with open(repo / "src" / "app.ts", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(KNOTTY.replace("knotty", "knottier"))
    return repo


CALLS = [
    ("get_next_item", {}),
    ("get_next_item", {"top": 2}),
    ("list_worklist", {}),
    ("get_function_brief", {"path": "src/app.ts", "name": "knotty"}),
    ("check_gate", {"path": "src/app.ts"}),
    ("get_function_history", {"path": "src/app.ts", "name": "knotty", "history": True}),
    ("list_runs", {}),
    ("get_trend", {}),
    ("get_ratchet_report", {}),
    ("list_claims", {}),
    ("list_duplicate_functions", {"similarity": 0.3}),
    ("list_coupled_files", {"min_support": 1, "min_confidence": 0.1}),
    ("check_config", {}),
]


def _output_schema(name: str) -> dict:
    (entry,) = [t for t in mcp_server.tool_listing() if t["name"] == name]
    return entry["outputSchema"]


def test_each_result_declares_every_field_it_carries(scored):
    gaps = {}
    for name, arguments in CALLS:
        result = mcp_server._call_tool(scored, name, arguments)
        assert result["isError"] is False, (name, result["content"][0]["text"][-600:])
        found = undeclared(result["structuredContent"], _output_schema(name))
        if found:
            gaps[f"{name} {arguments}"] = found

    assert gaps == {}


def test_check_gate_ceilings_map_a_path_to_an_integer():
    ceilings = _output_schema("check_gate")["properties"]["gate"]["properties"]["ceilings"]

    assert ceilings["additionalProperties"] == {"type": "integer"}


def _documented_worklist_entry() -> dict:
    page = (Path(__file__).resolve().parents[2] / "docs" / "agent-json.md").read_text(
        encoding="utf-8")
    section = page.split("\n## `worklist`\n", 1)[1].split("\n## ", 1)[0]
    return json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])["active"][0]


def test_the_worklist_example_shows_every_field_an_entry_carries(scored, capsys):
    assert main(["worklist", "--json", "--repo", str(scored)]) == 0
    entry = json.loads(capsys.readouterr().out)["active"][0]

    assert set(_documented_worklist_entry()) == set(entry)
