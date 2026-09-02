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

    exclude = next_item["inputSchema"]["properties"]["exclude"]

    assert exclude["type"] == "array" and exclude["items"] == {"type": "string"}


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

# --- what the listing teaches a client's model --------------------------------
#
# A registry grades every tool on how well it describes itself: purpose, when
# to reach for it, what it touches, and what each parameter means. The listing
# is the only thing a connected model reads, so a bare type with no description
# is a parameter the model guesses at.

def test_every_tool_declares_the_read_only_annotations():
    """Every tool shells to a read-only CLI command; the annotations say so in
    the field clients actually read, instead of only in this module's docstring."""
    for entry in tool_listing():
        assert entry["annotations"] == {"readOnlyHint": True, "idempotentHint": True,
                                        "openWorldHint": False}, entry["name"]


def test_every_parameter_carries_a_description():
    for entry in tool_listing():
        for key, prop in entry["inputSchema"]["properties"].items():
            assert prop.get("description", "").strip(), f"{entry['name']}.{key} says nothing"


def test_every_description_says_what_and_when():
    """Two sentences: what the tool answers, then when to reach for it (or how
    it relates to its neighbour). One-line noun phrases leave a model unable to
    tell worklist from next_item; essays get truncated by clients."""
    for entry in tool_listing():
        d = entry["description"]
        assert d.count(". ") >= 1, f"{entry['name']} never says when to use it: {d!r}"
        assert len(d) <= 340, f"{entry['name']} rambles at {len(d)} chars"


def test_initialize_hands_the_client_instructions():
    """The server-level `instructions` field is where the two-command
    prerequisite lives: the tools answer from a measured store, and a client
    connected to an unmeasured repo should learn that here, not from nine
    identical error results."""
    from crapkit.mcp_server import _handle
    from pathlib import Path as _P

    resp = _handle(_P("."), {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

    ins = resp["result"]["instructions"]
    assert "read" in ins and "coverage" in ins and "next_item" in ins

# --- the protocol revision the handshake settles ------------------------------
#
# A server that always answers with the 2024-11-05 revision tells a current
# client to drop everything newer, annotations and structured output included.
# The handshake is a negotiation: echo the client's revision when this server
# implements it, and offer the newest one it does otherwise.

def _initialize(params):
    from pathlib import Path as _P

    from crapkit.mcp_server import _handle

    return _handle(_P("."), {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": params})["result"]


def test_a_clients_supported_revision_is_echoed_back():
    assert _initialize({"protocolVersion": "2025-06-18"})["protocolVersion"] == "2025-06-18"
    assert _initialize({"protocolVersion": "2024-11-05"})["protocolVersion"] == "2024-11-05"


def test_an_unknown_revision_gets_the_newest_this_server_implements():
    """The spec's rule for a version the server does not support: respond with
    the latest it does, and let the client decide whether to proceed."""
    assert _initialize({"protocolVersion": "2099-01-01"})["protocolVersion"] == "2025-06-18"
    assert _initialize({})["protocolVersion"] == "2025-06-18"


# --- structured output --------------------------------------------------------

def test_a_json_answer_reaches_the_client_parsed_as_well_as_printed():
    """Every tool that emits JSON did so for machines; a client on the current
    revision reads structuredContent and skips re-parsing the text block."""
    from crapkit.mcp_server import _structured

    result = _structured({"content": [{"type": "text", "text": '{"empty": false, "top": 1}'}],
                          "isError": False})

    assert result["structuredContent"] == {"empty": False, "top": 1}
    assert result["content"][0]["text"] == '{"empty": false, "top": 1}'


def test_prose_and_errors_stay_text_only():
    from crapkit.mcp_server import _structured

    prose = _structured({"content": [{"type": "text", "text": "doctor: no problems found"}],
                         "isError": False})
    err = _structured({"content": [{"type": "text", "text": '{"a": 1}'}], "isError": True})

    assert "structuredContent" not in prose
    assert "structuredContent" not in err

