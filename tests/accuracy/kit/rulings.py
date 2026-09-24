"""Rulings: every place crapkit and an oracle disagree, and what the difference is.

Each packet keeps a rulings.tsv. A row names the calculation, the oracle, the
construct they read differently, both values, and a ruling:

- `definition`: crapkit means something else on purpose. The row needs outside
  support (a URL, a paper section, a docs anchor with its #, or the literal
  `convention_only`, which JF answers before a merge).
- `defect`: crapkit is wrong. The row names its issue, and the test that pins
  it is a strict xfail until the fix lands.
- `fixed`: a former defect; both values now agree.

A test pins a row with `@rulings.applies(ID)` and a call to
`pin_ruling(ID, crapkit=..., oracle=...)` with the two values it measured.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import pytest

ACCURACY = Path(__file__).resolve().parents[1]
COLUMNS = ("id", "calc", "oracle", "construct", "crapkit_value", "oracle_value", "ruling",
           "outside_support", "docs_anchor", "test", "issue")
RULINGS = ("definition", "defect", "fixed")


class RulingsError(ValueError):
    """A rulings.tsv the kit cannot trust."""


class RulingDefect(AssertionError):
    """crapkit still gives the defect's recorded wrong value."""


@dataclass(frozen=True)
class Ruling:
    id: str
    calc: str
    oracle: str
    construct: str
    crapkit_value: str
    oracle_value: str
    ruling: str
    outside_support: str
    docs_anchor: str
    test: str
    issue: str
    source: str = ""


def _supported(text: str) -> bool:
    return (text == "convention_only" or text.startswith(("https://", "http://", "paper:"))
            or "#" in text)


_CHECKS = (
    (lambda row: row.ruling in RULINGS, f"the ruling is one of {', '.join(RULINGS)}"),
    (lambda row: row.ruling != "defect" or row.issue, "a defect names its issue"),
    (lambda row: row.ruling != "fixed" or row.crapkit_value == row.oracle_value,
     "a fixed row records one value on both sides"),
    (lambda row: _supported(row.outside_support),
     "outside_support is a URL, a paper: section, a docs anchor with #, or convention_only"),
)


def _problem(row: Ruling) -> str | None:
    return next((rule for check, rule in _CHECKS if not check(row)), None)


def _rows(path: Path) -> list[Ruling]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise RulingsError(f"{path}: header must be {' '.join(COLUMNS)}")
        return [Ruling(**record, source=str(path)) for record in reader]


def _checked(row: Ruling, seen: dict[str, Ruling]) -> Ruling:
    problem = _problem(row)
    if problem:
        raise RulingsError(f"{row.source}: {row.id}: {problem}")
    if row.id in seen:
        raise RulingsError(f"{row.id} appears in {seen[row.id].source} and {row.source}")
    return row


def load(root: Path = ACCURACY) -> dict[str, Ruling]:
    """Every row of every */rulings.tsv under root, keyed by id."""
    seen: dict[str, Ruling] = {}
    for path in sorted(root.glob("*/rulings.tsv")):
        for row in _rows(path):
            seen[row.id] = _checked(row, seen)
    return seen


def convention_only(rows: dict[str, Ruling]) -> list[str]:
    return sorted(key for key, row in rows.items() if row.outside_support == "convention_only")


def _row(ruling_id: str, rows: dict[str, Ruling] | None) -> Ruling:
    table = load() if rows is None else rows
    if ruling_id not in table:
        raise RulingsError(f"no rulings row {ruling_id}")
    return table[ruling_id]


def applies(ruling_id: str, rows: dict[str, Ruling] | None = None):
    """The decorator for a test that pins a row: a strict xfail while it is a defect."""
    row = _row(ruling_id, rows)
    if row.ruling != "defect":
        return lambda test: test
    return pytest.mark.xfail(strict=True, raises=RulingDefect,
                             reason=f"{ruling_id} is an open defect: {row.issue}")


def _defect(row: Ruling, crapkit: str) -> None:
    if crapkit == row.crapkit_value:
        raise RulingDefect(f"{row.id}: crapkit still says {crapkit}, {row.oracle} says "
                           f"{row.oracle_value} ({row.issue})")
    raise AssertionError(f"{row.id}: crapkit now says {crapkit}; the row records "
                         f"{row.crapkit_value} and {row.oracle} says {row.oracle_value}. "
                         "Update the row to fixed, or record the new value")


def pin_ruling(ruling_id: str, *, crapkit, oracle, rows: dict[str, Ruling] | None = None) -> None:
    """Both measured values against the row. Values compare as their str()."""
    row = _row(ruling_id, rows)
    measured = (str(crapkit), str(oracle))
    assert measured[1] == row.oracle_value, (
        f"{row.id}: {row.oracle} now says {measured[1]}, the row records {row.oracle_value}")
    if row.ruling == "defect" and measured[0] != row.oracle_value:
        _defect(row, measured[0])
    expected = row.crapkit_value if row.ruling == "definition" else row.oracle_value
    assert measured[0] == expected, (
        f"{row.id}: crapkit says {measured[0]}, the {row.ruling} row expects {expected}")
