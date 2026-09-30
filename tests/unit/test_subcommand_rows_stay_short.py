"""README's Subcommands rows name a command and its flags; other pages hold the rest.

The 0.8.1 docs audit counted the last cell of the doctor row at 851 words, and
duplication, mutate, claude-hook and next-item past 230, in a 1,965-line README.
The rules those rows spelled out moved to docs/commands.md, and each row links
its section. next-item's field rules already sat in docs/agent-json.md, so its
row links there. The duplication row keeps each line it quotes, with how the
comment reader reads it: tests/unit/test_duplication_comment_lines.py runs those
lines through the reader and reads them from the row.
"""
from pathlib import Path

import pytest

from crapkit.cli import claude_hook

ROOT = Path(__file__).resolve().parents[2]
BLOB = "https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs"
COMMANDS = f"{BLOB}/commands.md"
LONGEST = 200
MOVED = ("doctor", "mutate", "claude-hook", "duplication")


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def _rows() -> dict[str, str]:
    """Each row's cells after the command, keyed by the command's first word, or
    by the whole command cell when rows share that word (the ratchet ones)."""
    table = _readme().split("\n## Subcommands\n", 1)[1].split("\n## ", 1)[0]
    cells = [line[3:].split("` | ", 1) for line in table.splitlines() if line.startswith("| `")]
    return {command if command.startswith("ratchet") else command.split()[0]: rest for command, rest in cells}


def _section(name: str, page: str = "commands.md") -> str:
    text = (ROOT / "docs" / page).read_text(encoding="utf-8")
    return " ".join(text.split(f"\n## {name}\n", 1)[1].split("\n## ", 1)[0].split())


def test_no_subcommand_row_runs_past_the_limit():
    rows = _rows()
    long = {name: len(cell.split()) for name, cell in rows.items() if len(cell.split()) > LONGEST}

    assert set(MOVED) | {"next-item"} <= set(rows)
    assert long == {}, long


def test_each_shortened_row_links_its_section():
    rows = _rows()

    assert [name for name in MOVED if f"({COMMANDS}#{name})" not in rows[name]] == []
    assert f"({BLOB}/agent-json.md#next-item)" in rows["next-item"]


# A sentence from each rule a row dropped, keyed by the section that holds it now.
MOVED_RULES = {
    "doctor": ("7.13.1", "`container_ok`", "2.1.139", "`crapkit doctor: checking PATH`"),
    "mutate": ("`n-->0` reads as `n-- > 0`", "`static_cast<T&&>(x)`",
               "Shell and PowerShell files are refused by name",
               "`no verdict: N of the K killed ran no test (exit 5), so no test caught them`"),
    "claude-hook": ("`crapkit advisory: PATH could not be read, so no function in it was judged`",),
    "duplication": ("`**options`, `*out = x;`", "so a Rust lifetime opens nothing",
                    "in Rust and Swift too, where block comments nest"),
}
NEXT_ITEM_RULES = ("Scores equal at 4 decimal places go to the file with more commits",
                   "`scored_changes` counts the files whose content differs now",
                   "`(anonymous)#N`", "`PKG/Legacy` is `pkg/legacy` where the disk ignores case")


@pytest.mark.parametrize("name", MOVED)
def test_the_moved_rules_sit_in_their_sections(name):
    section = _section(name)

    assert [rule for rule in MOVED_RULES[name] if rule not in section] == []


def test_the_next_item_rules_the_row_dropped_sit_in_agent_json():
    page = " ".join((ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8").split())

    assert [rule for rule in NEXT_ITEM_RULES if rule not in page] == []


def test_the_bash_window_the_hook_section_names_is_the_one_the_hook_reads():
    window, most = claude_hook._FRESH_WINDOW_SECONDS, claude_hook._MAX_COMMAND_FILES

    assert f"touched in the last {window} seconds, {most} at most" in _section("claude-hook")
