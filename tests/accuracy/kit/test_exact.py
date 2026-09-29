"""kit.exact pinned to published values. Every row names where its number comes
from; none of them comes from crapkit."""
from decimal import Decimal
from fractions import Fraction
import math

import pytest

from accuracy.kit import exact

SAVOIA_2 = "Savoia, artima.com weblog thread 210575 (2007), the formula's two limits"
SAVOIA_3 = "Savoia, artima.com weblog thread 215899 (2007), crap4j threshold table"
PHPUNIT = ("phpunit/php-code-coverage@ee9fdcf1dd94 tests/tests/Node/CrapIndexTest.php "
           "and src/Node/CrapIndex.php")
README = "crapkit README.md, 'Grade and CRAP load'"
README_RISK = "crapkit README.md, 'Risk: what ranks the worklist'"
GOOGLE = "Lewis and Ou, 'Bug Prediction at Google', Google Engineering Tools blog (2011)"
THRESHOLD = 30  # crap4j: a method over 30 is CRAPpy (thread 215899)


@pytest.mark.parametrize("ccn, cov, expected, source", [
    (1, 1, 1, SAVOIA_2),
    (7, 1, 7, SAVOIA_2),
    (1, 0, 2, SAVOIA_2),
    (7, 0, 56, SAVOIA_2),
    (100, 0, 10_100, SAVOIA_3),
    (2, 0, 6, PHPUNIT),
    (3, 1, 3, PHPUNIT),
    (4, Fraction(1, 2), 6, PHPUNIT),
])
def test_crap_matches_published_values(ccn, cov, expected, source):
    assert exact.crap(ccn, cov) == expected, source


def _required_percent(ccn):
    """The least whole coverage percentage that keeps CRAP at or under 30."""
    return next((p for p in range(101) if exact.crap(ccn, Fraction(p, 100)) <= THRESHOLD), None)


@pytest.mark.parametrize("ccn, percent", [
    (1, 0), (5, 0), (10, 42), (20, 71), (25, 80), (30, 100), (31, None),
])
def test_crap4j_threshold_table(ccn, percent):
    assert _required_percent(ccn) == percent, SAVOIA_3


def test_the_crap4j_table_row_at_ccn_15_is_over_its_own_threshold():
    """The table prints 57% for ccn 15, but 225 * 0.43^3 + 15 is 32.889...
    The formula in the same article needs 60%: the row is the table's slip,
    and kit.exact follows the formula."""
    assert exact.crap(15, Fraction(57, 100)) == Fraction(32889075, 1000000)
    assert _required_percent(15) == 60


def test_ccn_25_at_80_percent_is_exactly_the_threshold():
    """Float arithmetic reads 29.999999999999996 here; the fraction reads 30."""
    assert exact.crap(25, Fraction(80, 100)) == THRESHOLD
    assert 25 * 25 * (1 - 0.8) ** 3 + 25 != THRESHOLD


@pytest.mark.parametrize("ccn, covered, total, text, source", [
    (4, 1, 2, "6.00", PHPUNIT),
    (1, 3, 4, "1.02", "hand: 1 + (1/4)^3 = 1.015625, past the 2 dp midpoint"),
])
def test_crap_from_counts_prints_as_phpunit_prints_below_95_percent(ccn, covered, total, text,
                                                                     source):
    assert exact.fixed(exact.crap_from_counts(ccn, covered, total), 2) == text, source


@pytest.mark.parametrize("value, places, expected", [
    (Fraction(5, 2), 0, "2"),
    (Fraction(7, 2), 0, "4"),
    (Fraction(-5, 2), 0, "-2"),
    (Fraction(1, 3), 4, "0.3333"),
    (Fraction(2, 3), 4, "0.6667"),
    (Fraction(123445, 10), 0, "12344"),
    (Fraction(123455, 10), 0, "12346"),
    (Fraction(10005, 100000), 4, "0.1000"),
    (Fraction(10015, 100000), 4, "0.1002"),
    (Fraction(10025, 100000), 4, "0.1002"),
    (Fraction(10035, 100000), 4, "0.1004"),
    (Fraction(100049, 1000000), 4, "0.1000"),
    (Fraction(100051, 1000000), 4, "0.1001"),
    (7, 2, "7.00"),
])
def test_half_even_rounds_the_exact_value(value, places, expected):
    """IEEE 754-2008 roundTiesToEven, applied to the real number."""
    assert exact.fixed(value, places) == expected


def test_half_even_of_a_float_is_pythons_round():
    """2.675 is stored as 2.67499999999999982236431605997495353221893310546875
    (Python docs, 'Floating-Point Arithmetic: Issues and Limitations')."""
    assert exact.half_even(2.675, 2) == Decimal("2.67")
    assert exact.half_even(Fraction(2675, 1000), 2) == Decimal("2.68")
    assert exact.half_even(0.125, 2) == Decimal("0.12")


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_nonfinite_float_has_no_rounding(bad):
    with pytest.raises(ValueError, match="has no exact value"):
        exact.half_even(bad, 4)


@pytest.mark.parametrize("over, total, letter", [
    (0, 100, "A+"), (1, 100, "A"), (1, 51, "A"), (2, 100, "B"), (1, 50, "B"),
    (4, 100, "B"), (5, 100, "C"), (9, 100, "C"), (10, 100, "D"), (19, 100, "D"),
    (20, 100, "F"), (1, 1, "F"), (0, 1, "A+"),
])
def test_grade_bands(over, total, letter):
    assert exact.grade(over, total) == letter, README


@pytest.mark.parametrize("args", [(1, 0), (-1, 5), (6, 5)])
def test_no_grade_without_a_share(args):
    with pytest.raises(ValueError, match="no grade"):
        exact.grade(*args)


def test_the_newest_commit_weighs_one_half():
    assert exact.recency_weight(2_000, 1_000, 2_000) == 0.5, README_RISK


def test_the_oldest_commit_weighs_one_over_one_plus_e_to_the_twelve():
    assert exact.recency_weight(1_000, 1_000, 2_000) == 1 / (1 + math.exp(12)), GOOGLE


def test_the_midpoint_weighs_one_over_one_plus_e_to_the_six():
    assert exact.recency_weight(1_500, 1_000, 2_000) == 1 / (1 + math.exp(6)), GOOGLE


def test_one_timestamp_counts_each_commit_once():
    assert exact.churn_weight([1_000, 1_000, 1_000], 1_000, 1_000) == 3.0, README_RISK


def test_a_file_weight_sums_its_commits():
    expected = math.fsum([0.5, 1 / (1 + math.exp(6)), 1 / (1 + math.exp(12))])

    assert exact.churn_weight([2_000, 1_500, 1_000], 1_000, 2_000) == expected


def test_a_commit_outside_the_window_is_refused():
    with pytest.raises(ValueError, match="outside"):
        exact.recency_weight(999, 1_000, 2_000)


@pytest.mark.parametrize("covered, total", [(0, 1), (1, 1)])
def test_a_one_line_function_has_a_ratio(covered, total):
    """A function of one measurable line is the smallest total there is."""
    assert exact.ratio(covered, total) == Fraction(covered, total)


@pytest.mark.parametrize("covered, total", [(0, 0), (3, 2), (-1, 2)])
def test_no_ratio_without_counts(covered, total):
    with pytest.raises(ValueError, match="no coverage ratio"):
        exact.ratio(covered, total)


@pytest.mark.parametrize("ccn, cov", [(0, 0), (1, Fraction(3, 2)), (1, -1)])
def test_crap_is_undefined_off_its_domain(ccn, cov):
    with pytest.raises(ValueError, match="CRAP is undefined"):
        exact.crap(ccn, cov)
