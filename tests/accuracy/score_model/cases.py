"""The packet's tables and grids, read without crapkit.

hand_score.tsv holds one row per worked case: `input` and `expected` are
space-separated key=value pairs, and `source` names where the expected value
comes from. The CRAP grid is ccn 1 to 40 against covered/total for total 1 to
120: 295,200 cases in full, 175,480 once equal fractions are kept once (a
float cov is covered/total rounded once, so equal fractions give crapkit equal
inputs).
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRID_CCN = range(1, 41)
GRID_TOTAL = range(1, 121)


def _pairs(text: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in text.split())


def _table(name: str) -> list[dict]:
    lines = (HERE / name).read_bytes().decode("utf-8").split("\n")
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:] if line.strip()]


def hand(calc: str) -> list[tuple[str, dict, dict]]:
    """(case, input, expected) for every hand row of one calc."""
    return [(row["case"], _pairs(row["input"]), _pairs(row["expected"]))
            for row in _table("hand_score.tsv") if row["calc"] == calc]


def fraction(text: str) -> Fraction:
    return Fraction(text)


def full_grid():
    """(ccn, covered, total) for every case of the full grid."""
    for ccn in GRID_CCN:
        for total in GRID_TOTAL:
            for covered in range(total + 1):
                yield ccn, covered, total


def reduced_grid():
    """(ccn, covered, total) with covered/total in lowest terms, each fraction once."""
    for ccn in GRID_CCN:
        for total in GRID_TOTAL:
            for covered in range(total + 1):
                if Fraction(covered, total).denominator == total:
                    yield ccn, covered, total


def tie_table() -> dict[tuple[int, int, int, int], dict]:
    """hand_d5_ties.tsv keyed by (places, ccn, covered, total) in lowest terms."""
    return {(int(row["places"]), int(row["ccn"]), int(row["covered"]), int(row["total"])): row
            for row in _table("hand_d5_ties.tsv")}
