"""The pages describe `rescore --gate` and check_gate by the rule the tool states.

check_gate's description says a ratchet mark pardons a changed function only
while its crap is at or under the mark, which is stricter than the commit hook,
and that the marks are read only on a breach. AGENTS.md still said the tool
answers whether a file "clears the commit gate", and the README called
`rescore --gate` the commit gate's verdict on demand, so an agent expected the
hook's looser pardon and met a breach it could not explain.
"""
from pathlib import Path

import pytest

from crapkit import mcp_server

ROOT = Path(__file__).resolve().parents[2]
PARDON = "at or under"


def _tool(name: str) -> dict:
    return next(tool for tool in mcp_server.TOOLS if tool["name"] == name)


def _row(page: str, first_cell: str) -> str:
    text = (ROOT / page).read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith(f"| {first_cell} |")]
    assert len(rows) == 1, f"{page}: expected one {first_cell} row, found {len(rows)}"
    return rows[0]


def test_the_tool_itself_states_the_pardon_rule():
    assert PARDON in _tool("check_gate")["description"]


@pytest.mark.parametrize(("page", "first_cell"), [
    ("AGENTS.md", "`check_gate`"),
    ("docs/agent-json.md", "`check_gate`"),
    ("README.md", "`crapkit rescore FILE --gate`"),
])
def test_the_row_states_the_pardon_rule(page, first_cell):
    assert PARDON in _row(page, first_cell)


def test_the_gate_step_says_when_the_marks_are_read():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    step = text.split("\n## 3. Gate the edit\n", 1)[1].split("\n## ", 1)[0]

    assert "The marks file is read only when a changed function is over its ceiling" in step
