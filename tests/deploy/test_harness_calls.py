"""lin-call-*: a real harness makes crapkit tool calls with crapkit's largest
answers, and the cell reads the tool result the harness hands its model.

The model is the scripted stub (kit/stub_openai.py, kit/stub_anthropic.py or
kit/stub_gemini.py).
It calls get_next_item, then get_function_brief on a 300-line function, then
list_worklist for the top 50, and records every request body. The repo holds
62 functions, 61 of them over the worklist floor, so both large answers are
as long as a real repo's. Gemini CLI adds a wait_for_previous boolean to every
MCP tool it shows its model and passes it on in the call, so the Gemini model
sets it, as Gemini's own model does.

A result reaches the model whole, or, where the harness moves a long result
to a file and hands the model the path, whole in that file at the moment the
model reads the result: Copilot CLI saves each long result to its own file,
Junie cuts the result at about 15,000 characters and writes the full output to
.output.txt in the repo, which its next long result overwrites. A cell fails
when the model gets neither: a harness that cuts the result past its output
cap, rewrites it into a shape that no longer parses, or never calls the tool.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kit import profiles
from kit.cells import cell
from kit.mcp_client import McpClient

PACKET = "deploy-harnesses"
BIG = "calc/big.py"
TOP = 50
CALLS = [("get_next_item", {}), ("get_function_brief", {"path": BIG, "name": "big"}),
         ("list_worklist", {"top": TOP})]
CELLS = {"claude-code": ("lin-call-claude", "core"), "opencode": ("lin-call-opencode", "full"),
         "goose": ("lin-call-goose", "full"), "cline": ("lin-call-cline", "full"),
         "continue": ("lin-call-continue", "full"), "copilot-cli": ("lin-call-copilot", "full"),
         "crush": ("lin-call-crush", "full"), "junie": ("lin-call-junie", "full"),
         "gemini-cli": ("lin-call-gemini", "full")}
# Keys a harness's own model adds to each call, which the harness forwards to crapkit.
CLIENT_ARGS = {"gemini-cli": {"wait_for_previous": False}}
BUGS = {"cline": "deploy-bug deploy-harnesses-5: Cline keeps 8,000 characters of a tool result and cuts the "
                 "middle out, so a Cline model gets neither get_function_brief on a 300-line function nor "
                 "list_worklist top 50 whole"}
# Copilot CLI: "Output too large to read at once (27.0 KB). Saved to: <path>".
# Junie: "... has been summarized. See full logs here: <repo>/.output.txt and <repo>/.output.json."
SAVED = re.compile(r"(?:Saved to|See full logs here): (\S+?\.txt)\b")


def big_function(branches: int = 150) -> str:
    """One function of `branches` if/return pairs: about 300 lines, ccn above 150."""
    body = "".join(f"    if x == {n}:\n        return {n * 3}\n" for n in range(branches))
    return f"def big(x):\n{body}    return -1\n"


def small_function(n: int) -> str:
    """ccn 7: over the worklist floor, so list_worklist ranks it."""
    tests = ("a > b", "a < b", "a == 0", "b == 0", "a == b", "a > 10")
    body = "".join(f"    if {test}:\n        return {n + k}\n" for k, test in enumerate(tests))
    return f"\n\ndef f{n}(a, b):\n{body}    return -{n}\n"


def big_repo(box, templates) -> Path:
    """The measured repo plus calc/big.py, measured again and committed."""
    repo = profiles.real_cli_box(box, templates)
    text = big_function() + "".join(small_function(n) for n in range(60))
    (repo / BIG).write_text(text, encoding="utf-8", newline="\n")
    profiles.commit(box, repo, "a big module")
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    return repo


def expected(box, repo: Path) -> dict[str, dict]:
    """What crapkit itself answers each call, read straight from the server."""
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        client.initialize()
        return {tool: json.loads(client.call(tool, arguments)["content"][0]["text"]) for tool, arguments in CALLS}


class SavedFiles:
    """What each file a tool result names held when the model received that
    result: the stub reads them before it answers the request carrying it."""

    def __init__(self):
        self.held: dict[str, str] = {}

    def __call__(self, body: dict) -> None:
        for text in profiles.tool_results([body]):
            self.held.setdefault(text, self._read(text))

    def summary(self) -> dict[str, int]:
        """Each saved file the model was pointed at -> the characters it held then."""
        return {SAVED.search(text)[1]: len(held) for text, held in self.held.items() if held is not None}

    @staticmethod
    def _read(text: str) -> str | None:
        match = SAVED.search(text)
        path = Path(match[1]) if match else None
        return path.read_text(encoding="utf-8") if path and path.is_file() else None


def delivered(text: str, answer: dict, saved: SavedFiles) -> str | None:
    """How one tool result carries crapkit's answer: whole, in a saved file, or not at all."""
    if profiles.result_json(text) == answer:
        return "whole"
    held = saved.held.get(text)
    return "saved-file" if held is not None and profiles.result_json(held) == answer else None


def how_delivered(results: list[str], answer: dict, saved: SavedFiles) -> str | None:
    return next((way for way in (delivered(text, answer, saved) for text in results) if way), None)


def deliveries(results: list[str], answers: dict[str, dict], saved: SavedFiles) -> dict[str, str | None]:
    """tool -> how its answer reached the model, None when it never did."""
    return {tool: how_delivered(results, answer, saved) for tool, answer in answers.items()}


def sizes(answers: dict[str, dict]) -> str:
    return ",".join(str(len(json.dumps(answer))) for answer in answers.values())


def joined(ways: dict[str, str | None]) -> str:
    return ",".join(f"{tool}={way}" for tool, way in ways.items())


def call_cell(key: str):
    cell_id, image = CELLS[key]

    @pytest.mark.xfail(key in BUGS, strict=True, reason=BUGS.get(key, ""))
    @cell(cell_id, channel="harness + stub model", harness="pinned real CLIs",
          scenario="fresh: scripted tool call with the largest payloads; tool_result the stub receives",
          use_cases="list_worklist, get_next_item, get_function_brief", os="linux", image=image, cadence="nightly")
    def test(box, templates, record_property):
        repo = big_repo(box, templates)
        answers = expected(box, repo)
        record_property("payload_chars", sizes(answers))
        saved = SavedFiles()
        calls = [(tool, {**arguments, **CLIENT_ARGS.get(key, {})}) for tool, arguments in CALLS]
        script, bodies, _ = profiles.stub_session(box, repo, key, calls, watch=saved)
        results = profiles.tool_results(bodies)
        box.transcript.attach("tool-results", results)
        box.transcript.attach("saved-files", saved.summary())
        assert len(script.called) == len(CALLS), (script.called, script.crapkit_tools())
        ways = deliveries(results, answers, saved)
        record_property("delivered", joined(ways))
        assert None not in ways.values(), f"the model never saw every answer whole: {joined(ways)}"
    test.__name__ = f"test_{cell_id.replace('-', '_')}"
    return test


for _key in CELLS:
    globals()[f"test_{CELLS[_key][0].replace('-', '_')}"] = call_cell(_key)
