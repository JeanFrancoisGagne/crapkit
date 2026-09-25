"""A 0.5.x tool name answers with the name 0.6.0 gave it.

0.6.0 renamed every MCP tool to verb_noun. A client that pinned the old names
(a Codex `enabled_tools` list, a Claude Code `mcp__crapkit__worklist`
allowlist entry, a script) keeps sending them after the upgrade. The server
answered `unknown tool 'worklist'` and nothing else, so the model calling it
had no next call to try. The refusal now names the new tool, and a name that
was never a tool keeps the bare refusal.
"""
import re
from pathlib import Path

import pytest

from crapkit import mcp_server
from crapkit.mcp_server import RENAMED_IN_0_6_0, TOOLS

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
_ROW = re.compile(r"^\| `(\w+)` \| `(\w+)` \|$", re.MULTILINE)


def _no_cli(tool, arguments, repo):
    raise AssertionError(f"a refused call spawned the CLI for {tool['name']}")


def _answer(tmp_path: Path, name: str) -> dict:
    (tmp_path / "crapkit.toml").write_text("[crapkit]\ntarget = 6\n", encoding="utf-8")
    return mcp_server._call_tool(tmp_path, name, {}, run_cli=_no_cli)


def _changelog_table() -> dict[str, str]:
    """The 0.5.x -> 0.6.0 table the 0.6.0 changelog entry prints."""
    section = CHANGELOG.read_text(encoding="utf-8").partition("## 0.6.0 ")[2].partition("\n## ")[0]
    return dict(_ROW.findall(section))


@pytest.mark.parametrize("old,new", sorted(RENAMED_IN_0_6_0.items()))
def test_every_old_name_answers_with_its_new_one(tmp_path, old, new):
    result = _answer(tmp_path, old)

    assert result["isError"] is True
    assert result["content"][0]["text"] == (
        f"unknown tool {old!r}: renamed {new} in 0.6.0, with the same arguments and result; "
        f"call {new}")


def test_the_worklist_refusal_reads_as_the_upgrade_guide_promises(tmp_path):
    text = _answer(tmp_path, "worklist")["content"][0]["text"]

    assert text.startswith("unknown tool 'worklist': renamed list_worklist in 0.6.0")


def test_a_name_that_was_never_a_tool_keeps_the_bare_refusal(tmp_path):
    for name in ("no_such_tool", "", "list_worklists"):
        assert _answer(tmp_path, name)["content"][0]["text"] == f"unknown tool {name!r}"


def test_the_table_is_the_one_the_changelog_published():
    """The changelog is what a 0.5.x user was told. A row missing here is an
    old name that still answers bare; a row the changelog never printed is a
    rename nobody announced."""
    assert RENAMED_IN_0_6_0 == _changelog_table()
    assert len(RENAMED_IN_0_6_0) == 10


def test_every_new_name_is_a_tool_the_server_serves_and_no_old_one_is():
    served = {tool["name"] for tool in TOOLS}

    assert set(RENAMED_IN_0_6_0.values()) <= served
    assert not set(RENAMED_IN_0_6_0) & served
