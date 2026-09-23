"""A schema that lists remedies admits every remedy the scorer can emit.

A client that validates structured results against outputSchema rejects a whole
answer over one value its enum does not list.
"""
from crapkit.mcp_server import tool_listing

VOCABULARY = {"ok", "add-tests", "decompose", "split-lines"}


def _enum_fields(node):
    if isinstance(node, dict):
        if "enum" in node:
            yield node
        for value in node.values():
            yield from _enum_fields(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _enum_fields(value)


def remedy_fields() -> list[dict]:
    return [field for tool in tool_listing() for field in _enum_fields(tool)
            if "add-tests" in field["enum"]]


def _admits_null(field: dict) -> bool:
    types = field["type"]
    return "null" in ((types,) if isinstance(types, str) else types)


def test_every_remedy_enum_admits_the_whole_vocabulary():
    fields = remedy_fields()
    assert fields, "no schema lists remedies any more: this contract has lost its subject"
    assert [set(field["enum"]) - {None} for field in fields] == [VOCABULARY] * len(fields)


def test_a_remedy_enum_lists_null_exactly_when_its_type_admits_null():
    """An inventory-only run has no remedy to give, so list_worklist's rows
    carry null there; a field admitting null in its type must list it in its enum."""
    fields = remedy_fields()
    assert [None in field["enum"] for field in fields] == [_admits_null(f) for f in fields]
    assert any(_admits_null(f) for f in fields), "list_worklist's remedy must admit null"


def test_every_remedy_description_says_what_split_lines_means():
    assert all("split-lines" in field["description"] for field in remedy_fields())
