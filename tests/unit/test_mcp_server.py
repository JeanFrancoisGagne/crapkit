"""The serve loop in-process: a request line in, a reply line out, no subprocess.

sys.stdin and sys.stdout are swapped for a StringIO pair, so these tests read
exactly the frames a client reads. What they pin is the contract ADR 0001
records: a bad tools/call (missing positional, undeclared key, wrong type) is a
tool result with isError true, written in the tool's vocabulary, and the session
continues; JSON-RPC errors stay reserved for the protocol itself (unknown
method, params that are not an object, an exception escaping the server).
Nothing here spawns the CLI: every
refusal is decided before build_argv runs, which is the point.
"""
import io
import json
import queue
import sys
from pathlib import Path

import pytest

from crapkit import mcp_server
from crapkit.mcp_server import TOOLS, build_argv, serve, tool_listing
from hang_guard import HANG_SECONDS


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
    source, out = _live_frames(lines)
    monkeypatch.setattr(sys, "stdin", source)
    monkeypatch.setattr(sys, "stdout", out)
    rc = serve(tmp_path)
    assert rc == 0
    return {m["id"]: m for m in map(json.loads, out.getvalue().strip().splitlines())}


def _live_frames(lines):
    """Keep client stdin open until each request's reply has been read."""
    incoming = queue.Queue()
    requests = 0
    for line in lines:
        incoming.put(line + '\n')
        message = mcp_server._parse(line)
        requests += int(message is not None and 'id' in message)
    if not requests:
        incoming.put('')

    class Input:
        def readline(self):
            return incoming.get(timeout=HANG_SECONDS)

    class Output(io.StringIO):
        def write(self, text):
            nonlocal requests
            result = super().write(text)
            requests -= 1
            if requests == 0:
                incoming.put('')
            return result

    return Input(), Output()


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
    ("list_runs", {"repo": 5}, "repo must be a string (got 5)"),
    ("list_runs", {"repo": ["a"]}, 'repo must be a string (got ["a"])'),
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


@pytest.mark.parametrize("method, params", [
    ("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "dispatch-test", "version": "1"}}),
    ("tools/list", {}),
])
def test_a_synchronous_dispatch_error_replies_once_and_keeps_the_session(monkeypatch,
                                                                         tmp_path, method, params):
    _no_cli(monkeypatch)
    calls = []

    def _dispatch_fault(arguments):
        calls.append(arguments)
        raise RuntimeError("dispatch-fault")

    monkeypatch.setitem(mcp_server._METHODS, method, _dispatch_fault)
    notification = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
    replies = _serve(monkeypatch, tmp_path, [
        notification,
        _rpc(1, method, params),
        _rpc(2, "ping"),
    ])

    assert set(replies) == {1, 2}, "notifications stay silent"
    assert calls == [params], "notifications must not invoke the handler"
    assert replies[1]["error"]["code"] == -32603
    assert replies[1]["error"]["message"] == "RuntimeError: dispatch-fault"
    assert replies[2]["result"] == {}


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

    monkeypatch.setattr(mcp_server, "run_owned", _run)
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


UNREAD_NAME = {"path": "src/caf\\xe9.py", "reason": "its name is not UTF-8, and crapkit reads every "
               "path as UTF-8: rename it (git mv) to a UTF-8 name"}
NAME_REFUSAL = json.dumps({"error": {"exit": 3, "kind": "config", "unread_files": [UNREAD_NAME],
                                     "message": "src/caf\\xe9.py is named in bytes that are not UTF-8"},
                           "schema": 1})


def test_a_name_the_gate_refused_as_not_utf8_is_a_failed_verdict(monkeypatch, tmp_path):
    """rescore --gate exits 3 on such a name, as every CLI gate does; check_gate
    speaks MCP and returns that refusal in its verdict as the unread finding,
    so an agent reads a gate that failed, not a tool that broke. Each entry
    takes the shape rescore --gate --json lists, `dirty` included."""
    _cli_answers(monkeypatch, 3, NAME_REFUSAL, "crapkit: src/caf\\xe9.py is named ...")
    replies = _serve(monkeypatch, tmp_path, [_call(1, "check_gate", {"path": "src/caf\udce9.py"})])

    call = replies[1]["result"]
    assert call["isError"] is False, call
    assert call["structuredContent"] == {"functions": [], "schema": 1, "gate": {
        "ok": False, "judged": 0, "ceilings": {}, "breaches": [], "untracked": [],
        "unread_files": [{**UNREAD_NAME, "dirty": True}]}}
    assert json.loads(call["content"][0]["text"]) == call["structuredContent"]


@pytest.mark.parametrize("tool, arguments, stdout", [
    ("get_function_brief", {"path": "src/caf\udce9.py", "name": "f"}, NAME_REFUSAL),
    ("check_gate", {"path": "src/a.py"},
     json.dumps({"error": {"exit": 3, "kind": "config", "message": "no crapkit.toml"}, "schema": 1})),
    ("check_gate", {"path": "src/a.py"}, "not json"),
], ids=["brief-has-no-verdict", "gate-refusal-naming-no-file", "gate-refusal-not-json"])
def test_every_other_refusal_stays_a_tool_error(monkeypatch, tmp_path, tool, arguments, stdout):
    _cli_answers(monkeypatch, 3, stdout, "crapkit: refused")
    replies = _serve(monkeypatch, tmp_path, [_call(1, tool, arguments)])

    call = replies[1]["result"]
    assert call["isError"] is True, call
    assert call["content"][0]["text"] == stdout
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


def test_initialize_explains_the_tools_side_effect_boundary(monkeypatch, tmp_path):
    replies = _serve(monkeypatch, tmp_path, [_rpc(1, "initialize", {})])
    instructions = replies[1]["result"]["instructions"]

    assert "without running test suites or editing source files" in instructions
    assert "write caches, initialize or migrate the snapshot store, and fill rollups" in instructions
    assert "get_next_item takes no claim" in instructions
    assert "check_gate runs rescore and records no verification run" in instructions
    assert "writes to the repo" not in instructions


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


# --- params and arguments that are not objects --------------------------------
#
# `x or {}` stood in for null and empty only. A list, a string or a number in
# `params` or `arguments` reached `.get` and came back as -32603 carrying an
# AttributeError. By-position values are the likeliest of these: JSON-RPC
# allows them, MCP does not. `arguments` belong to a named tool, so ADR 0001's
# tool result answers them in the tool's words. `params` that are not an object
# name no tool, so tools/call and initialize answer JSON-RPC -32602 (invalid
# params) with no result, and the session reads on.

# The refusal names the JSON type, as the -32602 answer for params does, and
# never echoes the value: a whole by-position list, or a long string, is not
# what the agent needs to read to correct its call.
@pytest.mark.parametrize("tool, arguments, sentence", [
    ("list_runs", 3, "arguments must be an object (got a number)"),
    ("list_runs", 1.5, "arguments must be an object (got a number)"),
    ("list_worklist", "top=3", "arguments must be an object (got a string)"),
    ("list_worklist", True, "arguments must be an object (got a boolean)"),
    ("list_runs", [1], "arguments must be an object (got an array)"),
    ("get_function_brief", ["a.py", "f"], "arguments must be an object (got an array)"),
    ("list_runs", "", "arguments must be an object (got a string)"),
    ("list_runs", 0, "arguments must be an object (got a number)"),
    ("list_runs", False, "arguments must be an object (got a boolean)"),
    ("list_runs", [], "arguments must be an object (got an array)"),
], ids=["a-number", "a-float", "a-string", "true", "a-list-on-a-tool-without-positionals",
        "positionals-by-position", "an-empty-string", "zero", "false", "an-empty-list"])
def test_arguments_that_are_not_an_object_are_refused_in_the_tools_words(
        monkeypatch, tmp_path, tool, arguments, sentence):
    _no_cli(monkeypatch)
    replies = _serve(monkeypatch, tmp_path, [_call(1, tool, arguments), _rpc(2, "ping")])

    call = replies[1]["result"]
    assert call["isError"] is True, replies[1]
    assert call["content"][0]["text"] == sentence
    assert replies[2]["result"] == {}


# Only absent, null and {} mean no arguments. An empty string, 0, false and []
# are values of the wrong type, refused above: read as none given, list_runs
# ran its CLI on them.
@pytest.mark.parametrize("arguments", ["omitted", None, {}],
                         ids=["absent", "null", "an-empty-object"])
def test_no_arguments_in_any_empty_shape_reads_as_none_given(monkeypatch, tmp_path, arguments):
    _no_cli(monkeypatch)
    replies = _serve(monkeypatch, tmp_path, [_call(1, "get_function_brief", arguments)])

    assert replies[1]["result"]["content"][0]["text"] == \
        "get_function_brief needs path (see inputSchema.required)"


# Every JSON type that is not an object, and the words the -32602 message uses
# for it. An empty array, an empty string, zero and false are not null: each
# is a value of the wrong type, not a missing one.
_NOT_OBJECTS = [(["get_function_brief", {"path": "a.py", "name": "f"}], "an array"),
                ([], "an array"), ("get_function_brief", "a string"), ("", "a string"),
                (7, "a number"), (0, "a number"), (1.5, "a number"), (False, "a boolean")]
_NOT_OBJECT_IDS = ["a-list-by-position", "an-empty-list", "a-string", "an-empty-string",
                   "a-number", "zero", "a-float", "false"]


def _invalid_params(msg_id, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": message}}


@pytest.mark.parametrize("params, got", _NOT_OBJECTS, ids=_NOT_OBJECT_IDS)
def test_tools_call_params_that_are_not_an_object_are_a_refusal(monkeypatch, tmp_path,
                                                               params, got):
    _no_cli(monkeypatch)
    replies = _serve(monkeypatch, tmp_path, [_rpc(1, "tools/call", params), _rpc(2, "ping")])

    assert replies[1] == _invalid_params(
        1, f"params must be an object naming the tool and its arguments (got {got})")
    assert replies[2]["result"] == {}


@pytest.mark.parametrize("params", ["omitted", None, {}],
                         ids=["params-absent", "params-null", "params-empty"])
def test_tools_call_with_no_params_names_no_tool(monkeypatch, tmp_path, params):
    _no_cli(monkeypatch)
    replies = _serve(monkeypatch, tmp_path, [_rpc(1, "tools/call", params)])

    call = replies[1]["result"]
    assert call["isError"] is True, replies[1]
    assert call["content"][0]["text"] == "unknown tool ''"


@pytest.mark.parametrize("params, got", [(["2025-06-18"], "an array"),
                                         ("2025-06-18", "a string"), (5, "a number"),
                                         ([], "an array"), (False, "a boolean")],
                         ids=["params-a-list", "params-a-string", "params-a-number",
                              "params-an-empty-list", "params-false"])
def test_initialize_params_that_are_not_an_object_are_invalid_params(monkeypatch, tmp_path,
                                                                    params, got):
    replies = _serve(monkeypatch, tmp_path, [_rpc(1, "initialize", params), _rpc(2, "ping")])

    assert replies[1] == _invalid_params(
        1, f"params must be an object carrying protocolVersion (got {got})")
    assert replies[2]["result"] == {}


@pytest.mark.parametrize("params", [[1], "x", 3], ids=["a-list", "a-string", "a-number"])
def test_a_method_that_reads_no_params_answers_whatever_type_they_are(monkeypatch, tmp_path,
                                                                     params):
    replies = _serve(monkeypatch, tmp_path, [_rpc(1, "ping", params),
                                             _rpc(2, "tools/list", params),
                                             _rpc(3, "resources/list", params)])

    assert replies[1]["result"] == {}
    assert [t["name"] for t in replies[2]["result"]["tools"]] == [t["name"] for t in TOOLS]
    assert replies[3]["error"] == {"code": -32601, "message": "unknown method 'resources/list'"}


@pytest.mark.parametrize("name, shown", [(None, "None"), (5, "5"), (["list_runs"], "['list_runs']"),
                                         ("", "''")],
                         ids=["null", "a-number", "a-list", "empty"])
def test_a_tool_name_of_any_other_type_is_an_unknown_tool(monkeypatch, tmp_path, name, shown):
    _no_cli(monkeypatch)
    replies = _serve(monkeypatch, tmp_path, [_rpc(1, "tools/call", {"name": name})])

    assert replies[1]["result"]["content"][0]["text"] == f"unknown tool {shown}"


@pytest.mark.parametrize("params", ["omitted", None, {},
                                    {"protocolVersion": 20250618},
                                    {"protocolVersion": ["2025-06-18"]},
                                    {"protocolVersion": {"v": "2025-06-18"}}],
                         ids=["params-absent", "params-null", "params-empty",
                              "version-a-number", "version-a-list", "version-an-object"])
def test_initialize_answers_the_newest_revision_when_it_cannot_read_one(monkeypatch, tmp_path,
                                                                        params):
    replies = _serve(monkeypatch, tmp_path, [_rpc(1, "initialize", params)])

    assert replies[1]["result"]["protocolVersion"] == mcp_server.SUPPORTED_PROTOCOLS[0]


@pytest.mark.parametrize("method, shown", [(None, "None"), ("omitted", "''"),
                                           (["ping"], "['ping']"), (7, "7")],
                         ids=["null", "absent", "a-list", "a-number"])
def test_a_method_that_is_not_a_known_string_is_an_unknown_method(monkeypatch, tmp_path,
                                                                  method, shown):
    msg = {"jsonrpc": "2.0", "id": 1}
    if method != "omitted":
        msg["method"] = method
    replies = _serve(monkeypatch, tmp_path, [json.dumps(msg), _rpc(2, "ping")])

    assert replies[1]["error"] == {"code": -32601, "message": f"unknown method {shown}"}
    assert replies[2]["result"] == {}


@pytest.mark.parametrize("arguments, argv", [
    ({"path": "src/café.py", "name": "naïve_世界"}, ["src/café.py", "naïve_世界"]),
    ({"top": 10 ** 30}, ["--top", str(10 ** 30)]),
    ({"repo": ""}, []),
], ids=["non-ascii-name", "top-10-to-the-30", "repo-empty"])
def test_values_at_the_edge_of_their_type_reach_the_cli_as_given(monkeypatch, tmp_path,
                                                                 arguments, argv):
    calls = _cli_answers(monkeypatch, 0, json.dumps({"schema": 1}))
    tool = "get_function_brief" if "path" in arguments else "list_worklist"
    replies = _serve(monkeypatch, tmp_path, [_call(1, tool, arguments)])

    assert replies[1]["result"]["isError"] is False, replies[1]
    assert all(word in calls[0] for word in argv), calls
    assert calls[0][-2:] == ["--repo", str(tmp_path)]
