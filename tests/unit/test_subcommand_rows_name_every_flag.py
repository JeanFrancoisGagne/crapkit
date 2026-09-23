"""Each row of README's Subcommands table spells every flag its subcommand takes.

`ratchet seed --baseline ID` is the way past a failed verify that pins seed to
a run it cannot read (#75), and the ratchet row never showed the flag, so a
reader of the table had no route to it. `hook-precommit --base REF`, the form
CI runs, was missing the same way. `--repo` is left out: every subcommand takes
it and the table says so once.
"""
import argparse
import re
from functools import lru_cache
from pathlib import Path

import pytest

from crapkit.cli.parser import build_parser

ROOT = Path(__file__).resolve().parents[2]
_ROW = re.compile(r"^\|\s*`([^`]+)`", re.M)
_EVERY_SUBCOMMAND = {"-h", "--help", "--repo"}


@lru_cache(maxsize=1)
def _subcommands() -> dict[str, argparse.ArgumentParser]:
    subs = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    return dict(subs[0].choices)


@lru_cache(maxsize=1)
def _first_cells() -> dict[str, str]:
    """The first cell of every row, joined per subcommand word."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("\n## Subcommands\n", 1)[1].split("\n## ", 1)[0]
    cells: dict[str, str] = {}
    for cell in _ROW.findall(section):
        word = cell.split()[0]
        cells[word] = cells.get(word, "") + " " + cell
    return cells


def _spells(row: str, flag: str) -> bool:
    """The whole flag, so `--base` is not found inside `--baseline`."""
    return re.search(re.escape(flag) + r"(?![\w-])", row) is not None


@pytest.mark.parametrize("name", sorted(_subcommands()))
def test_the_row_spells_every_flag_the_subcommand_takes(name):
    taken = {flag for action in _subcommands()[name]._actions for flag in action.option_strings}
    row = _first_cells().get(name, "")

    assert sorted(flag for flag in taken - _EVERY_SUBCOMMAND if not _spells(row, flag)) == []


def test_a_flag_is_not_found_inside_a_longer_one():
    row = "verify [--baseline ID | --baseline-tsv PATH]"

    assert not _spells(row, "--base")
    assert _spells(row, "--baseline")
