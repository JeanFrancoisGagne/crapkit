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


def _gives_the_formula(line: str) -> bool:
    return "est_uncovered_paths" in line and "(1 - cov)" in line


def _formula_lines(page: Path) -> list[str]:
    lines = page.read_bytes().decode("utf-8").splitlines()
    return [f"{page.relative_to(ROOT).as_posix()}:{number}: {line.strip()}"
            for number, line in enumerate(lines, 1) if _gives_the_formula(line)]


def _defining_lines() -> list[str]:
    pages = sorted({page for pattern in PAGES for page in ROOT.glob(pattern)})
    return [line for page in pages for line in _formula_lines(page)]


def _children(node) -> list:
    if isinstance(node, dict):
        return list(node.values())
    return list(node) if isinstance(node, (list, tuple)) else []


def _own_description(node) -> list[str]:
    own = node.get("est_uncovered_paths") if isinstance(node, dict) else None
    return [own["description"]] if isinstance(own, dict) else []


def _schema_descriptions(node) -> list[str]:
    """The description of every est_uncovered_paths property under `node`."""
    return _own_description(node) + [text for child in _children(node)
                                      for text in _schema_descriptions(child)]


def test_every_page_that_gives_the_formula_says_half_to_even():
    lines = _defining_lines()

    assert len(lines) >= 2
    assert [line for line in lines if "half to even" not in line] == []


def test_every_mcp_output_schema_says_half_to_even():
    descriptions = _schema_descriptions(mcp_server.TOOLS)

    assert len(descriptions) >= 2
    assert [text for text in descriptions if "half to even" not in text] == []
