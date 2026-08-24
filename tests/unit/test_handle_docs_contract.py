"""The pages have to name the handle, the promoted packet fields and the refresh
contract, because a session reads the page before it reads a payload.

Each assertion here compares a documented string against the code that produces
it, so a field that gets renamed or a command that gets rewritten fails here
rather than sending an agent to run something that no longer exists.
"""
from functools import lru_cache
from pathlib import Path

import pytest

from crapkit import packet

ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_PAGES = ("AGENTS.md", "README.md", "docs/agent-json.md")


@lru_cache(maxsize=None)
def _doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# --- the handle --------------------------------------------------------------

@pytest.mark.parametrize("page", AGENT_PAGES)
def test_every_agent_page_names_the_ordinal_handle_form(page: str):
    """A session that never sees the form guesses the start line and loses it to
    its own edit."""
    assert "(anonymous)#" in _doc(page), page
    assert "`handle`" in _doc(page), page


@pytest.mark.parametrize("page", ("AGENTS.md", "docs/agent-json.md"))
def test_the_pages_say_the_handle_counts_positions_not_lines(page: str):
    text = " ".join(_doc(page).lower().split())
    assert "second anonymous function" in text or "counting the file's anonymous" in text \
        or "counts the file's anonymous functions" in text, page


def test_the_documented_out_of_range_message_is_the_one_the_tool_prints():
    from crapkit.cli import _no_handle_message

    printed = _no_handle_message("calc/report.py", "(anonymous)#5", _anon_rows(2))

    assert printed == ("no (anonymous)#5 in calc/report.py in the latest scored run"
                       " — it holds: (anonymous)#1, (anonymous)#2")
    assert printed in _doc("AGENTS.md"), "AGENTS.md prints a message nothing emits"


def _anon_rows(count: int) -> list:
    from crapkit.score import ScoredRow

    return [ScoredRow("web", "calc/report.py", "(anonymous)", 10 * n, 10 * n + 4,
                      5, 5, 5, 4, 0, 1, 0.5, "measured", 30.0, "decompose")
            for n in range(1, count + 1)]


def test_the_handle_the_pages_promise_is_the_one_the_code_builds():
    assert packet.handles(_anon_rows(2)) == {10: "(anonymous)#1", 20: "(anonymous)#2"}


# --- the refresh contract ----------------------------------------------------

@pytest.mark.parametrize("page", ("AGENTS.md", "docs/agent-json.md"))
def test_the_pages_name_the_field_that_says_refresh_writes(page: str):
    assert "refresh_writes_run" in _doc(page), page


def test_no_page_still_prints_the_refresh_command_that_could_not_refresh():
    """`brief` re-reads the snapshot that is already stale. A page printing it as
    the answer to `stale: true` sends a session round a loop with no exit."""
    for page in AGENT_PAGES:
        text = " ".join(_doc(page).split())
        assert "refresh\": \"crapkit brief" not in text, page
        assert "refresh\": \"python -m crapkit brief" not in text, page


def test_the_json_page_prints_the_refresh_command_the_packet_carries():
    """Without the `python -m` prefix: the page's transcripts use the console
    script, and the flag is the load-bearing half."""
    flag = packet.REFRESH.split("crapkit ", 1)[1]

    assert f"crapkit {flag}" in _doc("docs/agent-json.md")
    assert flag == "coverage --reuse-unchanged"


# --- the promoted packet fields ----------------------------------------------

@pytest.mark.parametrize("field", ("remedy", "est_splits", "est_uncovered_paths"))
def test_the_json_page_documents_the_promoted_packet_fields(field: str):
    brief_section = _doc("docs/agent-json.md").split("## `brief`", 1)[1]

    assert f"`{field}`" in brief_section, f"the brief section never names `{field}`"


def test_the_agents_packet_table_names_what_the_packet_promoted():
    section = _doc("AGENTS.md").split("## 1. The packet", 1)[1].split("\n## ", 1)[0]

    for field in ("`handle`", "`remedy`", "`est_splits`"):
        assert field in section, f"the packet table never names {field}"
