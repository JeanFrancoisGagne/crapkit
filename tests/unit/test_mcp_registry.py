"""The MCP tool registry is pure data: names, schemas, argv mappings. The
server assembles from it; these tests need no transport."""
from crapkit.mcp_server import TOOLS, build_argv, tool_listing


def test_registry_names_are_unique_and_snake_case():
    names = [t["name"] for t in TOOLS]
    assert len(names) == len(set(names))
    assert all(n.replace("_", "").isalnum() and n == n.lower() for n in names)
    assert {"next_item", "worklist", "explain", "doctor", "coupling", "duplication",
            "ratchet_report", "runs"} <= set(names)


def test_listing_shape_matches_mcp():
    for tool in tool_listing():
        assert tool["inputSchema"]["type"] == "object"
        assert isinstance(tool["description"], str) and tool["description"]


def test_build_argv_maps_flags_positionals_and_lists():
    tool = next(t for t in TOOLS if t["name"] == "next_item")
    argv = build_argv(tool, {"top": 3, "exclude": ["cli.py", "lanes"]})
    assert argv[0] == "next-item"
    assert argv[argv.index("--top") + 1] == "3"
    assert argv.count("--exclude") == 2

    explain = next(t for t in TOOLS if t["name"] == "explain")
    argv = build_argv(explain, {"path": "src/a.ts", "name": "dispatch"})
    assert argv[:3] == ["explain", "src/a.ts", "dispatch"]


def test_the_served_schema_types_exclude_as_an_array_of_fragments():
    """A client that validates arguments against the served schema rejects the
    string form the docs used to print before the call ever reaches crapkit."""
    (next_item,) = [t for t in tool_listing() if t["name"] == "next_item"]

    assert next_item["inputSchema"]["properties"]["exclude"] == {
        "type": "array", "items": {"type": "string"}}


def test_every_fragment_in_the_array_becomes_its_own_cli_flag():
    tool = next(t for t in TOOLS if t["name"] == "next_item")

    argv = build_argv(tool, {"exclude": ["stats", "grade", "report"]})

    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--exclude"] == \
        ["stats", "grade", "report"], "the CLI flag repeats; the array is how you repeat it"


def test_mutating_commands_are_not_exposed():
    names = {t["name"] for t in TOOLS}
    assert not {"verify", "coverage", "mutate", "ratchet_seed"} & names, \
        "the MCP surface is read-side only; runs that write baselines stay in the CLI"


def test_next_item_argv_carries_no_json_flag():
    """next-item always emits JSON and defines no --json flag; appending one
    made the MCP tool exit 2 with a usage dump (found by the release audit)."""
    tool = next(t for t in TOOLS if t["name"] == "next_item")
    argv = build_argv(tool, {})
    assert "--json" not in argv
