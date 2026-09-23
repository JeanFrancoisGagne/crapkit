"""list_worklist's answer on an inventory-only store fits the schema it serves.

An inventory run carries complexity and no coverage, so every ranked row's
flag, remedy, crap and cov are null. A client that validates structuredContent
against outputSchema drops the whole answer over one value the schema refuses,
and remedy's schema refused null while the other three admitted it.

The check runs the tool the way a client does, through the server's call path
and the CLI it spawns, and validates the result with the part of JSON Schema
the listing uses: type, enum, properties and items.
"""
from cli_inproc_repo import add_knotty, commit_all, repo, template_repo  # noqa: F401

from crapkit import mcp_server
from crapkit.cli import main

_JSON_TYPES = {"string": str, "number": (int, float), "integer": int, "boolean": bool,
               "object": dict, "array": list, "null": type(None)}


def _is(value, name: str) -> bool:
    if isinstance(value, bool) and name in ("integer", "number"):
        return False  # JSON keeps true and false apart from numbers; Python does not
    return isinstance(value, _JSON_TYPES[name])


def _type_errors(value, schema: dict, where: str) -> list[str]:
    names = schema.get("type")
    if names is None:
        return []
    names = (names,) if isinstance(names, str) else tuple(names)
    return [] if any(_is(value, n) for n in names) else [f"{where}: {value!r} is not {names}"]


def _enum_errors(value, schema: dict, where: str) -> list[str]:
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where}: {value!r} is not one of {schema['enum']}"]
    return []


def _child_errors(value, schema: dict, where: str) -> list[str]:
    errors = []
    if isinstance(value, dict):
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors += schema_errors(value[key], sub, f"{where}/{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += schema_errors(item, schema["items"], f"{where}/{i}")
    return errors


def schema_errors(value, schema: dict, where: str = "") -> list[str]:
    return (_type_errors(value, schema, where) + _enum_errors(value, schema, where)
            + _child_errors(value, schema, where))


def output_schema(name: str) -> dict:
    (entry,) = [t for t in mcp_server.tool_listing() if t["name"] == name]
    return entry["outputSchema"]


def test_the_checker_catches_what_a_validating_client_rejects():
    schema = {"type": "object", "properties": {"rows": {"type": "array", "items": {
        "type": "object", "properties": {"remedy": {"type": "string", "enum": ("ok",)},
                                         "ccn": {"type": "integer"}}}}}}

    assert schema_errors({"rows": [{"remedy": "ok", "ccn": 3}]}, schema) == []
    assert schema_errors({"rows": [{"remedy": None, "ccn": True}]}, schema) == [
        "/rows/0/remedy: None is not ('string',)",
        "/rows/0/remedy: None is not one of ('ok',)",
        "/rows/0/ccn: True is not ('integer',)"]


def test_list_worklist_on_an_inventory_only_store_fits_its_output_schema(repo, capsys):
    add_knotty(repo)  # ccn 8: the template's own functions sit under the floor
    commit_all(repo, "knotty")
    assert main(["inventory", "--repo", str(repo)]) == 0
    capsys.readouterr()

    result = mcp_server._call_tool(repo, "list_worklist", {})

    assert result["isError"] is False, result["content"][0]["text"]
    answer = result["structuredContent"]
    rows = answer["active"] + answer["dormant_top"]
    assert rows and all(r["remedy"] is None for r in rows), rows
    assert schema_errors(answer, output_schema("list_worklist")) == []
