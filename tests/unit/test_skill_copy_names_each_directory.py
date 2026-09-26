"""Every page that says to copy plugin/skills/* names where the copy goes.

The copy is the way in for a runtime with no plugin marketplace, or for a user
who wants no plugin. "Its own skills directory" or "that runtime's equivalent"
left the reader to find the directory, and each runtime reads a different one.
The three below are the ones a deploy cell copied into and saw each runtime
list the three skills from: Claude Code's, Codex's (`$CODEX_HOME`, `~/.codex`
by default) and Gemini CLI's.

Gemini CLI 0.61.0 reads a skill's name and description and nothing else, so it
lists crapkit-onboard to its model beside the two skills meant for it. The
only way to take it out is its own settings, which the same paragraph names.
"""
import re
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FALLBACK = "plugin/skills/*"
PAGES = ("README.md", "docs/adoption.md", "docs/handbook.html",
         "plugin/skills/crapkit-onboard/SKILL.md")
NAMED = ("~/.claude/skills", "$CODEX_HOME/skills", "~/.codex/skills", "~/.gemini/skills",
         "gemini skills disable crapkit-onboard --scope user")


@lru_cache(maxsize=None)
def _text(page: str) -> str:
    return (ROOT / page).read_text(encoding="utf-8")


def _passage(page: str) -> str:
    """From the fallback to the end of its paragraph or of the page, whitespace
    collapsed."""
    text = _text(page)
    at = text.index(FALLBACK)
    ends = [end for end in (text.find("\n\n", at), text.find("</p>", at)) if end != -1]
    return " ".join(text[at:min(ends, default=len(text))].split())


def _pages_with_the_fallback() -> list[str]:
    pages = ["README.md", "AGENTS.md", "docs/handbook.html",
             *(p.relative_to(ROOT).as_posix() for p in ROOT.glob("docs/*.md")),
             *(p.relative_to(ROOT).as_posix() for p in ROOT.glob("plugin/skills/*/*.md"))]
    return sorted(page for page in pages if _gives_the_fallback(page))


def _gives_the_fallback(page: str) -> bool:
    return page != PER_AGENT and FALLBACK in _text(page)


def test_the_list_below_holds_every_page_that_gives_the_fallback():
    assert _pages_with_the_fallback() == sorted(PAGES)


# docs/harnesses.md gives no fallback: the row of each agent that loads skills
# names the directory that agent reads, Continue's and Zed's among them.
PER_AGENT = "docs/harnesses.md"


def test_each_agent_row_that_copies_the_skills_names_its_directory():
    rows = [line for line in _text(PER_AGENT).splitlines() if FALLBACK in line]

    assert rows, "the harness page names no skills copy"
    assert [row for row in rows if not re.search(r"`[^`]*skills`", row)] == []


@pytest.mark.parametrize("page", PAGES)
def test_the_fallback_names_each_runtimes_skills_directory(page: str):
    passage = _passage(page)

    assert [name for name in NAMED if name not in passage] == [], passage


def test_the_passage_stops_at_the_end_of_its_paragraph():
    """Guards the test above: a passage that ran to the end of the page would
    find the names anywhere below it."""
    assert _passage("docs/adoption.md").endswith("(agent-json.md#mcp-server).")
    assert _passage("docs/handbook.html").endswith("Agreement prints nothing at all, at exit 0.")
    assert "## Languages" not in _passage("README.md")
