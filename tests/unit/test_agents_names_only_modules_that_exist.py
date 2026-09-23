"""AGENTS.md's "Where code goes" tables name only modules the package holds.

`retention.py` left src/crapkit when test evidence retention moved to the
development runner, and its row stayed, sending a contributor to a file that
is not there to change a rule that no longer lives in the package.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "crapkit"
_MODULE_ROW = re.compile(r"^\| `([a-z_]+\.py)` \|", re.M)


def _section(heading: str) -> str:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    return text.split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0]


def _present(name: str) -> bool:
    return (PACKAGE / name).is_file() or (PACKAGE / "cli" / name).is_file()


def test_every_module_row_names_a_file_that_exists():
    rows = _MODULE_ROW.findall(_section("## Where code goes"))

    assert rows, "the section lost its module tables"
    assert [name for name in rows if not _present(name)] == []
