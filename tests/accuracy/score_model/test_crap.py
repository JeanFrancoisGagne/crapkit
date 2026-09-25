"""CRAP score: crapkit's crap() against the exact value, PHPUnit's CrapIndex and the
published worked examples.

crapkit computes in doubles; the expected side is exact (kit.exact, Fraction).
A double may stray a few units in the last place from the exact value, so a
float comparison allows ULPS of them (the full grid's worst case, measured on
Windows UCRT and Linux glibc alike). The strings a user reads, at 1, 2 and 4
decimals, must equal the exact value rounded half-even, except at the exact
ties hand_d5_ties.tsv lists (rulings D5).
"""
from __future__ import annotations

import ast
from fractions import Fraction
import math
from pathlib import Path

from hypothesis import given, strategies as st
import pytest

from accuracy.kit import exact, rulings, strategies
from accuracy.kit.settings import pure
from accuracy.score_model import cases, production
from accuracy.score_model.oracles import phpunit_crap_index as phpunit

ULPS = 8
PLACES = (1, 2, 4)
REPO = Path(__file__).resolve().parents[3]


def crapkit_crap(ccn: int, covered: int, total: int) -> float:
    return production.load("score:crap")(ccn, covered / total)


def within_ulps(value: float, expected: Fraction) -> bool:
    return _ulps_ok(value, expected.numerator, expected.denominator)


def _ulps_ok(value: float, num: int, den: int) -> bool:
    """|value - num/den| <= ULPS units in value's last place, in integers."""
    top, bottom = value.as_integer_ratio()
    unit_top, unit_bottom = math.ulp(value).as_integer_ratio()
    return abs(top * den - num * bottom) * unit_bottom <= ULPS * unit_top * bottom * den


def _ints(given_: dict, *names: str) -> list[int]:
    return [int(given_[name]) for name in names]


# --- hand rows ------------------------------------------------------------------------

CRAP_ROWS = [row for row in cases.hand("CRAP score") if "crap" in row[2]]
SAVOIA_ROWS = [row for row in cases.hand("CRAP score") if "min_percent" in row[2]]


@pytest.mark.parametrize("given_,expected", [row[1:] for row in CRAP_ROWS],
                         ids=[row[0] for row in CRAP_ROWS])
def test_hand_rows(given_, expected):
    want = cases.fraction(expected["crap"])
    got = crapkit_crap(*_ints(given_, "ccn", "covered", "total"))

    assert within_ulps(got, want), (got, want)
    assert f"{got:.4f}" == exact.fixed(want, 4)


def _min_percent(ccn: int, threshold: int) -> str:
    crap = production.load("score:crap")
    found = next((percent for percent in range(101) if crap(ccn, percent / 100) <= threshold),
                 None)
    return "none" if found is None else str(found)


@pytest.mark.parametrize("given_,expected", [row[1:] for row in SAVOIA_ROWS],
                         ids=[row[0] for row in SAVOIA_ROWS])
def test_savoia_coverage_needed_table(given_, expected):
    """The least whole percent that keeps a method at or under CRAP 30."""
    assert _min_percent(*_ints(given_, "ccn", "threshold")) == expected["min_percent"]


# --- the exact grid ----------------------------------------------------------------------

def exact_parts(ccn: int, covered: int, total: int) -> tuple[int, int]:
    """CRAP(ccn, covered/total) as (numerator, denominator): ccn^2 (t - c)^3 / t^3 + ccn
    over t^3. test_exact_parts_is_kit_exact holds it to kit.exact.crap."""
    return ccn * ccn * (total - covered) ** 3 + ccn * total ** 3, total ** 3


def _rounds_half_even(printed: str, num: int, den: int, places: int) -> bool:
    """Whether `printed` is num/den rounded half-even to `places` decimals."""
    digits = int(printed.replace(".", ""))
    twice_gap = 2 * abs(num * 10 ** places - digits * den)
    return twice_gap < den or (twice_gap == den and digits % 2 == 0)


def _string_problem(ties: dict, key: tuple, printed: str, parts: tuple) -> str | None:
    listed = ties.get(key)
    if listed is not None:
        return None if printed == listed["crapkit"] else f"{key}: {printed}, listed {listed['crapkit']}"
    if _rounds_half_even(printed, *parts, key[0]):
        return None
    return f"{key}: {printed}, exact {exact.fixed(Fraction(*parts), key[0])}"


def _case_problems(ccn: int, covered: int, total: int, ties: dict) -> list[str]:
    value = crapkit_crap(ccn, covered, total)
    parts = exact_parts(ccn, covered, total)
    cov = Fraction(covered, total)
    keys = [(places, ccn, cov.numerator, cov.denominator) for places in PLACES]
    found = [_string_problem(ties, key, f"{value:.{key[0]}f}", parts) for key in keys]
    found.append(None if _ulps_ok(value, *parts) else f"crap({ccn}, {cov}) = {value!r} strays")
    return list(filter(None, found))


def _grid_problems(grid) -> list[str]:
    ties = cases.tie_table()
    return [problem for case in grid for problem in _case_problems(*case, ties)]


def test_exact_parts_is_kit_exact():
    sample = list(cases.full_grid())[::97]

    assert [case for case in sample
            if Fraction(*exact_parts(*case)) != exact.crap(case[0], Fraction(*case[1:]))] == []


def test_reduced_grid_matches_the_exact_value():
    """175,480 cases: every double within ULPS of the exact CRAP (the full
    grid's worst is 4.13, at CRAP(37, 43/92), on UCRT and glibc alike), every 1,
    2 and 4 dp string the exact value rounded half-even, bar the listed ties."""
    assert _grid_problems(cases.reduced_grid())[:20] == []


@pytest.mark.nightly
def test_full_grid_matches_the_exact_value():
    assert _grid_problems(cases.full_grid())[:20] == []


def _tie_row_problem(key: tuple, row: dict) -> str | None:
    places, ccn, covered, total = key
    want = exact.crap(ccn, Fraction(covered, total))
    tie = want * 10 ** places * 2
    if not (tie.denominator == 1 and tie.numerator % 2 == 1):
        return f"{key} is not an exact {places} dp tie"
    if row["exact"] != exact.fixed(want, places) or row["crapkit"] == row["exact"]:
        return f"{key}: exact column {row['exact']}, crapkit column {row['crapkit']}"
    return None


def test_every_listed_tie_is_an_exact_tie_crapkit_rounds_the_other_way():
    rows = cases.tie_table().items()

    assert [problem for key, row in rows if (problem := _tie_row_problem(key, row))] == []


TIE_PINS = {"D5": (4, 4, 1, 40), "D5.2": (2, 5, 1, 10), "D5.1": (1, 20, 1, 20)}


@pytest.mark.parametrize("ruling", sorted(TIE_PINS))
def test_ties_keep_crapkit_s_binary_rounding(ruling):
    places, ccn, covered, total = TIE_PINS[ruling]
    value = crapkit_crap(ccn, covered, total)

    rulings.pin_ruling(ruling, crapkit=f"{value:.{places}f}",
                       oracle=exact.fixed(exact.crap(ccn, Fraction(covered, total)), places))


@rulings.applies("D13")
def test_crap_double_uses_no_platform_pow():
    """IEEE 754 basic operations are correctly rounded on every platform, and
    libm's pow is not: UCRT and glibc part in the last bit on 64 grid cases.
    The double must come from basic operations, so every platform prints it
    alike: ccn * ccn * (d * d * d) + ccn with d = 1 - cov."""
    crap = production.load("score:crap")
    differ = [(ccn, covered, total) for ccn, covered, total in cases.reduced_grid()
              if crap(ccn, covered / total) != _basic_ops(ccn, covered / total)]

    rulings.pin_ruling("D13", crapkit=str(differ == []), oracle="True")


def _basic_ops(ccn: int, cov: float) -> float:
    uncovered = 1.0 - cov
    return ccn * ccn * (uncovered * uncovered * uncovered) + ccn


# --- PHPUnit's CrapIndex --------------------------------------------------------------------

PHPUNIT_PUBLISHED = [((2, 0.0), "6"), ((3, 100.0), "3"), ((5, 95.0), "5"), ((4, 50.0), "6.00")]


@pytest.mark.parametrize("args,printed", PHPUNIT_PUBLISHED)
def test_the_phpunit_transcription_reproduces_its_published_tests(args, printed):
    assert phpunit.crap_index(*args) == printed


def _phpunit_problem(ccn: int, covered: int, total: int) -> str | None:
    percent = covered / total * 100
    num, den = exact_parts(ccn, covered, total)
    if percent >= 95 or num * 200 % (2 * den) == den:
        return None
    ours = crapkit_crap(ccn, covered, total)
    printed = phpunit.crap_index(ccn, percent)
    mine = str(int(ours)) if percent == 0.0 else f"{ours:.2f}"
    return None if mine == printed else f"crap({ccn}, {covered}/{total}): {mine}, PHPUnit {printed}"


def test_phpunit_crap_index_agrees_below_95_percent():
    """Away from 2 dp ties, where PHP's sprintf rounding is its own."""
    problems = (_phpunit_problem(*case) for case in cases.reduced_grid() if case[1] * 20 < case[2] * 19)

    assert [problem for problem in problems if problem][:20] == []


def test_phpunit_prints_ccn_from_95_percent():
    rulings.pin_ruling("SM-PHPUNIT-95", crapkit=f"{crapkit_crap(5, 95, 100):.6f}",
                       oracle=phpunit.crap_index(5, 95.0))


# --- properties -------------------------------------------------------------------------------

@given(strategies.crap_case())
@pure
def test_crap_bounds_and_ends(case):
    value = crapkit_crap(case.ccn, case.covered, case.total)

    assert case.ccn <= value <= case.ccn * case.ccn + case.ccn
    assert within_ulps(value, case.crap)
    assert crapkit_crap(case.ccn, 1, 1) == case.ccn
    assert crapkit_crap(case.ccn, 0, 1) == case.ccn * case.ccn + case.ccn


@given(strategies.ccn(), strategies.counts(), st.integers(0, 50))
@pure
def test_crap_falls_as_coverage_rises_and_rises_with_ccn(ccn, pair, extra):
    covered, total = pair
    more = min(total, covered + extra)

    assert crapkit_crap(ccn, more, total) <= crapkit_crap(ccn, covered, total)
    assert crapkit_crap(ccn + 1, covered, total) > crapkit_crap(ccn, covered, total)


# --- the unit and e2e files take no expected value from the formula --------------------------

FORMULA_FILES = ["tests/unit/test_score.py", "tests/unit/test_digest.py",
                 "tests/unit/test_ratchet.py", "tests/unit/test_ratchet_move.py",
                 "tests/unit/test_ratchet_seed_prune.py",
                 "tests/unit/test_ratchet_tighten_damping.py", "tests/unit/test_sarif.py",
                 "tests/unit/test_store.py", "tests/unit/test_verify.py",
                 "tests/unit/test_verify_dirty.py", "tests/unit/test_verify_gate_marks.py",
                 "tests/unit/test_queue_admission.py", "tests/e2e/test_inventory_e2e.py"]


def _cube(node: ast.AST) -> bool:
    return (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow)
            and isinstance(node.right, ast.Constant) and node.right.value == 3)


def _crapkit_crap(func: ast.AST) -> bool:
    """`crap(...)` or `x.crap(...)`, except kit.exact's `exact.crap(...)`."""
    if isinstance(func, ast.Attribute):
        return func.attr == "crap" and getattr(func.value, "id", None) != "exact"
    return getattr(func, "id", None) == "crap"


def _calls_crap(node: ast.AST) -> bool:
    return any(_crapkit_crap(item.func) for item in ast.walk(node) if isinstance(item, ast.Call))


def _asserts_crap(node: ast.AST) -> bool:
    return isinstance(node, ast.Assert) and _calls_crap(node.test)


def _formula_uses(path: str, root: Path = REPO) -> list[str]:
    nodes = list(ast.walk(ast.parse((root / path).read_bytes())))
    cubes = [f"{path}:{node.lineno} ** 3" for node in filter(_cube, nodes)]
    return cubes + [f"{path}:{node.lineno} crap( in an assert" for node in filter(_asserts_crap, nodes)]


def test_unit_files_take_no_expected_value_from_the_formula():
    """An expected CRAP comes from kit.exact or a literal: a test that rebuilds
    the formula, or asks crapkit's crap() for it, agrees with any bug in it."""
    assert [use for path in FORMULA_FILES for use in _formula_uses(path)] == []


def test_the_formula_rule_finds_a_planted_use(tmp_path):
    planted = tmp_path / "t.py"
    planted.write_text("\n".join([
        "from accuracy.kit import exact", "from crapkit.score import crap", "def test_a():",
        "    assert crap(1, 0.0) == 2", "    x = (1 - 0.5) ** 3",
        "    assert exact.crap(1, 0) == 2", ""]), encoding="utf-8")

    assert _formula_uses("t.py", tmp_path) == ["t.py:5 ** 3", "t.py:4 crap( in an assert"]
