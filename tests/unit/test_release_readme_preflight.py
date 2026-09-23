"""tools/release/README.md says what `release.py check` refuses, in the words it prints.

The README once said `check` refused its first two preflight rows. It refused the
second and third, and nothing looked at the first (whether PATH's python is the
release venv's). A reader trusted `check` with a row it never read.
"""
from pathlib import Path

from test_release_tool import release

README = Path(__file__).resolve().parents[2] / "tools" / "release" / "README.md"


def _preflight_section():
    text = README.read_text(encoding="utf-8")
    return text.split("## Preflight", 1)[1].split("\n## ", 1)[0]


def _rows(section):
    """Table rows as cell lists, header and rule dropped."""
    lines = [line for line in section.splitlines() if line.startswith("|")]
    return [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[2:]]


def _every_refusal():
    return release.preflight(locate=lambda name: None, credential=lambda: False)


def test_the_readme_quotes_each_line_check_prints_when_it_refuses():
    section = _preflight_section()

    missing = [line for line in _every_refusal() if line not in section]

    assert missing == []


def test_the_rows_marked_for_check_are_as_many_as_the_refusals_it_prints():
    checked = [row[0] for row in _rows(_preflight_section()) if row[1] == "`check`"]

    assert len(checked) == len(_every_refusal()) == 2, checked
