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


def test_the_moved_rules_sit_in_their_sections():
    doctor, mutate, hook, duplication = (_section(name) for name in MOVED)

    assert "7.13.1" in doctor and "`container_ok`" in doctor and "2.1.139" in doctor
    assert "`crapkit doctor: checking PATH`" in doctor
    assert "`n-->0` reads as `n-- > 0`" in mutate and "`static_cast<T&&>(x)`" in mutate
    assert "Shell and PowerShell files are refused by name" in mutate
    assert "`no verdict: N of the K killed ran no test (exit 5), so no test caught them`" in mutate
    assert "`crapkit advisory: PATH could not be read, so no function in it was judged`" in hook
    assert "`**options`, `*out = x;`" in duplication and "so a Rust lifetime opens nothing" in duplication
    assert "in Rust and Swift too, where block comments nest" in duplication


def test_the_next_item_rules_the_row_dropped_sit_in_agent_json():
    queue = _section("`next-item`", "agent-json.md")
    fields = (ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8")

    assert "Scores equal at 4 decimal places go to the file with more commits" in queue
    assert "`(anonymous)#N`" in fields and "`PKG/Legacy` is `pkg/legacy` where the disk ignores case" in fields
    assert "`scored_changes` counts the files whose content differs now" in queue


def test_the_bash_window_the_hook_section_names_is_the_one_the_hook_reads():
    window, most = claude_hook._FRESH_WINDOW_SECONDS, claude_hook._MAX_COMMAND_FILES

    assert f"touched in the last {window} seconds, {most} at most" in _section("claude-hook")
