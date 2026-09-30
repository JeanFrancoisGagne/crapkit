"""README's Subcommands rows name a command and its flags; docs/commands.md holds the rest.

The 0.8.1 docs audit counted the last cell of the doctor row at 851 words, and
mutate and claude-hook past 360, in a 1,965-line README. The rules those rows
spelled out moved to docs/commands.md, and each row links its section. The
duplication row stays long: tests/unit/test_duplication_comment_lines.py runs
each line the row quotes through the comment reader and reads it from the row.
"""
from pathlib import Path

from crapkit.cli import claude_hook

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = "https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/commands.md"
LONGEST = 300
MOVED = ("doctor", "mutate", "claude-hook")


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def _rows() -> dict[str, str]:
    """Each row's cells after the command, keyed by the command's first word, or
    by the whole command cell when rows share that word (the ratchet ones)."""
    table = _readme().split("\n## Subcommands\n", 1)[1].split("\n## ", 1)[0]
    cells = [line[3:].split("` | ", 1) for line in table.splitlines() if line.startswith("| `")]
    return {command if command.startswith("ratchet") else command.split()[0]: rest for command, rest in cells}


def _section(name: str) -> str:
    page = (ROOT / "docs" / "commands.md").read_text(encoding="utf-8")
    return " ".join(page.split(f"\n## {name}\n", 1)[1].split("\n## ", 1)[0].split())


def test_no_subcommand_row_but_duplication_runs_past_the_limit():
    rows = _rows()
    long = {name: len(cell.split()) for name, cell in rows.items() if len(cell.split()) > LONGEST}

    assert set(MOVED) <= set(rows)
    assert long.keys() <= {"duplication"}, long


def test_each_shortened_row_links_its_section():
    rows = _rows()

    assert [name for name in MOVED if f"({COMMANDS}#{name})" not in rows[name]] == []


def test_the_moved_rules_sit_in_their_sections():
    doctor, mutate, hook = (_section(name) for name in MOVED)

    assert "7.13.1" in doctor and "`container_ok`" in doctor and "2.1.139" in doctor
    assert "`crapkit doctor: checking PATH`" in doctor
    assert "`n-->0` reads as `n-- > 0`" in mutate and "`static_cast<T&&>(x)`" in mutate
    assert "Shell and PowerShell files are refused by name" in mutate
    assert "`crapkit advisory: PATH could not be read, so no function in it was judged`" in hook


def test_the_bash_window_the_hook_section_names_is_the_one_the_hook_reads():
    window, most = claude_hook._FRESH_WINDOW_SECONDS, claude_hook._MAX_COMMAND_FILES

    assert f"touched in the last {window} seconds, {most} at most" in _section("claude-hook")
