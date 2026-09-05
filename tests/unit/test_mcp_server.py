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
        _call(2, "get_function_brief", {"path": "src/a.py"}),
        _rpc(3, "tools/list"),
    ])

    assert set(replies) == {1, 2, 3}, "a refused call must not end the session"
    call = replies[2]["result"]
    assert call["isError"] is True, call
    assert call["content"][0]["text"] == "get_function_brief needs name (see inputSchema.required)"
    assert "structuredContent" not in call
    assert replies[3]["result"]["tools"], "the request after the refusal is answered"


def test_params_null_arguments_null_and_a_null_positional_are_refusals(monkeypatch,
                                                                         tmp_path):
    """Three shapes of null, none of them a crash and none of them a spawn. The
    served schema says a positional is a required string, so a positional sent
    as null is the same refusal as one left out."""
    _no_cli(monkeypatch)
    replies = _serve(monkeypatch, tmp_path, [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": None}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "get_function_brief", "arguments": None}}),
        _rpc(3, "ping"),
        _call(4, "get_function_brief", {"path": None, "name": "f"}),
    ])

    assert set(replies) == {1, 2, 3, 4}
    assert replies[1]["result"]["isError"] is True
    assert "unknown tool ''" in replies[1]["result"]["content"][0]["text"]
    assert replies[2]["result"]["content"][0]["text"] == \
        "get_function_brief needs path (see inputSchema.required)"
    assert replies[3]["result"] == {}
    null_positional = replies[4]["result"]
    assert null_positional["isError"] is True, null_positional
    assert null_positional["content"][0]["text"] == \
        "get_function_brief needs path (see inputSchema.required)"


def test_an_undeclared_key_names_the_accepted_ones(monkeypatch, tmp_path):
    _no_cli(monkeypatch)
    replies = _serve(monkeypatch, tmp_path, [_call(1, "list_worklist", {"bogus": 1})])

    call = replies[1]["result"]
    assert call["isError"] is True
    assert call["content"][0]["text"] == \
        "list_worklist does not take 'bogus'; accepted: repo, top, scope"


@pytest.mark.parametrize("tool, arguments, sentence", [
    ("list_worklist", {"top": "three"}, 'top must be an integer (got "three")'),
    ("get_next_item", {"top": True}, "top must be an integer (got true)"),
    ("get_next_item", {"exclude": "cli.py"}, 'exclude must be an array of strings (got "cli.py")'),
    ("list_coupled_files", {"min_confidence": "high"}, 'min_confidence must be a number (got "high")'),
    ("get_function_history", {"path": "a.py", "name": "f", "history": "yes"},
     'history must be a boolean (got "yes")'),
    ("get_function_brief", {"path": 7, "name": "f"}, "path must be a string (got 7)"),
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
        _call(1, "list_runs", {}),
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

    assert by_name["get_function_brief"]["required"] == ["path", "name"]
    assert by_name["get_function_history"]["required"] == ["path", "name"]
    for name in ("list_worklist", "get_next_item", "list_runs", "check_config", "list_coupled_files",
                 "list_duplicate_functions", "get_ratchet_report"):
        assert "required" not in by_name[name], f"{name} has no positional to require"


def test_explain_and_doctor_shell_to_json_like_the_other_tools():
    explain = next(t for t in TOOLS if t["name"] == "get_function_history")
    doctor = next(t for t in TOOLS if t["name"] == "check_config")

    assert build_argv(explain, {"path": "a.py", "name": "f"}) == ["explain", "a.py", "f", "--json"]
    assert build_argv(doctor, {}) == ["doctor", "--json"]


def test_explain_history_and_tests_are_bare_flags_only_when_true():
    explain = next(t for t in TOOLS if t["name"] == "get_function_history")
    props = next(t for t in tool_listing() if t["name"] == "get_function_history")["inputSchema"]["properties"]

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
    same depth; agent-json.md has other `explain` rows outside it (the CLI section)."""
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
    explain = _row(section, "`get_function_history`")
    assert "JSON" in explain and "`history`" in explain and "`tests`" in explain, explain
    assert "JSON" in _row(section, "`check_config`")
    for tool in ("`list_worklist`", "`get_next_item`"):
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


# --- the tenth tool: gate (0.5.0) ---------------------------------------------
#
# After an edit no MCP tool answered "does my change clear the ceiling": brief
# only returned the shell string `crapkit rescore PATH --gate`. `gate` maps to
# `rescore PATH --gate --json`; exit 6 is a verdict, not a failure, so it comes
# back as a result whose `gate.ok` is false, while 3, 4 and 5 stay tool errors.

def _cli_answers(monkeypatch, returncode: int, stdout: str, stderr: str = "") -> list:
    """The CLI the server would spawn, answered without a process; every argv
    the server built is kept for the assertions."""
    import subprocess

    calls: list = []

    def _run(argv, **_kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(mcp_server.subprocess, "run", _run)
    return calls


def test_gate_is_listed_with_a_required_path_and_the_read_only_annotations():
    (gate,) = [t for t in tool_listing() if t["name"] == "check_gate"]

    assert gate["inputSchema"]["required"] == ["path"]
    assert gate["annotations"]["readOnlyHint"] is True
    assert "hook" in gate["description"] and "ceiling" in gate["description"]


def test_gate_maps_to_rescore_path_gate_json():
    tool = next(t for t in TOOLS if t["name"] == "check_gate")

    assert build_argv(tool, {"path": "src/a.py"}) == ["rescore", "--gate", "src/a.py", "--json"]


def test_a_breach_is_a_result_with_ok_false_not_a_tool_error(monkeypatch, tmp_path):
    verdict = json.dumps({"baseline_run": 1, "functions": [],
                          "gate": {"ok": False, "judged": 1, "breaches": [{"path": "src/a.py"}]},
                          "schema": 1})
    calls = _cli_answers(monkeypatch, 6, verdict, "  GATE  crap 8.0 ...")
    replies = _serve(monkeypatch, tmp_path, [_call(1, "check_gate", {"path": "src/a.py"})])

    call = replies[1]["result"]
    assert call["isError"] is False, call
    assert call["structuredContent"]["gate"]["ok"] is False
    assert call["structuredContent"]["gate"]["breaches"] == [{"path": "src/a.py"}]
    assert calls[0][3:7] == ["rescore", "--gate", "src/a.py", "--json"], calls


def test_a_clean_edit_is_a_result_with_ok_true(monkeypatch, tmp_path):
    _cli_answers(monkeypatch, 0, json.dumps({"gate": {"ok": True, "judged": 1, "breaches": []},
                                             "schema": 1}))
    replies = _serve(monkeypatch, tmp_path, [_call(1, "check_gate", {"path": "src/a.py"})])

    call = replies[1]["result"]
    assert call["isError"] is False and call["structuredContent"]["gate"]["ok"] is True


@pytest.mark.parametrize("exit_code, line", [
    (1, "crapkit: no scored run in /repo - run `crapkit coverage` first"),
    (3, "crapkit: no crapkit.toml at /repo"),
    (4, "crapkit: git failed"),
    (5, "crapkit: lizard is not installed"),
])
def test_the_other_exits_stay_tool_errors(monkeypatch, tmp_path, exit_code, line):
    _cli_answers(monkeypatch, exit_code, "", line)
    replies = _serve(monkeypatch, tmp_path, [_call(1, "check_gate", {"path": "src/a.py"})])

    call = replies[1]["result"]
    assert call["isError"] is True, call
    assert call["content"][0]["text"] == line
    assert "structuredContent" not in call


def test_exit_six_is_a_verdict_only_for_the_gate_tool(monkeypatch, tmp_path):
    """No other tool exits 6; if one ever did, that is still a failure."""
    _cli_answers(monkeypatch, 6, json.dumps({"runs": [], "schema": 1}))
    replies = _serve(monkeypatch, tmp_path, [_call(1, "list_runs", {})])

    assert replies[1]["result"]["isError"] is True


def test_gate_needs_a_path_and_takes_nothing_else(monkeypatch, tmp_path):
    _no_cli(monkeypatch)
    replies = _serve(monkeypatch, tmp_path, [_call(1, "check_gate", {}),
                                             _call(2, "check_gate", {"path": "a.py", "top": 3})])

    assert replies[1]["result"]["content"][0]["text"] == "check_gate needs path (see inputSchema.required)"
    assert replies[2]["result"]["content"][0]["text"] == \
        "check_gate does not take 'top'; accepted: repo, path"


def test_the_instructions_count_twelve_tools_and_name_the_gate():
    assert "twelve tools" in mcp_server._INSTRUCTIONS and "ten tools" not in mcp_server._INSTRUCTIONS
    assert "check_gate" in mcp_server._INSTRUCTIONS


@pytest.mark.parametrize("page", sorted(_MCP_SECTION))
def test_both_pages_list_the_gate_tool_beside_the_other_eleven(page):
    section = _mcp_section(page)

    gate = _row(section, "`check_gate`")
    assert "`path`" in gate and "rescore" in gate and "`ok`" in gate, gate
    assert "nine" not in section.lower(), f"{page} still counts nine tools"


# --- the repo a call names is walked upward (ADR 0002) ------------------------

def test_a_repo_below_the_root_spawns_the_cli_on_the_root_above_it(monkeypatch, tmp_path):
    """A client started in a monorepo workspace, or a call naming one, serves
    the root configuration that claims the workspace: the CLI is spawned with
    `--repo` at that root, never at the workspace."""
    (tmp_path / "web" / "src").mkdir(parents=True)
    calls = _cli_answers(monkeypatch, 0, json.dumps({"runs": [], "schema": 1}))
    replies = _serve(monkeypatch, tmp_path,
                     [_call(1, "list_runs", {"repo": str(tmp_path / "web" / "src")})])

    assert replies[1]["result"]["isError"] is False
    assert calls[0][-2:] == ["--repo", str(tmp_path)], calls


def test_a_repo_naming_no_directory_gets_the_no_config_answer_and_spawns_nothing(monkeypatch,
                                                                                 tmp_path):
    """A mistyped `repo` is not adopted by the configuration above it: the
    answer names the directory that is not there, and no CLI runs."""
    _no_cli(monkeypatch)
    missing = tmp_path / "web" / "nope"
    replies = _serve(monkeypatch, tmp_path, [_call(1, "list_runs", {"repo": str(missing)})])

    call = replies[1]["result"]
    assert call["isError"] is True, call
    assert call["content"][0]["text"].startswith(
        f"no crapkit.toml in {missing} - nothing measured here."), call
