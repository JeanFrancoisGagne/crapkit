"""Every MCP tool answers what the CLI command the docs map it to prints.

The session run (golden_runs) calls each of the 12 tools, some twice to reach
the array and bool arguments, in one `crapkit mcp` session, and runs the CLI
argv docs/agent-json.md ("MCP server") maps each call to. The rules below are
written from that section, not from mcp_server.py:

- `isError` is true exactly when the CLI call exited non-zero, except
  `check_gate`, whose exit 6 is the verdict and answers `isError: false`.
- The text is the CLI's `--json` payload (`get_next_item`, which has no
  `--json`, prints JSON anyway); on an error it is what the CLI printed: the
  error object for a `--json` tool, the stderr line for `get_next_item`.
- `structuredContent` carries the same object parsed whenever the call exited
  0 (and on `check_gate`'s 6); an error carries none.

Payloads compare as parsed JSON after kit.surfaces.normalize, which replaces
times, durations, versions and the repo root.
"""
import json

import pytest

from accuracy.corpus_goldens import golden_runs
from accuracy.kit import surfaces

pytestmark = pytest.mark.process
LABELS = [label for label, *_ in golden_runs.MCP_CALLS]
TOOLS = {label: tool for label, tool, _, _ in golden_runs.MCP_CALLS}
ARGV = {label: argv for label, _, _, argv in golden_runs.MCP_CALLS}


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    return golden_runs.session(golden_runs.shared_base(tmp_path_factory))


def _volatile(run) -> surfaces.Volatile:
    return surfaces.Volatile(roots=surfaces.spellings(run.root))


def _result(run, label: str) -> dict:
    return json.loads((run.raw / f"mcp-{label}.json").read_text(encoding="utf-8"))


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def _parsed(text: str, volatile: surfaces.Volatile):
    return surfaces.normalize(json.loads(text), volatile)


def _code(run, label: str) -> int:
    return run.codes[f"cli-{label}.json"]


def is_error(tool: str, code: int) -> bool:
    """The docs' rule: any non-zero exit, but check_gate's verdict exit 6."""
    return code != 0 and not (tool == "check_gate" and code == 6)


def test_every_tool_is_called_at_least_once():
    assert sorted(set(TOOLS.values())) == sorted([
        "check_config", "check_gate", "get_function_brief", "get_function_history",
        "get_next_item", "get_ratchet_report", "get_trend", "list_claims",
        "list_coupled_files", "list_duplicate_functions", "list_runs", "list_worklist"])


def test_array_arguments_become_one_flag_per_element():
    """docs/agent-json.md: `scope` and `exclude` elements each become their own
    flag; `history` and `tests` add --history and --tests."""
    assert ARGV["get_next_item-arrays"].count("--exclude") == 2
    assert ARGV["get_next_item-arrays"].count("--scope") == 2
    assert {"--history", "--tests"} <= set(ARGV["get_function_history-flags"])


@pytest.mark.parametrize("label", LABELS)
def test_is_error_follows_the_cli_exit(session, label):
    result = _result(session, label)

    assert bool(result.get("isError")) == is_error(TOOLS[label], _code(session, label))


@pytest.mark.parametrize("label", LABELS)
def test_structured_content_is_present_exactly_when_the_call_succeeded(session, label):
    result = _result(session, label)
    wanted = not is_error(TOOLS[label], _code(session, label))

    assert ("structuredContent" in result) == wanted


@pytest.mark.cross_surface
@pytest.mark.parametrize("label", LABELS)
def test_the_text_is_what_the_cli_printed(session, label):
    result, volatile = _result(session, label), _volatile(session)
    printed = session.output(f"cli-{label}.json")
    stderr = session.output(f"cli-{label}.json.stderr")
    if printed.strip():
        assert _parsed(_text(result), volatile) == _parsed(printed, volatile)
    else:
        assert _text(result).strip() == stderr.strip().splitlines()[-1]


@pytest.mark.cross_surface
@pytest.mark.parametrize("label", LABELS)
def test_structured_content_equals_the_cli_json(session, label):
    result, volatile = _result(session, label), _volatile(session)
    if "structuredContent" in result:
        assert surfaces.normalize(result["structuredContent"], volatile) == _parsed(
            session.output(f"cli-{label}.json"), volatile)


def test_the_unknown_name_refuses_on_both_sides(session):
    label = "get_function_brief-unknown"
    result = _result(session, label)

    assert _code(session, label) != 0 and result["isError"] is True
    assert json.loads(_text(result))["error"]["kind"]
