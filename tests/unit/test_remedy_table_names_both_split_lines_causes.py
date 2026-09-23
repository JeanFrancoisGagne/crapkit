"""README's remedy table gives both reasons a function gets `split-lines`.

score.shares_its_def_line sends a Python def written on one line to the
shared-span floor, because its only line is the `def` statement that runs at
import and coverage.py cannot show a call. AGENTS.md, the brief field table and
the crapkit skill say so; the README's remedy table named only a line span two
functions share, so a reader with a one-line def and no neighbour could not
tell why tests never lowered its score.
"""
from pathlib import Path

from crapkit.score import shares_its_def_line

ROOT = Path(__file__).resolve().parents[2]


class _Row:
    path, start, end = "calc/one.py", 7, 7


def _split_lines_row() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| `split-lines` |")]
    assert len(rows) == 1, f"expected one split-lines row, found {len(rows)}"
    return rows[0]


def test_a_one_line_python_def_takes_the_shared_span_floor():
    assert shares_its_def_line(_Row())


def test_the_row_names_the_one_line_def_and_the_line_it_shares():
    row = _split_lines_row()

    assert "one-line Python def" in row and "`def` statement" in row
