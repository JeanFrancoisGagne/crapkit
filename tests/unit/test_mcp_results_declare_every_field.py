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
from crapkit.gitpaths import readable


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


def _array_gaps(value: list, schema: dict, where: str) -> list[str]:
    return sorted({gap for item in value
                   for gap in undeclared(item, schema["items"], f"{where}[]")})


def undeclared(value, schema: dict, where: str = "") -> list[str]:
    if isinstance(value, dict) and "properties" in schema:
        return _object_gaps(value, schema, where)
    if isinstance(value, list) and "items" in schema:
        return _array_gaps(value, schema, where)
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


def _check_gate_on(scored, name: str) -> dict:
    """check_gate's answer on a file written under NAME, a TypeScript body over its ceiling."""
    (scored / name).parent.mkdir(parents=True, exist_ok=True)
    (scored / name).write_text(KNOTTY, encoding="utf-8")
    return mcp_server._call_tool(scored, "check_gate", {"path": name})


def test_check_gates_verdict_on_a_name_that_is_not_utf8_declares_every_field(scored):
    """The verdict on a file a scope takes: a POSIX name holding byte e9, or
    on NTFS a lone surrogate."""
    result = _check_gate_on(scored, "src/caf\udce9.ts")

    assert result["isError"] is False, result["content"][0]["text"][-600:]
    assert result["structuredContent"]["gate"]["unread_files"][0]["path"] == "src/caf\\xe9.ts"
    assert undeclared(result["structuredContent"], _output_schema("check_gate")) == []


def test_check_gates_verdict_on_a_name_that_is_not_utf8_carries_every_key_a_verdict_carries(scored):
    """The verdict held functions, schema and gate only, so a reader of
    baseline_run found no key on an isError false result, where every other
    check_gate answer carries one."""
    plain = mcp_server._call_tool(scored, "check_gate", {"path": "src/app.ts"})["structuredContent"]

    unread = _check_gate_on(scored, "src/caf\udce9.ts")["structuredContent"]

    assert sorted(unread) == sorted(plain)
    assert (unread["baseline_run"], unread["baseline_commit"], unread["note"]) == (
        plain["baseline_run"], plain["baseline_commit"], plain["note"])


def test_check_gate_never_hands_a_name_that_is_not_utf8_to_the_cli(scored, monkeypatch):
    """A uv-built venv's launcher on Windows hands a lone surrogate on argv to the child as
    U+FFFD, so the child looked up src/caf\ufffd.ts, a file nobody named, and
    answered isError true with `does not exist`. The server decides the
    verdict itself, and no word it spawns holds the name."""
    spawned = []
    real = mcp_server.run_owned
    monkeypatch.setattr(mcp_server, "run_owned",
                        lambda argv, **kw: spawned.append(argv) or real(argv, **kw))

    result = _check_gate_on(scored, "src/caf\udce9.ts")

    assert result["isError"] is False, result["content"][0]["text"][-600:]
    assert [word for argv in spawned for word in argv if not readable(word)] == []


def test_the_changelog_names_the_windows_python_that_read_the_name_as_ufffd():
    """Only a uv-built venv's launcher hands the CLI a lone surrogate as one
    U+FFFD. Under a plain CPython, pipx or `python -m venv` install on Windows,
    0.8.0 answered with the same traceback it gave on Linux, so a line saying
    every Windows install got `does not exist` names a symptom most never saw."""
    changelog = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("\n## 0.8.1 ")
    release = " ".join(changelog[start:changelog.index("\n## ", start + 1)].split())

    assert "On Windows it answered `isError: true`" not in release
    assert ("Under a uv-built venv on Windows, whose launcher hands the CLI such a name as "
            r"one U+FFFD, it answered `isError: true` with `src/caf\ufffd.ts does not exist`"
            ) in release


@pytest.mark.parametrize("name", ["docs/caf\udce9.md", "tools/caf\udce9.ts"],
                         ids=["no-scope-language", "outside-every-scope-path"])
def test_check_gate_judges_a_name_no_scope_takes_as_any_unscoped_file(scored, name):
    """Q17: a name no scope takes is skipped, not refused. The server failed the
    gate on any existing name that is not UTF-8, scoped or not."""
    result = _check_gate_on(scored, name)

    assert result["isError"] is False, result["content"][0]["text"][-600:]
    gate = result["structuredContent"]["gate"]
    assert (gate["ok"], gate["judged"], gate["breaches"], gate["unread_files"]) == (True, 0, [], [])


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
