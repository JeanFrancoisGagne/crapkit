"""est_uncovered_paths is round((1 - cov) * ccn), half to even, on the exact product.

cov reaches the budget as the double covered / total. (1 - 5/12) * 6 is 3.5
exactly, which rounds to 4, but the doubles give 3.4999999999999996, and
rounding that said 3. Over ccn 1 to 40 and totals up to 120 in lowest terms,
127 of the 820 exact halves landed on the wrong side that way.
"""
from fractions import Fraction
from itertools import product
from math import gcd

from crapkit.packet import budget
from crapkit.score import ScoredRow


def paths(ccn: int, covered: int, total: int) -> int:
    row = ScoredRow("src", "src/a.py", "f( )", 1, 9, ccn, ccn, ccn, 3, 1, 1, covered / total,
                    "measured", 0.0, "ok")
    return budget(row, 6)["est_uncovered_paths"]


def test_an_exact_half_the_doubles_put_below_it_rounds_to_even():
    assert (1 - 5 / 12) * 6 == 3.4999999999999996
    assert paths(6, 5, 12) == 4


def test_exact_halves_round_to_the_even_neighbour():
    assert paths(5, 1, 2) == 2
    assert paths(3, 1, 2) == 2
    assert paths(7, 1, 2) == 4


def is_exact_half(ccn: int, covered: int, total: int) -> bool:
    """covered/total is in lowest terms and (1 - covered/total) * ccn is a whole
    number and a half."""
    return gcd(covered, total) == 1 and 2 * (total - covered) * ccn % (2 * total) == total


def exact_halves() -> list[tuple[int, int, int]]:
    """(ccn, covered, total) over ccn 1 to 40 and totals up to 120, every exact half."""
    return [(ccn, covered, total) for ccn, total in product(range(1, 41), range(1, 121))
            for covered in range(total + 1) if is_exact_half(ccn, covered, total)]


def test_every_exact_half_on_the_grid_rounds_to_even():
    halves = exact_halves()
    wrong = [case for case in halves
             if paths(*case) != round(Fraction(case[2] - case[1], case[2]) * case[0])]

    assert len(halves) == 820
    assert wrong == []
