"""The serve loop in-process: a request line in, a reply line out, no subprocess.

sys.stdin and sys.stdout are swapped for a StringIO pair, so these tests read
exactly the frames a client reads. What they pin is the contract ADR 0001
records: a bad tools/call (missing positional, undeclared key, wrong type) is a
tool result with isError true, written in the tool's vocabulary, and the session
continues; JSON-RPC errors stay reserved for the protocol itself (unknown
method, an exception escaping the server). Nothing here spawns the CLI: every
refusal is decided before build_argv runs, which is the point.
"""
import io
import json
import sys
from pathlib import Path

import pytest

from crapkit import mcp_server
from crapkit.mcp_server import TOOLS, build_argv, serve, tool_listing


def _rpc(msg_id, method, params="omitted"):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params != "omitted":
        msg["params"] = params
    return json.dumps(msg)


def _call(msg_id, name, arguments="omitted"):
    params = {"name": name}
    if arguments != "omitted":
        params["arguments"] = arguments
    return _rpc(msg_id, "tools/call", params)


def _serve(monkeypatch, tmp_path: Path, lines: list[str]) -> dict:
    """Every request at once through serve(); replies keyed by id. The root
    holds a crapkit.toml so a refusal cannot hide behind the no-config answer."""
    (tmp_path / "crapkit.toml").write_text("[crapkit]\ntarget = 6\n", encoding="utf-8")
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(lines) + "\n"))
    monkeypatch.setattr(sys, "stdout", out)
    rc = serve(tmp_path)
    assert rc == 0
    return {m["id"]: m for m in map(json.loads, out.getvalue().strip().splitlines())}


def _no_cli(monkeypatch):
    """A guard, not a stub: the CLI must never be spawned for a refused call."""
    def _boom(*_a, **_k):
        raise AssertionError("the CLI was spawned for a call the table already refused")
    monkeypatch.setattr(mcp_server, "_run_cli", _boom)


# --- the loop survives a bad call ---------------------------------------------

def test_a_missing_positional_is_a_tool_error_and_the_next_request_is_answered(monkeypatch,
                                                                              tmp_path):
    _no_cli(monkeypatch)
    replies = _serve(monkeypatch, tmp_path, [
        _rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}),
        _call(2, "brief", {"path": "src/a.py"}),
        _rpc(3, "tools/list"),
    ])

    assert set(replies) == {1, 2, 3}, "a refused call must not end the session"
    call = replies[2]["result"]
    assert call["isError"] is True, call
    assert call["content"][0]["text"] == "brief needs name (see inputSchema.required)"
    assert "structuredContent" not in call
    assert replies[3]["result"]["tools"], "the request after the refusal is answered"


def test_params_null_and_arguments_null_are_refusals_not_crashes(monkeypatch, tmp_path):
    _no_cli(monkeypatch)
    replies = _serve(monkeypatch, tmp_path, [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": None}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "brief", "arguments": None}}),
        _rpc(3, "ping"),
    ])

    assert set(replies) == {1, 2, 3}
    assert replies[1]["result"]["isError"] is True
    assert "unknown tool ''" in replies[1]["result"]["content"][0]["text"]
    assert replies[2]["result"]["content"][0]["text"] == \
        "brief needs path (see inputSchema.required)"
    assert replies[3]["result"] == {}


def test_an_undeclared_key_names_the_accepted_ones(monkeypatch, tmp_path):
    _no_cli(monkeypatch)
    replies = _serve(monkeypatch, tmp_path, [_call(1, "worklist", {"bogus": 1})])

    call = replies[1]["result"]
    assert call["isError"] is True
    assert call["content"][0]["text"] == \
        "worklist does not take 'bogus'; accepted: repo, top, scope"


@pytest.mark.parametrize("tool, arguments, sentence", [
    ("worklist", {"top": "three"}, 'top must be an integer (got "three")'),
    ("next_item", {"top": True}, "top must be an integer (got true)"),
    ("next_item", {"exclude": "cli.py"}, 'exclude must be an array of strings (got "cli.py")'),
    ("coupling", {"min_confidence": "high"}, 'min_confidence must be a number (got "high")'),
    ("explain", {"path": "a.py", "name": "f", "history": "yes"},
     'history must be a boolean (got "yes")'),
    ("brief", {"path": 7, "name": "f"}, "path must be a string (got 7)"),
])
def test_a_wrong_type_is_named_in_the_tools_words(monkeypatch, tmp_path, tool, arguments,
                                                  sentence):
    _no_cli(monkeypatch)
    replies = _serve(monkeypatch, tmp_path, [_call(1, tool, arguments)])

    call = replies[1]["result"]
    assert call["isError"] is True, call
    assert call["content"][0]["text"] == sentence


def test_ping_answers_an_empty_result(monkeypatch, tmp_path):
    replies = _serve(monkeypatch, tmp_path, [_rpc(1, "ping")])

    assert replies[1] == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_an_escaped_exception_is_a_32603_reply_and_the_loop_continues(monkeypatch, tmp_path):
    def _explode(*_a, **_k):
        raise RuntimeError("the store is locked")
    monkeypatch.setattr(mcp_server, "_run_cli", _explode)
    replies = _serve(monkeypatch, tmp_path, [
        _call(1, "runs", {}),
        _rpc(2, "ping"),
    ])

    assert set(replies) == {1, 2}, "the request after the exception is still answered"
    err = replies[1]["error"]
    assert err["code"] == -32603
    assert "RuntimeError" in err["message"] and "the store is locked" in err["message"]
    assert replies[2]["result"] == {}


def test_a_non_object_frame_and_a_blank_line_get_no_reply(monkeypatch, tmp_path):
    replies = _serve(monkeypatch, tmp_path, ["[1, 2]", "", "7", '"text"', _rpc(9, "ping")])

    assert set(replies) == {9}


# --- what the listing and argv say ----------------------------------------------

def test_tools_list_declares_required_from_the_positionals():
    by_name = {t["name"]: t["inputSchema"] for t in tool_listing()}

    assert by_name["brief"]["required"] == ["path", "name"]
    assert by_name["explain"]["required"] == ["path", "name"]
    for name in ("worklist", "next_item", "runs", "doctor", "coupling", "duplication",
                 "ratchet_report"):
        assert "required" not in by_name[name], f"{name} has no positional to require"


def test_explain_and_doctor_shell_to_json_like_the_other_tools():
    explain = next(t for t in TOOLS if t["name"] == "explain")
    doctor = next(t for t in TOOLS if t["name"] == "doctor")

    assert build_argv(explain, {"path": "a.py", "name": "f"}) == ["explain", "a.py", "f", "--json"]
    assert build_argv(doctor, {}) == ["doctor", "--json"]


def test_explain_history_and_tests_are_bare_flags_only_when_true():
    explain = next(t for t in TOOLS if t["name"] == "explain")
    props = next(t for t in tool_listing() if t["name"] == "explain")["inputSchema"]["properties"]

    assert props["history"]["type"] == "boolean" and props["tests"]["type"] == "boolean"
    argv = build_argv(explain, {"path": "a.py", "name": "f", "history": True, "tests": False})
    assert argv == ["explain", "a.py", "f", "--history", "--json"]
    assert "--tests" not in build_argv(explain, {"path": "a.py", "name": "f"})


# --- the docs contract: what the two pages promise about these tools ----------
#
# A docs contract moves in the same commit as the behavior it pins. Before
# 0.5.0 both pages listed explain and doctor as "plain text" and the agents
# guide said initialize reports 2024-11-05 (stale since 0.4.13).

_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_SECTION = {"AGENTS.md": "## The MCP server", "docs/agent-json.md": "## MCP server"}


def _mcp_section(page: str) -> str:
    """The body under the page's MCP heading, down to the next heading of the
    same depth; agent-json.md has other `explain` rows outside it."""
    lines = (_ROOT / page).read_text(encoding="utf-8").splitlines()
    heading = _MCP_SECTION[page]
    assert heading in lines, f"{page} lost its {heading!r} heading"
    rest = lines[lines.index(heading) + 1:]
    end = next((i for i, ln in enumerate(rest) if ln.startswith("## ")), len(rest))
    return "\n".join(rest[:end])


def _row(section: str, first_cell: str) -> str:
    rows = [ln for ln in section.splitlines() if ln.startswith(f"| {first_cell} |")]
    assert len(rows) == 1, f"expected one row for {first_cell!r}, found {len(rows)}"
    return rows[0]


@pytest.mark.parametrize("page", sorted(_MCP_SECTION))
def test_both_pages_list_explain_and_doctor_as_json_with_the_new_arguments(page):
    section = _mcp_section(page)

    assert "plain text" not in section, f"{page} still calls a --json tool plain text"
    explain = _row(section, "`explain`")
    assert "JSON" in explain and "`history`" in explain and "`tests`" in explain, explain
    assert "JSON" in _row(section, "`doctor`")
    for tool in ("`worklist`", "`next_item`"):
        assert "`scope`" in _row(section, tool), f"{page}: {tool} never names scope"


@pytest.mark.parametrize("page", sorted(_MCP_SECTION))
def test_both_pages_state_the_refusal_contract(page):
    section = " ".join(_mcp_section(page).split())

    assert "`isError`" in section and "`required`" in section, page
    assert "-32603" in section and "`ping`" in section, page


def test_the_agents_guide_no_longer_pins_the_stale_protocol_revision():
    section = _mcp_section("AGENTS.md")

    assert "reports protocol `2024-11-05`" not in section
    assert "2025-06-18" in section, "the guide names the newest revision the server speaks"
