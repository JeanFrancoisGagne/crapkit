"""Function coverage ratio: branches, then statements, then called-or-not.

crapkit's FnCoverage.coverage and its coverage.py reader against the README's
definition in Fraction (model_score.coverage_ratio) and the hand rows.
"""
from __future__ import annotations

from fractions import Fraction

from hypothesis import given, strategies as st
import pytest

from accuracy.kit.settings import pure
from accuracy.score_model import cases, model_score, production

RATIO_ROWS = cases.hand("Function coverage ratio")


def _pair(text: str) -> tuple[int, int]:
    covered, total = text.split("/")
    return int(covered), int(total)


def crapkit_ratio(branches: tuple[int, int], statements: tuple[int, int], invoked: bool) -> float:
    fn = production.fn_coverage("f", 1, 9, invoked=invoked, branches=branches,
                                statements=statements)
    return fn.coverage


@pytest.mark.parametrize("given_,expected", [row[1:] for row in RATIO_ROWS],
                         ids=[row[0] for row in RATIO_ROWS])
def test_hand_rows(given_, expected):
    got = crapkit_ratio(_pair(given_["branches"]), _pair(given_["statements"]),
                        given_["invoked"] == "1")

    assert Fraction(got) == Fraction(float(cases.fraction(expected["cov"])))


def pairs(limit: int = 400):
    return st.integers(0, limit).flatmap(lambda total: st.tuples(st.integers(0, total),
                                                                 st.just(total)))


@given(pairs(), pairs(), st.booleans())
@pure
def test_ratio_matches_the_readme_definition(branches, statements, invoked):
    """One division either way, so the double is the correctly rounded model value."""
    want = model_score.coverage_ratio(branches, statements, invoked)

    assert crapkit_ratio(branches, statements, invoked) == float(want)


@given(pairs(), pairs(), st.booleans(), st.integers(1, 20))
@pure
def test_a_covered_branch_never_lowers_the_ratio(branches, statements, invoked, more):
    covered, total = branches
    before = crapkit_ratio(branches, statements, invoked)
    after = crapkit_ratio((covered + more, total + more), statements, invoked)

    assert 0.0 <= before <= 1.0
    assert after >= before or total == 0


def _summary(branches: tuple[int, int], statements: tuple[int, int]) -> dict:
    return {"covered_branches": branches[0], "num_branches": branches[1],
            "covered_lines": statements[0], "num_statements": statements[1]}


@given(pairs(), pairs(), st.integers(1, 500))
@pure
def test_the_coverage_py_reader_keeps_coverage_py_s_own_counts(branches, statements, start):
    """A coverage.py JSON function entry (format 3) read by crapkit scores the
    README ratio of the counts coverage.py wrote, and starts at its start_line."""
    lines = list(range(start, start + statements[1]))
    entry = {"summary": _summary(branches, statements), "start_line": start,
             "executed_lines": lines[:statements[0]], "missing_lines": lines[statements[0]:]}
    [fn] = production.load("coverage_py:_file_functions")({"functions": {"f": entry}})
    invoked = statements[0] > 0

    assert fn.start == start
    assert fn.coverage == float(model_score.coverage_ratio(branches, statements, invoked))
