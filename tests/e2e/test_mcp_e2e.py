"""MCP stdio handshake against the real server process: initialize, list the
tools, call one. Newline-delimited JSON-RPC, exactly what an MCP client sends."""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo
from name_bytes import NOT_UTF8_NAMES

# The MCP server is a stdio process, and this file tests it as one.
run_cli = cli_runner(spawn=True)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", r)
    git_init_repo(r)
    git_commit_all(r, "init")
    return r


@pytest.fixture()
def inventoried_repo(repo: Path) -> Path:
    """One inventory run, so explain and worklist have a store to answer from."""
    res = run_cli(repo, "inventory")
    assert res.returncode == 0, res.stdout + res.stderr
    return repo


def _rpc(msg_id, method, params=None):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _call(msg_id, name, arguments=None):
    return _rpc(msg_id, "tools/call", {"name": name, "arguments": arguments or {}})


def _serve(repo: Path, requests: list[str]) -> dict:
    proc = run_cli(repo, "mcp", "--repo", str(repo), stdin="\n".join(requests) + "\n")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    return {m["id"]: m for m in map(json.loads, proc.stdout.strip().splitlines())}


def test_initialize_list_and_call(repo: Path):
    responses = _serve(repo, [
        _rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        _rpc(2, "tools/list"),
        _call(3, "check_config"),
    ])

    assert responses[1]["result"]["serverInfo"]["name"] == "crapkit"
    tool_names = {t["name"] for t in responses[2]["result"]["tools"]}
    assert "get_next_item" in tool_names and "check_config" in tool_names
    assert len(responses[2]["result"]["tools"]) == 12
    call = responses[3]["result"]
    assert call["isError"] is False, call
    assert "structuredContent" not in call, "2024-11-05 defines no structuredContent"
    report = json.loads(call["content"][0]["text"])
    assert report["problems"] == [], \
        "doctor answers the JSON report: a passing doctor is an empty problems list"
    assert report["schema"] == 1


def test_a_bad_call_is_a_tool_error_and_the_server_keeps_answering(repo: Path):
    """Five mistakes a coding agent makes, then a good call. Each mistake is a
    tool result in the tool's words (ADR 0001); the good call still answers."""
    replies = _serve(repo, [
        _rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}),
        _call(2, "get_function_brief", {"path": "pylib/mod.py"}),
        _call(3, "list_worklist", {"bogus": 1}),
        _call(4, "list_worklist", {"top": "three"}),
        _call(5, "get_function_brief", {"path": None, "name": "guarded"}),
        _rpc(6, "ping"),
        _call(7, "check_config"),
    ])

    assert set(replies) == {1, 2, 3, 4, 5, 6, 7}, "no mistake ends the session"
    texts = {i: replies[i]["result"]["content"][0]["text"] for i in (2, 3, 4, 5)}
    assert all(replies[i]["result"]["isError"] is True for i in (2, 3, 4, 5)), texts
    assert texts[2] == "get_function_brief needs name (see inputSchema.required)"
    assert texts[3] == "list_worklist does not take 'bogus'; accepted: repo, top, scope"
    assert texts[4] == 'top must be an integer (got "three")'
    assert "usage:" not in texts[4], "the refusal speaks the tool's vocabulary, not argparse's"
    assert texts[5] == "get_function_brief needs path (see inputSchema.required)", \
        "a positional sent as null is refused by the table, not answered by a spawned CLI"
    assert replies[6]["result"] == {}
    assert replies[7]["result"]["isError"] is False


def test_tools_list_names_the_required_arguments(repo: Path):
    replies = _serve(repo, [_rpc(1, "tools/list")])

    schemas = {t["name"]: t["inputSchema"] for t in replies[1]["result"]["tools"]}
    assert schemas["get_function_brief"]["required"] == ["path", "name"]
    assert schemas["get_function_history"]["required"] == ["path", "name"]
    assert "required" not in schemas["list_worklist"]


def test_explain_answers_structured_json_and_history_reaches_the_cli(inventoried_repo: Path):
    replies = _serve(inventoried_repo, [
        _call(1, "get_function_history", {"path": "pylib/mod.py", "name": "guarded"}),
        _call(2, "get_function_history", {"path": "pylib/mod.py", "name": "guarded", "history": True}),
    ])

    plain = replies[1]["result"]
    assert plain["isError"] is False, plain
    assert plain["structuredContent"]["name"] == "guarded"
    assert "commits" not in plain["structuredContent"]["functions"][0]
    with_history = replies[2]["result"]["structuredContent"]["functions"][0]
    assert [c["subject"] for c in with_history["commits"]] == ["init"], \
        "history: true reaches the CLI as --history"


def test_worklist_and_next_item_take_a_scope_array(inventoried_repo: Path):
    """The fixture declares two scopes, src and py; only py admits a row on an
    inventory-only run. get_next_item has no scored run to hand out, so its answer
    is the CLI's own refusal: proof the scope array cleared the table."""
    replies = _serve(inventoried_repo, [
        _call(1, "list_worklist", {"scope": ["py"]}),
        _call(2, "list_worklist", {"scope": ["src"]}),
        _call(3, "get_next_item", {"scope": ["py"]}),
    ])

    py = replies[1]["result"]
    assert py["isError"] is False, py
    assert py["structuredContent"]["active"], "the py scope holds the fixture's one admitted row"
    assert {r["scope"] for r in py["structuredContent"]["active"]} == {"py"}
    src = replies[2]["result"]["structuredContent"]
    assert src["active"] == [], "the src scope admits no row on an inventory-only run"
    nxt = replies[3]["result"]
    assert nxt["isError"] is True, nxt
    assert nxt["content"][0]["text"].startswith("crapkit: no scored run"), \
        "the scope array reached the CLI, which wants a scored run before it hands out a packet"


# --- the gate tool against the real server (0.5.0) -----------------------------

TANGLED = """

def tangled(n):
    if n > 1:
        n = n + 1
    if n > 2:
        n = n + 1
    if n > 3:
        n = n + 1
    if n > 4:
        n = n + 1
    if n > 5:
        n = n + 1
    if n > 6:
        n = n + 1
    if n > 7:
        n = n + 1
    return n
"""


@pytest.fixture()
def scored_repo(repo: Path) -> Path:
    """One coverage run, so rescore has a baseline to overlay."""
    res = run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return repo


def test_gate_answers_the_post_edit_question(scored_repo: Path):
    """A clean edit is `ok: true`; a ccn-8 function added to the working tree
    is `ok: false` with the breach in the payload, and neither is a tool error."""
    mod = scored_repo / "pylib" / "mod.py"
    mod.write_text(mod.read_text(encoding="utf-8").replace("x % 2", "x % 3"), encoding="utf-8")
    clean = _serve(scored_repo, [_call(1, "check_gate", {"path": "pylib/mod.py"})])[1]["result"]
    mod.write_text(mod.read_text(encoding="utf-8") + TANGLED, encoding="utf-8")
    breached = _serve(scored_repo, [_call(2, "check_gate", {"path": "pylib/mod.py"})])[2]["result"]

    assert clean["isError"] is False and clean["structuredContent"]["gate"]["ok"] is True, clean
    assert breached["isError"] is False, breached
    gate = breached["structuredContent"]["gate"]
    assert gate["ok"] is False and [b["function"] for b in gate["breaches"]] == ["tangled( n )"]
    assert gate["breaches"][0]["ceiling"] == 6 and gate["ceilings"] == {"pylib/mod.py": 6}


def test_gate_without_a_scored_run_is_still_a_tool_error(inventoried_repo: Path):
    reply = _serve(inventoried_repo, [_call(1, "check_gate", {"path": "pylib/mod.py"})])[1]["result"]

    assert reply["isError"] is True and "structuredContent" not in reply, reply
    error = json.loads(reply["content"][0]["text"])["error"]
    assert (error["exit"], error["kind"]) == (1, "state")
    assert error["message"].startswith("no scored run"),         "the CLI's --json error object is the text: exit 1 is a refusal, not a verdict"


def test_gate_on_a_missing_file_is_still_a_tool_error(scored_repo: Path):
    """check_gate answers exit 3 as a verdict only with the gate payload; the
    error object of an argument refusal stays a tool error."""
    reply = _serve(scored_repo, [_call(1, "check_gate", {"path": "pylib/gone.py"})])[1]["result"]

    assert reply["isError"] is True and "structuredContent" not in reply, reply
    assert json.loads(reply["content"][0]["text"])["error"]["exit"] == 3


POSIX_NAME = pytest.mark.skipif(
    sys.platform == "win32", reason="needs a POSIX file system that stores any byte in a name")


@POSIX_NAME
@NOT_UTF8_NAMES
def test_gate_on_a_scoped_name_that_is_not_utf8_answers_the_081_verdict(scored_repo: Path):
    """A name a scope takes, which no reader can key: the verdict 0.8.1 gave,
    field for field, isError false."""
    (scored_repo / os.fsdecode(b"pylib/caf\xe9.py")).write_text(TANGLED, encoding="utf-8")

    reply = _serve(scored_repo, [_call(1, "check_gate", {"path": "pylib/caf\udce9.py"})])[1]["result"]

    assert reply["isError"] is False, reply
    content = reply["structuredContent"]
    assert json.loads(reply["content"][0]["text"]) == content
    assert {key: value for key, value in content.items() if not key.startswith("baseline_")} == {
        "functions": [], "schema": 1,
        "note": ("coverage is the baseline run's; complexity is the working tree's. "
                 "Run verify for the real verdict."),
        "gate": {"ok": False, "judged": 0, "ceilings": {}, "breaches": [], "untracked": [],
                 "unread_files": [{"path": "pylib/caf\\xe9.py", "dirty": True,
                                   "reason": ("its name is not UTF-8, and crapkit reads every path as "
                                              "UTF-8: rename it (git mv) to a UTF-8 name")}]}}


@POSIX_NAME
@NOT_UTF8_NAMES
def test_gate_on_a_name_no_scope_takes_judges_nothing_and_passes(scored_repo: Path):
    (scored_repo / "docs").mkdir()
    (scored_repo / os.fsdecode(b"docs/caf\xe9.md")).write_text(TANGLED, encoding="utf-8")

    reply = _serve(scored_repo, [_call(1, "check_gate", {"path": "docs/caf\udce9.md"})])[1]["result"]

    assert reply["isError"] is False, reply
    assert reply["structuredContent"]["gate"] == {"ok": True, "judged": 0, "ceilings": {}, "breaches": [],
                                                  "untracked": [], "unread_files": []}
