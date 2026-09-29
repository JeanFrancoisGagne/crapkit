"""Expected values for the cross-surface check, and the rule each surface prints them by.

The expected value of a function comes from model_corpus over the counts the
recorded artifacts give (oracles/corpus_counts.py), computed exactly with
kit.exact. A surface prints it one of four ways, and each way has its rule:

- FLOAT: the JSON number or TSV cell itself. cov must equal the exact ratio
  rounded once to a float; crap must lie within 2 ulp of the exact value
  (crapkit computes it in floating point, and the plan allows 2 ulp).
- FIXED1: one decimal, the exact value rounded half-even (worklist text, the
  report, SARIF and annotation messages, the PR comment).
- PERCENT: a whole percent of cov, the exact value times 100 rounded half-even.
- TEXT: a label (flag, remedy) compared as written; INT: an integer.

A printed string that differs from the half-even string only because the exact
value is a tie at that precision is a known definition (ruling D5 keeps binary
half-even rounding at ties): tie() says when that is the case.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
import math
from pathlib import Path

from accuracy.corpus_goldens import model_corpus
from accuracy.corpus_goldens.oracles import corpus_counts
from accuracy.kit import exact, surfaces

SMALL = Path(__file__).resolve().parent / "small"
FLOAT, FIXED1, PERCENT, TEXT, INT = "float", "fixed1", "percent", "text", "int"
MAX_ULPS = 2


@dataclass(frozen=True)
class Mismatch:
    surface: str
    key: tuple
    field: str
    printed: object
    expected: str
    tie: bool

    def __str__(self) -> str:
        tie = " (a tie at this precision)" if self.tie else ""
        return (f"{self.surface} {self.key[0]}:{self.key[1]} {self.field}: printed "
                f"{self.printed!r}, expected {self.expected}{tie}")


def per_row(rows: list[dict], config: Path = SMALL / "crapkit.toml",
            recorded: Path = SMALL / "recorded") -> list[model_corpus.Expected]:
    """model_corpus.Expected for each row, in row order. A row carries path,
    long_name, start, end, ccn and scope."""
    scopes = model_corpus.scopes(config)
    table = corpus_counts.table(recorded)
    starts = Counter((row["path"], int(row["start"])) for row in rows)
    return [model_corpus.expected(row, scopes[row["scope"]], _single(table, row),
                                  starts[(row["path"], int(row["start"]))] > 1)
            for row in rows]


def expectations(rows: list[dict], **where) -> dict:
    """{(path, handle): Expected}. kit.surfaces.Roster gives every function that
    shares a start line one key, so those rows collapse to one entry here as
    they do in every parsed surface; shared_rows() checks them without keys."""
    roster = surfaces.Roster(rows)
    return {roster.key(row["path"], row["long_name"], row["start"]): item
            for row, item in zip(rows, per_row(rows, **where))}


def shared_starts(rows: list[dict]) -> list[dict]:
    """The rows whose start line another row of the same file shares."""
    starts = Counter((row["path"], int(row["start"])) for row in rows)
    return [row for row in rows if starts[(row["path"], int(row["start"]))] > 1]


def _single(table: dict, row: dict):
    found = table.get((row["path"], int(row["start"])), [])
    return found[0] if len(found) == 1 and not found[0].shared else None


def _value(expected: model_corpus.Expected, field: str):
    return {"crap": expected.crap, "cov": expected.cov, "flag": expected.flag,
            "remedy": expected.remedy}[field]


def _near(printed: float, value: Fraction) -> bool:
    target = float(value)
    return abs(printed - target) <= MAX_ULPS * math.ulp(target) and \
        abs(Fraction(printed) - value) <= MAX_ULPS * Fraction(math.ulp(target))


def _float_ok(field: str, printed, value: Fraction) -> bool:
    number = float(printed)
    return number == float(value) if field == "cov" else _near(number, value)


def _string(rule: str, value) -> str:
    if rule == FIXED1:
        return exact.fixed(value, 1)
    if rule == PERCENT:
        return exact.fixed(value * 100, 0)
    return str(value)


def tie(rule: str, value) -> bool:
    """Whether the exact value sits exactly halfway at the printed precision."""
    scale = {FIXED1: 10, PERCENT: 100}.get(rule)
    return scale is not None and (Fraction(value) * scale).denominator == 2


def check(surface: str, key: tuple, field: str, rule: str, printed, expected) -> Mismatch | None:
    value = _value(expected, field)
    if rule == FLOAT:
        ok = _float_ok(field, printed, value)
        return None if ok else Mismatch(surface, key, field, printed, repr(float(value)), False)
    wanted = _string(rule, value)
    if str(printed) == wanted:
        return None
    return Mismatch(surface, key, field, printed, wanted, tie(rule, value))


def _printed(fields: dict, rules: dict):
    """(field, rule, printed value) for each ruled field the surface printed."""
    return [(field, rule, fields[field]) for field, rule in rules.items()
            if fields.get(field) not in (None, "")]


def _pairs(view: dict, rules: dict, expected: dict):
    return [(key, field, rule, value) for key, fields in view.items() if key in expected
            for field, rule, value in _printed(fields, rules)]


def compare(surface: str, view: dict, rules: dict, expected: dict) -> tuple[list, int]:
    """(mismatches, fields checked) for one parsed surface."""
    pairs = _pairs(view, rules, expected)
    found = [check(surface, key, field, rule, value, expected[key])
             for key, field, rule, value in pairs]
    return [item for item in found if item], len(pairs)


# --- run and scope totals -------------------------------------------------------------------

def _printed_crap(row: dict) -> Fraction:
    cov = Fraction(float(row["cov"])).limit_denominator(1000)
    return exact.crap(int(row["ccn"]), cov)


def row_totals(scored: str, expected: dict, printed_path: str) -> list[tuple[str, int, Fraction]]:
    """(scope, ceiling, exact CRAP) per scored.tsv row: the model's value, except
    the rows of `printed_path` (a file a strict xfail covers), which count at
    the coverage crapkit printed for them."""
    rows = surfaces.read_tsv(scored)[1]
    roster = surfaces.Roster(rows)
    found = []
    for row in rows:
        item = expected[roster.key(row["path"], row["long_name"], row["start"])]
        crap = _printed_crap(row) if row["path"] == printed_path else item.crap
        found.append((row["scope"], item.ceiling, crap))
    return found


def _of_scope(totals: list, scope: str | None) -> list[tuple[int, Fraction]]:
    """(ceiling, crap) for the scope's rows, or for every row when scope is None."""
    return [(ceiling, crap) for name, ceiling, crap in totals if scope in (None, name)]


def scope_totals(totals: list, scope: str | None) -> tuple[int, int, str, str]:
    """(functions, over target, CRAP load at 2 dp, grade) over a scope's rows."""
    mine = _of_scope(totals, scope)
    over = len([crap for ceiling, crap in mine if crap > ceiling])
    load = sum(crap for _, crap in mine)
    return len(mine), over, exact.fixed(load, 2), exact.grade(over, len(mine))


def average(totals: list) -> str:
    """The mean CRAP over every row, at 4 dp half-even."""
    return exact.fixed(sum(crap for *_, crap in totals) / len(totals), 4)
