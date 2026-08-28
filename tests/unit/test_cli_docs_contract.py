"""README's Subcommands table is the surface agents drive crapkit from.

An agent that reads a table missing a subcommand never calls it, and one that
reads a row the parser dropped invents a command that exits 2. Both directions
are pinned here against the real parser.
"""
import argparse
import re
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from crapkit.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent.parent
HEADING = "## Subcommands"
_ROW = re.compile(r"^\|\s*`([^`]+)`")


def _section_lines(text: str) -> list[str]:
    lines = text.splitlines()
    assert HEADING in lines, f"README lost its {HEADING!r} heading"
    rest = lines[lines.index(HEADING) + 1:]
    end = next((i for i, ln in enumerate(rest) if ln.startswith("## ")), len(rest))
    return rest[:end]


@lru_cache(maxsize=None)
def documented() -> frozenset[str]:
    """First backticked cell of each table row, down to the command word:
    `runs prune [--keep N]` documents `runs`.

    Cached: three tests ask for it and the README does not change while the
    suite runs."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    cells = [_ROW.match(ln) for ln in _section_lines(text)]
    return frozenset(m.group(1).split()[0] for m in cells if m)


@lru_cache(maxsize=None)
def _subcommands() -> dict[str, argparse.ArgumentParser]:
    """The parser's subcommand table, built once for the module. `build_parser`
    imports a command family per subcommand, so every build is real work and
    every test here wants the same parser."""
    subs = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    return dict(subs[0].choices)


def defined() -> frozenset[str]:
    return frozenset(_subcommands())


def flags(name: str) -> set[str]:
    """Every option string one subcommand accepts."""
    return {opt for action in _subcommands()[name]._actions for opt in action.option_strings}


def test_the_readme_documents_every_subcommand_the_parser_defines():
    assert defined() - documented() == set(), "add a Subcommands row for each"


def test_the_readme_documents_no_subcommand_the_parser_lacks():
    assert documented() - defined() == set(), "drop or rename each stale Subcommands row"


def test_the_table_is_read_from_the_subcommands_section_alone():
    body = "## Subcommands\n\n| Command | What |\n|---|---|\n| `only-me` | x |\n\n## Next\n\n| `nope` | y |\n"
    assert [ln for ln in _section_lines(body) if _ROW.match(ln)] == ["| `only-me` | x |"]


# --- flags the packet rows promise -------------------------------------------
#
# Both read a live parser, so both are red until the parser change lands.

def test_brief_takes_the_batch_flag_the_readme_documents():
    assert "--batch" in flags("brief")


def test_explain_takes_the_json_flag_the_readme_documents():
    assert "--json" in flags("explain")


def test_doctor_takes_the_plugin_root_flag_the_readme_documents():
    """The plugin and the CLI ship apart, so the handshake between them is the
    one `doctor` mode an operator reaches for without a repo in hand. A row
    naming a flag argparse never got is a documented exit 2."""
    assert "--plugin-root" in flags("doctor")


# --- one parser build, one README read ---------------------------------------

def test_the_parser_is_built_once_for_the_whole_module(monkeypatch):
    """Pins the `_subcommands` cache, which both readers share: three tests want
    the subcommand set and two want one subcommand's flags, and building the
    parser imports a command family per subcommand."""
    _subcommands.cache_clear()
    builds: list[int] = []
    real = build_parser
    monkeypatch.setattr(sys.modules[__name__], "build_parser",
                        lambda: (builds.append(1), real())[1])

    assert defined() == defined()
    flags("brief")
    flags("explain")

    assert builds == [1], "every lookup reads one parser"
    _subcommands.cache_clear()


def test_the_readme_is_read_once_for_the_whole_module(monkeypatch):
    """Pins the `documented` cache, and that its key is the whole call: three
    reads of one file collapse to one."""
    documented.cache_clear()
    reads: list[str] = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text",
                        lambda self, **kw: (reads.append(self.name), real(self, **kw))[1])

    assert documented() == documented() == documented()

    assert reads == ["README.md"]
    documented.cache_clear()
