"""Every page and schema that defines est_uncovered_paths says it rounds half to even.

A bare `round((1 - cov) * ccn)` reads as half up to anyone who does not know
Python's round() takes a tie to the even neighbour, and 2.5 then reads 3 where
next-item and brief print 2.
"""
from pathlib import Path

from crapkit import mcp_server

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("README.md", "AGENTS.md", "CONTEXT.md", "docs/*.md", "docs/*.html",
         "plugin/skills/*/SKILL.md")


def _defining_lines() -> list[str]:
    pages = sorted({page for pattern in PAGES for page in ROOT.glob(pattern)})
    return [f"{page.relative_to(ROOT).as_posix()}:{number}: {line.strip()}"
            for page in pages
            for number, line in enumerate(page.read_bytes().decode("utf-8").splitlines(), 1)
            if "est_uncovered_paths" in line and "(1 - cov)" in line]


def _schema_descriptions(node) -> list[str]:
    """The description of every est_uncovered_paths property under `node`."""
    if isinstance(node, (list, tuple)):
        return [text for item in node for text in _schema_descriptions(item)]
    if not isinstance(node, dict):
        return []
    own = node.get("est_uncovered_paths")
    found = [own["description"]] if isinstance(own, dict) else []
    return found + [text for value in node.values() for text in _schema_descriptions(value)]


def test_every_page_that_gives_the_formula_says_half_to_even():
    lines = _defining_lines()

    assert len(lines) >= 2
    assert [line for line in lines if "half to even" not in line] == []


def test_every_mcp_output_schema_says_half_to_even():
    descriptions = _schema_descriptions(mcp_server.TOOLS)

    assert len(descriptions) >= 2
    assert [text for text in descriptions if "half to even" not in text] == []
