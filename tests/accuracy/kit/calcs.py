"""Every packet's calcs.tsv: which calculation it checks, the independent test that
checks it, and the production code the calculation lives in.

A calcs.tsv is a tab-separated table with this header:

    calc    independent_test    modules    functions

- calc: the calculation's name as the accuracy plan's matrix spells it.
- independent_test: the pytest node id of one test whose expected value does
  not come from crapkit. Its import closure holds no crapkit module
  (kit/closure.py), which test_kit_contract checks.
- modules: comma-separated repo paths of the modules the calculation lives
  in (src/crapkit/score.py). pyproject.toml's [tool.mutmut] paths_to_mutate
  is their union, and the pre-push hook runs the checks of every calc whose
  module a diff touches.
- functions: comma-separated `path:qualified.name` entries naming the
  production functions the independent test reaches. Each must run a body
  line on the golden CLI run or, where that run starts no command reaching it,
  under the independent test itself (suite_strength/test_calc_reach.py; a test
  of a copy of the rule proves nothing about the rule).

A calc belongs to exactly one packet.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ACCURACY = Path(__file__).resolve().parents[1]
COLUMNS = ("calc", "independent_test", "modules", "functions")


class CalcsError(ValueError):
    """A calcs.tsv that breaks the format above."""


@dataclass(frozen=True)
class Calc:
    packet: str
    calc: str
    independent_test: str
    modules: tuple[str, ...]
    functions: tuple[str, ...]


def _split(cell: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in cell.split(",") if part.strip())


def _row(packet: str, cells: list[str]) -> Calc:
    if len(cells) != len(COLUMNS) or not all(cell.strip() for cell in cells):
        raise CalcsError(f"{packet}/calcs.tsv: a row needs all of {', '.join(COLUMNS)}: {cells}")
    calc, test, modules, functions = cells
    return Calc(packet, calc, test, _split(modules), _split(functions))


def _lines(path: Path) -> list[str]:
    text = path.read_bytes().decode("utf-8")
    return [line.removesuffix("\r") for line in text.split("\n") if line.strip()]


def _table(path: Path) -> list[Calc]:
    lines = _lines(path)
    if not lines or tuple(lines[0].split("\t")) != COLUMNS:
        raise CalcsError(f"{path}: the header must be {' '.join(COLUMNS)} (tab-separated)")
    return [_row(path.parent.name, line.split("\t")) for line in lines[1:]]


def load(root: Path = ACCURACY) -> list[Calc]:
    """Every row of every packet's calcs.tsv, refusing a calc two rows name."""
    rows = [row for path in sorted(root.glob("*/calcs.tsv")) for row in _table(path)]
    seen: dict[str, Calc] = {}
    for row in rows:
        other = seen.setdefault(row.calc, row)
        if other is not row:
            raise CalcsError(f"calc {row.calc!r} is named by {other.packet} and {row.packet}")
    return rows


def modules(rows: list[Calc]) -> list[str]:
    """The union of every row's modules: pyproject.toml's paths_to_mutate."""
    return sorted({module for row in rows for module in row.modules})


def touched(rows: list[Calc], changed: list[str]) -> list[Calc]:
    """The calcs whose modules a diff touches, for the pre-push hook."""
    wanted = set(changed)
    return [row for row in rows if wanted.intersection(row.modules)]
