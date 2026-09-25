"""What each small-corpus row should read, written from the README, never from crapkit's code.

doc: README.md:28-31 sha256=d1cf4bd000bc592b220e5b4d2998ba9de806fbbbb80dd95bd94124e5aa86ffdd
doc: README.md:819-844 sha256=8c51ef858c2a2bda303a91824906a1474a8e9b84ed9511071a1a5e59b2e3858f
doc: docs/lanes.md:226-236 sha256=93f5c29cfdc4878494770e55c646168de83496b67d10ed3878f89a330bca57a4

The inputs are the corpus's crapkit.toml (scopes, lanes, ceilings), the
counts table the recorded artifacts give (oracles/corpus_counts.py), and each
row's analysis columns (path, start, end, ccn), which other packets check.

- flag: `cc-only` when the scope sets coverage_optional; `no-lane` when no
  lane lists the scope; `untested` when the function shares its start line
  with another one, or is a Python def whose body sits on its def line
  (README, the split-lines row), or when the artifact holds no function at its
  start line; `measured` otherwise.
- cov: the counts' ratio when measured, else 0.
- crap: ccn^2 * (1 - cov)^3 + ccn, exactly, and ccn for cc-only.
- remedy: decompose when ccn is over the ceiling, ok when crap is at or under
  it, split-lines for a shared line or a one-line Python def, add-tests
  otherwise; cc-only rows read only ok or decompose.
- grade: the share of functions over their ceiling, in the README's bands.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import tomllib

from accuracy.kit import exact


@dataclass(frozen=True)
class Scope:
    name: str
    paths: tuple[str, ...]
    ceiling: int
    cc_only: bool
    has_lane: bool


@dataclass(frozen=True)
class Expected:
    flag: str
    cov: Fraction
    crap: Fraction
    remedy: str
    ceiling: int


def scopes(config: Path) -> dict[str, Scope]:
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    laned = {scope for lane in data.get("lane", []) for scope in lane["scopes"]}
    target = data.get("crapkit", {}).get("target", 6)
    return {table["name"]: Scope(table["name"], tuple(table["paths"]), table.get("target", target),
                                 bool(table.get("coverage_optional")), table["name"] in laned)
            for table in data["scope"]}


def floored(row: dict, shared: bool) -> bool:
    """Coverage cannot see this function: its line is shared, or it is a
    one-line Python def."""
    one_line_def = row["path"].endswith(".py") and int(row["start"]) == int(row["end"])
    return shared or one_line_def


def _flag(scope: Scope, found: bool, floor: bool) -> str:
    if scope.cc_only:
        return "cc-only"
    if not scope.has_lane:
        return "no-lane"
    return "untested" if floor or not found else "measured"


def remedy(ccn: int, crap: Fraction, ceiling: int, floor: bool) -> str:
    if ccn > ceiling:
        return "decompose"
    if crap <= ceiling:
        return "ok"
    return "split-lines" if floor else "add-tests"


def expected(row: dict, scope: Scope, counts, shared: bool) -> Expected:
    """`counts` is the one Counts at the row's start line, or None."""
    ccn = int(row["ccn"])
    floor = floored(row, shared)
    flag = _flag(scope, counts is not None, floor)
    cov = counts.ratio() if flag == "measured" else Fraction(0)
    crap = Fraction(ccn) if flag == "cc-only" else exact.crap(ccn, cov)
    return Expected(flag, cov, crap, remedy(ccn, crap, scope.ceiling, floor), scope.ceiling)


def grade(expectations: list[Expected]) -> str:
    over = sum(1 for item in expectations if item.crap > item.ceiling)
    return exact.grade(over, len(expectations))
