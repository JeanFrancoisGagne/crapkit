"""probes/<lang>/ground_truth.tsv, read: each function's arms and statements, worked by hand.

A row names one function in one scenario (`call` runs probes/*/drive*, `idle`
only imports). `arms` are the decision arms the producer's documented model
counts, `arms_taken` the ones the driver's calls take, `stmts` and `stmts_run`
the statement lines likewise. `-` is an empty list; `absent` means the producer
leaves the function out of its artifact and `excluded` that it keeps the function
with every line excluded (a coverage.py pragma); `n/a` means the model says nothing,
and only called-equals-idle is asserted. `producers` narrows a JS row to the
`v8` or `istanbul` provider family; a Python table has no such column.

expected() is the README's coverage term over a row, and crapkit_expected() adds
the README's floor: a Python def whose body starts on its signature's last line
scores 0, untested, whatever ran.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBES = HERE / "probes"
FLOORED = ("one-line", "body-on-signature")
# The producers whose artifacts follow a documented branch model the tables are
# worked from. jest-v8 and c8 write raw v8-to-istanbul output: rulings D7 and CO4.
FAMILIES = {
    "coveragepy-7.16.1": "coveragepy", "coveragepy-7.13.0": "coveragepy",
    "coveragepy-7.10.6": "coveragepy", "pytest-cov-5.0.0": "coveragepy",
    "pytest-cov-7.1.0": "coveragepy", "coveragepy-live": "coveragepy",
    "vitest-v8-5.0.1": "v8", "vitest-istanbul-5.0.1": "istanbul",
    "jest-babel-30.5.2": "istanbul", "nyc-18.0.0": "istanbul",
}


@dataclass(frozen=True)
class Truth:
    path: str
    function: str
    start: int
    end: int
    layout: str
    scenario: str
    producers: str
    arms: tuple
    arms_taken: tuple
    stmts: tuple
    stmts_run: tuple
    source: str

    @property
    def unmeasured(self) -> bool:
        """The producer measured nothing of it on purpose: an ignore hint or a pragma."""
        return self.arms in (("absent",), ("excluded",))

    @property
    def modelled(self) -> bool:
        return self.arms != ("n/a",)


def _list(cell: str) -> tuple:
    return () if cell == "-" else tuple(cell.split())


def _truth(cells: dict) -> Truth:
    return Truth(cells["path"], cells["function"], int(cells["start"]), int(cells["end"]),
                 cells["layout"], cells["scenario"], cells.get("producers", "all"),
                 _list(cells["arms"]), _list(cells["arms_taken"]), _list(cells["stmts"]),
                 _list(cells["stmts_run"]), cells["source"])


def load(language: str) -> list[Truth]:
    text = (PROBES / language / "ground_truth.tsv").read_bytes().decode("utf-8")
    lines = [line for line in text.split("\n") if line]
    header = lines[0].split("\t")
    return [_truth(dict(zip(header, line.split("\t")))) for line in lines[1:]]


def _describes(row: Truth, scenario: str, probes: tuple, family: str) -> bool:
    return (row.scenario, row.path in probes, row.producers in ("all", family)) == (
        scenario, True, True)


def rows_for(producer: str, scenario: str, probes: tuple) -> list[Truth]:
    """The rows that describe one recording: its scenario, its files, its family."""
    family = FAMILIES[producer]
    return [row for row in _tables(probes) if _describes(row, scenario, probes, family)]


def _tables(probes: tuple) -> list[Truth]:
    """Every row of the tables of the probes' languages."""
    languages = sorted({probe.split("/")[0] for probe in probes})
    return [row for language in languages for row in load(language)]


def expected(row: Truth) -> Fraction:
    """README.md: branches in the span, else statements, else called or not."""
    if row.arms:
        return Fraction(len(row.arms_taken), len(row.arms))
    if row.stmts:
        return Fraction(len(row.stmts_run), len(row.stmts))
    return Fraction(int(row.scenario == "call"))


def crapkit_expected(row: Truth) -> tuple[Fraction, str]:
    """What crapkit should score: the floor for a Python def-line layout, else
    the ratio, measured."""
    if row.path.endswith(".py") and row.layout in FLOORED:
        return Fraction(0), "untested"
    return expected(row), "measured"
