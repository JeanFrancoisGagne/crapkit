"""A tool answer fits the tool result a client keeps whole.

Cline keeps 8,000 characters of a tool result, counted over its own JSON
serialization of the whole result, and cuts the middle out. A brief on a
300-line function was 15 to 27 KB of text, and a 2024-11-05 client such as
Cline got a structuredContent copy beside it, a field its revision does not
define, which doubled the size. The model received JSON cut in the middle that
it could not parse, for get_function_brief and for list_worklist top 50 alike.

Every JSON answer is now held to ANSWER_CHARS, counted as the text a client
embeds. An answer over it loses the end of its largest list or string fields
first, and carries `truncated`: each cut field with what it kept and what it
had, and the CLI command that prints the whole answer. structuredContent goes
only to a client whose revision defines it.
"""
import json
import subprocess
from pathlib import Path

import pytest

from crapkit import mcp_server
from crapkit.mcp_server import ANSWER_CHARS, TOOLS, tool_listing
from crapkit.packet import console_command

_CONFIG = '[crapkit]\ntarget = 6\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'


def _measured(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "crapkit.toml").write_text(_CONFIG, encoding="utf-8")
    return path


def _row(n: int) -> dict:
    return {"authors": 1, "ccn": 7, "ccn_std": 7, "commits": 1, "cov": 0.0, "crap": 56.0,
            "end": 318 + 16 * n, "flag": "measured", "function": f"f{n}( a , b )", "handle": f"f{n}",
            "nloc": 14, "occurrence": 1, "path": "calc/big.py", "ratchet_mark": None,
            "remedy": "decompose", "risk": 3.5, "scope": "calc", "start": 305 + 16 * n, "weight": 0.5}


def _worklist() -> dict:
    return {"active": [_row(n) for n in range(50)], "active_total": 61, "churn_window_months": 2,
            "commit": "a" * 40, "dormant_count": 0, "dormant_top": [], "floor": 1, "run_id": 1,
            "schema": 1, "stale": False}


def _big_source() -> str:
    body = "".join(f"    if x == {n}:\n        return {n * 3}\n" for n in range(150))
    return f"def big(x):\n{body}    return -1\n"


def _brief() -> dict:
    siblings = [{"ccn": 7, "crap": 56.0, "end": 318 + 16 * n, "function": f"f{n}( a , b )",
                 "occurrence": 1, "remedy": "decompose", "start": 305 + 16 * n} for n in range(60)]
    return {"attempts": [], "ccn": 151, "commands": {"gate": "crapkit rescore calc/big.py --gate"},
            "end": 302, "file_functions": siblings, "function": "big( x )", "path": "calc/big.py",
            "schema": 1, "source": _big_source(), "start": 1,
            "uncovered_lines": list(range(3, 302, 2))}


def _answer(monkeypatch, tmp_path: Path, name: str, arguments: dict, payload: dict) -> dict:
    stdout = json.dumps(payload, sort_keys=True) + "\n"
    monkeypatch.setattr(mcp_server, "run_owned",
                        lambda argv, **_: subprocess.CompletedProcess(argv, 0, stdout, ""))
    return mcp_server._call_tool(_measured(tmp_path / "repo"), name, arguments)


def _embedded(result: dict) -> int:
    """The characters the text takes inside a client's JSON of the result."""
    return len(json.dumps(result["content"][0]["text"], ensure_ascii=False))


def _cline(result: dict) -> int:
    """Cline's serialization of the whole result, which it cuts at 8,000."""
    return len(json.dumps(result, separators=(",", ":"), ensure_ascii=False))


def test_a_worklist_of_fifty_keeps_the_top_rows_that_fit_and_says_how_many(monkeypatch, tmp_path):
    payload = _worklist()

    result = _answer(monkeypatch, tmp_path, "list_worklist", {"top": 50}, payload)

    answer = json.loads(result["content"][0]["text"])
    kept = answer["truncated"]["fields"]["active"]
    assert _embedded(result) <= ANSWER_CHARS
    assert answer["active"] == payload["active"][:kept["kept"]]
    assert kept == {"kept": len(answer["active"]), "of": 50}
    assert answer["truncated"]["full"].endswith(
        f'worklist "--top=50" "--repo={(tmp_path / "repo").resolve()}" --json')
    assert answer["active_total"] == 61
    assert result["structuredContent"] == answer


def _at(payload: dict, dotted: str):
    for key in dotted.split("."):
        payload = payload[key]
    return payload


def _start(node, count: int):
    return dict(list(node.items())[:count]) if isinstance(node, dict) else node[:count]


def _assert_cut_to_prefixes(answer: dict, payload: dict) -> dict:
    """Each field `truncated` names, by its path of keys, is the start of the
    whole answer's field, and says how much of it the answer kept."""
    cut = answer["truncated"]["fields"]
    for field, counts in cut.items():
        kept, whole = _at(answer, field), _at(payload, field)
        assert kept == _start(whole, counts["kept"]), field
        assert counts == {"kept": len(kept), "of": len(whole)}, field
    return cut


def test_a_brief_on_a_300_line_function_cuts_its_siblings_before_its_source(monkeypatch, tmp_path):
    payload = _brief()

    result = _answer(monkeypatch, tmp_path, "get_function_brief",
                     {"path": "calc/big.py", "name": "big"}, payload)

    answer = json.loads(result["content"][0]["text"])
    assert _embedded(result) <= ANSWER_CHARS
    assert set(_assert_cut_to_prefixes(answer, payload)) == {"file_functions"}
    assert answer["source"] == payload["source"]
    assert answer["function"] == "big( x )" and answer["start"] == 1 and answer["end"] == 302


def test_a_source_longer_than_the_budget_keeps_its_first_lines(monkeypatch, tmp_path):
    """Lists go first, so the siblings and the uncovered lines are cut before
    the source, which is what an edit is made from."""
    payload = {**_brief(), "source": _big_source() * 3}

    result = _answer(monkeypatch, tmp_path, "get_function_brief",
                     {"path": "calc/big.py", "name": "big"}, payload)

    answer = json.loads(result["content"][0]["text"])
    assert _embedded(result) <= ANSWER_CHARS
    cut = _assert_cut_to_prefixes(answer, payload)
    assert set(cut) == {"file_functions", "uncovered_lines", "source"}
    assert answer["source"].startswith("def big(x):\n    if x == 0:")
    assert answer["function"] == "big( x )" and answer["start"] == 1 and answer["end"] == 302


def test_an_answer_under_the_budget_is_the_cli_text_itself(monkeypatch, tmp_path):
    payload = {"runs": [], "schema": 1}

    result = _answer(monkeypatch, tmp_path, "list_runs", {}, payload)

    assert result["content"][0]["text"] == json.dumps(payload, sort_keys=True) + "\n"
    assert "truncated" not in result["structuredContent"]


@pytest.mark.parametrize("tool, arguments, payload", [
    ("list_worklist", {"top": 50}, _worklist()),
    ("get_function_brief", {"path": "calc/big.py", "name": "big"}, _brief()),
])
def test_a_2024_11_05_client_gets_the_text_alone_and_cline_keeps_it_whole(monkeypatch, tmp_path,
                                                                          tool, arguments, payload):
    stdout = json.dumps(payload, sort_keys=True) + "\n"
    monkeypatch.setattr(mcp_server, "run_owned",
                        lambda argv, **_: subprocess.CompletedProcess(argv, 0, stdout, ""))
    session = mcp_server._Session(_measured(tmp_path / "repo"))
    mcp_server._initialize_result({"protocolVersion": "2024-11-05", "capabilities": {}}, session)

    result = session.run_cli(mcp_server._tool_named(tool), arguments, str(tmp_path / "repo"))

    assert "structuredContent" not in result
    assert _cline(result) <= 8000


def test_a_2025_06_18_client_keeps_structured_content(monkeypatch, tmp_path):
    stdout = json.dumps({"runs": [], "schema": 1}) + "\n"
    monkeypatch.setattr(mcp_server, "run_owned",
                        lambda argv, **_: subprocess.CompletedProcess(argv, 0, stdout, ""))
    session = mcp_server._Session(_measured(tmp_path / "repo"))
    mcp_server._initialize_result({"protocolVersion": "2025-06-18", "capabilities": {}}, session)

    result = session.run_cli(mcp_server._tool_named("list_runs"), {}, str(tmp_path / "repo"))

    assert result["structuredContent"] == {"runs": [], "schema": 1}


def _listed_to(offered: str, tmp_path) -> list[dict]:
    """What tools/list answers a session that offered `offered` at initialize."""
    session = mcp_server._Session(tmp_path)
    mcp_server._initialize_result({"protocolVersion": offered, "capabilities": {}}, session)
    return mcp_server._METHODS["tools/list"]({}, session)["tools"]


@pytest.mark.parametrize("offered", ["2024-11-05", "2025-03-26"])
def test_a_client_before_2025_06_18_is_listed_no_output_schema(tmp_path, offered):
    """The TypeScript SDK 1.12 offers 2025-03-26 and fails a call to a listed
    outputSchema with "has an output schema but did not return structured content"."""
    listed = _listed_to(offered, tmp_path)

    assert len(listed) == len(TOOLS)
    assert [entry["name"] for entry in listed if "outputSchema" in entry] == []


def test_a_2025_06_18_client_is_listed_every_output_schema(tmp_path):
    listed = _listed_to("2025-06-18", tmp_path)

    assert listed == tool_listing()
    assert [entry["name"] for entry in listed if "outputSchema" in entry]


def test_an_error_text_is_never_cut(monkeypatch, tmp_path):
    long = "x" * (3 * ANSWER_CHARS)
    monkeypatch.setattr(mcp_server, "run_owned",
                        lambda argv, **_: subprocess.CompletedProcess(argv, 1, "", long))

    result = mcp_server._call_tool(_measured(tmp_path / "repo"), "list_runs", {})

    assert result == {"content": [{"type": "text", "text": long}], "isError": True}


def test_every_output_schema_declares_truncated():
    for entry in tool_listing():
        truncated = entry["outputSchema"]["properties"]["truncated"]
        assert set(truncated["properties"]) == {"fields", "full"}, entry["name"]


def test_every_tool_names_the_cli_command_for_the_whole_answer(tmp_path):
    for tool in TOOLS:
        arguments = {key: "a.py" if key == "path" else "f" for key in tool["positional"]}
        full = mcp_server._full_command(tool, arguments, str(tmp_path))
        assert full == console_command(mcp_server.build_argv(tool, arguments, str(tmp_path))), full


def test_the_pages_state_the_budget_the_server_holds():
    root = Path(__file__).resolve().parents[2]
    page = " ".join((root / "docs" / "agent-json.md").read_text(encoding="utf-8").split())
    agents = " ".join((root / "AGENTS.md").read_text(encoding="utf-8").split())

    assert f"One answer is {ANSWER_CHARS:,} characters or shorter" in page
    assert f"shorter than {mcp_server._CUTTABLE_CHARS} characters" in page
    assert f"An answer over {ANSWER_CHARS:,} characters" in agents


def test_a_failing_doctor_report_is_cut_to_the_budget_and_stays_an_error(monkeypatch, tmp_path):
    """check_config answers doctor's JSON report even when doctor exits 1 on a
    FAIL. A repo with 40 lanes that cannot start printed 18 KB of it."""
    report = {"problems": [f"lane 'l{n}': sh cannot run 'nosuchtool{n}' (exit 127) - the lane cannot "
                           "start, so its scopes can only ever score no-lane" for n in range(80)],
              "schema": 1, "warnings": []}
    stdout = json.dumps(report, sort_keys=True) + "\n"
    monkeypatch.setattr(mcp_server, "run_owned",
                        lambda argv, **_: subprocess.CompletedProcess(argv, 1, stdout, ""))

    result = mcp_server._call_tool(_measured(tmp_path / "repo"), "check_config", {})

    assert result["isError"] is True and "structuredContent" not in result
    assert _embedded(result) <= ANSWER_CHARS
    answer = json.loads(result["content"][0]["text"])
    assert _assert_cut_to_prefixes(answer, report) == {
        "problems": {"kept": len(answer["problems"]), "of": 80}}
    assert answer["truncated"]["full"].startswith('crapkit doctor "--repo=')


# --- fields below the top level ----------------------------------------------------

def _gate(files: int = 1) -> dict:
    """`rescore calc/big.py --gate --json` on a file with 60 raised functions:
    the breaches sit inside `gate`, and `ceilings` has one entry per file."""
    functions = [{"ccn": 9, "cov": 0.0, "crap": 90.0, "end": 318 + 20 * n, "flag": "measured",
                  "function": f"f{n}( a , b )", "occurrence": 1, "path": "calc/big.py",
                  "remedy": "decompose", "scope": "calc", "stale_coverage": True,
                  "start": 305 + 20 * n} for n in range(61)]
    breaches = [{"ccn": 9, "ceiling": 6, "cov": 0.0, "crap": 90.0, "function": f"f{n}( a , b )",
                 "key_name": f"f{n}( a , b )", "path": "calc/big.py", "remedy": "decompose",
                 "start": 305 + 20 * n} for n in range(60)]
    ceilings = {f"src/pkg/module_{n:04}.py": 6 for n in range(files)}
    return {"baseline_commit": "c" * 40, "baseline_run": 1, "functions": functions,
            "gate": {"breaches": breaches, "ceilings": ceilings, "judged": 61, "ok": False,
                     "untracked": []},
            "note": "the gate judges the working tree against HEAD", "schema": 1}


def _cline_answer(monkeypatch, tmp_path: Path, name: str, arguments: dict, payload: dict) -> dict:
    """The result a 2024-11-05 client such as Cline gets; check_gate's CLI
    exits 6 on a breach, the others 0."""
    stdout, code = json.dumps(payload, sort_keys=True) + "\n", 6 if name == "check_gate" else 0
    monkeypatch.setattr(mcp_server, "run_owned",
                        lambda argv, **_: subprocess.CompletedProcess(argv, code, stdout, ""))
    session = mcp_server._Session(_measured(tmp_path / "repo"))
    mcp_server._initialize_result({"protocolVersion": "2024-11-05", "capabilities": {}}, session)
    return session.run_cli(mcp_server._tool_named(name), arguments, str(tmp_path / "repo"))


def test_a_gate_with_sixty_breaches_cuts_the_nested_breaches_and_keeps_the_verdict(monkeypatch,
                                                                                 tmp_path):
    """The breaches list sits inside `gate`; cutting only top-level fields left
    10,692 characters that carried `truncated` and still overflowed Cline."""
    payload = _gate()

    result = _cline_answer(monkeypatch, tmp_path, "check_gate", {"path": "calc/big.py"}, payload)

    answer = json.loads(result["content"][0]["text"])
    assert _embedded(result) <= ANSWER_CHARS
    assert _cline(result) <= 8000
    cut = _assert_cut_to_prefixes(answer, payload)
    assert "gate.breaches" in cut and cut["gate.breaches"]["of"] == 60
    assert answer["gate"]["ok"] is False and answer["gate"]["judged"] == 61
    assert answer["gate"]["breaches"][0]["function"] == "f0( a , b )"
    assert answer["truncated"]["full"].endswith(
        f'rescore --gate "--repo={(tmp_path / "repo").resolve()}" --json -- calc/big.py')


def test_a_map_keyed_by_file_is_cut_by_entries_after_the_lists(monkeypatch, tmp_path):
    """`gate.ceilings` has one entry per rescored file; a gate over a large
    dirty tree names hundreds. The map keeps its first entries, and the
    verdict fields beside it stay whole."""
    payload = {**_gate(files=800), "functions": []}
    payload["gate"]["breaches"] = payload["gate"]["breaches"][:3]

    result = _cline_answer(monkeypatch, tmp_path, "check_gate", {"path": "calc/big.py"}, payload)

    answer = json.loads(result["content"][0]["text"])
    assert _embedded(result) <= ANSWER_CHARS
    cut = _assert_cut_to_prefixes(answer, payload)
    assert set(cut) == {"gate.ceilings"} and cut["gate.ceilings"]["of"] == 800
    assert answer["gate"]["breaches"] == payload["gate"]["breaches"]
    assert answer["gate"]["ok"] is False and answer["gate"]["judged"] == 61


_SCALARS = {"integer": 123456, "number": 1234.5, "boolean": True}


def _kind(schema: dict):
    kind = schema.get("type")
    return next(k for k in kind if k != "null") if isinstance(kind, list) else kind


def _many(depth: int) -> int:
    return 400 if depth < 3 else 3


def _filled_map(entries: dict, depth: int) -> dict:
    return {f"src/pkg/module_{n:04}.py": _filled(entries, depth + 1) for n in range(_many(depth))}


def _properties(schema: dict) -> dict:
    return {key: value for key, value in schema.get("properties", {}).items() if key != "truncated"}


def _filled_object(schema: dict, depth: int) -> dict:
    """A map keyed by data when the schema names no properties, else every
    named property but `truncated`."""
    named, entries = _properties(schema), schema.get("additionalProperties")
    if isinstance(entries, dict) and not named:
        return _filled_map(entries, depth)
    return {key: _filled(value, depth + 1) for key, value in named.items()}


def _filled(schema: dict, depth: int = 0):
    """An answer as large as the output schema allows at each field: long
    lists, long strings and large maps, at every depth the schema declares."""
    fillers = {
        "object": _filled_object,
        "array": lambda s, d: [_filled(s.get("items", {"type": "string"}), d + 1)
                               for _ in range(_many(d))],
        "string": lambda s, d: "s" * (3000 if d < 3 else 30)}
    filler = fillers.get(_kind(schema))
    return filler(schema, depth) if filler else _SCALARS.get(_kind(schema))


@pytest.mark.parametrize("entry", tool_listing(), ids=lambda entry: entry["name"])
def test_every_tool_answer_as_large_as_its_schema_allows_fits(monkeypatch, tmp_path, entry):
    """The budget holds for every shape a tool declares, whatever depth its
    lists, strings and maps sit at, and every top-level field survives."""
    payload = _filled(entry.get("outputSchema") or {"type": "object", "properties": {}})

    result = _cline_answer(monkeypatch, tmp_path, entry["name"],
                           {key: "a.py" for key in entry["inputSchema"].get("required", [])},
                           payload)

    answer = json.loads(result["content"][0]["text"])
    assert _embedded(result) <= ANSWER_CHARS
    assert _cline(result) <= 8000
    assert set(payload) <= set(answer)
    _assert_cut_to_prefixes(answer, payload)
