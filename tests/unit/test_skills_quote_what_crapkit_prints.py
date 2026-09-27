"""The plugin skills and AGENTS.md quote crapkit's lines as crapkit prints them.

An agent matches a quoted line against its terminal. 0.8.1 spells every dash in
its own messages ` - `, and the recover skill and AGENTS.md still quoted five of
those lines with an em dash, so a match on the quote missed the real output.
The same pages have to agree with each other on what a flag asks for, and a
section that counts its causes has to count the rows it holds.
"""
import re
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RECOVER = "plugin/skills/crapkit-recover/SKILL.md"
SKILL = "plugin/skills/crapkit/SKILL.md"


@lru_cache(maxsize=None)
def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _joined(rel: str) -> str:
    return " ".join(_read(rel).split())


# (page, the text it quotes, the source file that prints it, the text there)
QUOTED = [
    (RECOVER, "were not gated - `crapkit verify` fails a mark that rises",
     "src/crapkit/cli/verifying.py", "were not gated - `crapkit verify` fails a mark that rises"),
    (RECOVER, "statement-based for this artifact - add --cov-branch to the lane command",
     "src/crapkit/coverage_py.py", "for this artifact - add --cov-branch to the "),
    (RECOVER, "(tpl/page.html) - those files are skipped and the rest of the report is scored",
     "src/crapkit/coverage_py.py", ") - those files "),
    (RECOVER, "wrote no artifact this run - the PATH on disk predates it",
     "src/crapkit/lanes.py", "wrote no artifact this run - {leftover}"),
    ("AGENTS.md", "no scored run in <root> - run \\`crapkit coverage\\` first",
     "src/crapkit/cli/queue.py", "no scored run in {root} - run"),
]


@pytest.mark.parametrize("page, quote, source, printed", QUOTED, ids=[q[1][:30] for q in QUOTED])
def test_each_quoted_line_spells_its_dash_as_crapkit_prints_it(page, quote, source, printed):
    assert printed in _read(source), f"{source} no longer prints {printed!r}"
    assert quote in _joined(page), f"{page} does not quote {quote!r}"


def test_no_line_the_recover_skill_quotes_from_crapkit_holds_an_em_dash():
    """A quoted `"crapkit..."` or `"wrote no artifact..."` cell is output, not prose."""
    quoted = re.findall(r'"((?:crapkit|wrote no artifact)[^"]*)"', _read(RECOVER))

    assert quoted
    assert [q for q in quoted if "—" in q] == []


def test_the_skill_and_agents_md_agree_that_committing_does_not_clear_measured():
    """Freshness is the file's blob id in 0.8.1, so a commit moves nothing; the
    skill told an agent to commit its work in progress to clear the flag."""
    bullet = next(line for line in _read(SKILL).splitlines() if line.startswith("- `measured`:"))

    assert "Commit or revert" not in bullet, bullet
    assert "Rerun `crapkit coverage`" in bullet and "committing changes nothing" in bullet, bullet
    assert "Committing changes nothing here" in _joined("AGENTS.md")


NUMBERS = {"five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}


def _no_artifact_section() -> str:
    text = _read(RECOVER)
    start = text.index("## a lane that wrote no artifact:")
    return text[start:text.index("\n## ", start + 1)]


def _cause_rows(section: str) -> int:
    """The table's body rows: its header row names `Cause`, and its rule row
    starts `|---`, which the `| ` prefix already leaves out."""
    return sum(1 for line in section.splitlines() if line.startswith("| ") and "| Cause |" not in line)


def test_the_no_artifact_section_counts_the_causes_its_table_holds():
    """Its heading and one sentence said seven while another said `none of the five`."""
    section = _no_artifact_section()
    rows = _cause_rows(section)
    counted = re.findall(r"\b(?:of|none of) the (five|six|seven|eight|nine)\b", section)
    heading = re.match(r"## a lane that wrote no artifact: (\w+) causes", section)

    assert NUMBERS[heading[1]] == rows, (heading[0], rows)
    assert counted and {NUMBERS[word] for word in counted} == {rows}, counted
