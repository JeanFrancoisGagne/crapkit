"""The pages describe `rescore --gate` and check_gate by the rule the tool states.

check_gate's description says a ratchet mark pardons a changed function only
while its crap is at or under the mark, which is stricter than the commit hook,
and that a clean gate reads no marks. AGENTS.md said the tool answered whether
a file "clears the commit gate", and the README called `rescore --gate` the
commit gate's verdict on demand, so an agent expected the hook's looser pardon
and met a breach it could not explain. The hook refuses a marked function once
its staged ccn passes the mark, so no row may say any mark pardons.
"""
from pathlib import Path

import pytest

from crapkit import mcp_server

ROOT = Path(__file__).resolve().parents[2]
PARDON = "at or under"
ROWS = [
    ("AGENTS.md", "`check_gate`"),
    ("docs/agent-json.md", "`check_gate`"),
    ("README.md", "`crapkit rescore FILE --gate`"),
]
# The hook's rule before 0.9.0, each spelling a page used for it.
ANY_MARK_PARDONS = ("pardons any marked function", "where any mark pardons", "exempts on the mark's existence")


def _tool(name: str) -> dict:
    return next(tool for tool in mcp_server.TOOLS if tool["name"] == name)


def _row(page: str, first_cell: str) -> str:
    text = (ROOT / page).read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith(f"| {first_cell} |")]
    assert len(rows) == 1, f"{page}: expected one {first_cell} row, found {len(rows)}"
    return rows[0]


def test_the_tool_itself_states_the_pardon_rule():
    assert PARDON in _tool("check_gate")["description"]


@pytest.mark.parametrize(("page", "first_cell"), ROWS)
def test_the_row_states_the_pardon_rule(page, first_cell):
    assert PARDON in _row(page, first_cell)


def _handbook_rows() -> list[str]:
    text = (ROOT / "docs/handbook.html").read_text(encoding="utf-8")
    return [line for line in text.splitlines()
            if line.lstrip().startswith("<tr>") and ">rescore FILE --gate</td>" in line]


def test_the_handbook_rows_state_the_pardon_rule():
    """The ladder and the lookup table each have a row for it, and each called
    it the hook's own verdict before you stage."""
    rows = _handbook_rows()

    assert len(rows) == 2
    assert [PARDON in row and "same verdict" not in row for row in rows] == [True, True]


def test_no_row_says_the_hook_pardons_any_marked_function():
    rows = [_row(page, first_cell) for page, first_cell in ROWS] + _handbook_rows()

    assert [(row, old) for row in rows for old in ANY_MARK_PARDONS if old in row] == []


def test_the_handbook_walkthrough_does_not_call_it_the_same_verdict():
    text = (ROOT / "docs/handbook.html").read_text(encoding="utf-8")

    assert "the same verdict, on demand" not in text


def test_the_gate_step_says_when_the_marks_are_read():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    step = text.split("\n## 3. Gate the edit\n", 1)[1].split("\n## ", 1)[0]

    assert "The marks file is read only when a changed function is over its ceiling" in step
