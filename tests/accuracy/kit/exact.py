"""Expected values computed exactly, from published definitions. No crapkit.

- CRAP = ccn^2 * (1 - cov)^3 + ccn (Savoia, artima.com weblog thread 210575,
  2007), in Fraction arithmetic from integer counts, so no expected value
  inherits a floating-point rounding.
- half_even rounds the exact value of an int, a Fraction or a float (a float's
  exact binary value) to any number of places, ties to even. A float's result
  is what Python's round() gives; a Fraction's is the rounding of the real
  number, which is where the two readings of a 4 dp tie part.
- grade reads the README's band table in Fraction: A+ at zero, then A, B, C, D
  under 2, 5, 10 and 20 percent, F from 20 percent up.
- recency_weight is the logistic from Lewis and Ou, "Bug Prediction at Google"
  (Google Engineering Tools blog, 2011): 1 / (1 + e^(-12t + 12)) for t the
  commit's position between the oldest (0) and the newest (1) commit. The
  README fixes the newest commit's weight at 0.5 and a log with one timestamp
  at 1 per commit. churn_weight sums a file's weights with math.fsum.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
import math

Number = int | Fraction | float

_BANDS = ((Fraction(2, 100), "A"), (Fraction(5, 100), "B"),
          (Fraction(10, 100), "C"), (Fraction(20, 100), "D"))


def ratio(covered: int, total: int) -> Fraction:
    """Covered over total as an exact fraction in [0, 1]."""
    if total <= 0 or not 0 <= covered <= total:
        raise ValueError(f"no coverage ratio for {covered} of {total}")
    return Fraction(covered, total)


def crap(ccn: int, cov: Fraction | int) -> Fraction:
    cov = Fraction(cov)
    if ccn < 1 or not 0 <= cov <= 1:
        raise ValueError(f"CRAP is undefined at ccn {ccn}, cov {cov}")
    uncovered = 1 - cov
    return ccn * ccn * uncovered * uncovered * uncovered + ccn


def crap_from_counts(ccn: int, covered: int, total: int) -> Fraction:
    return crap(ccn, ratio(covered, total))


def _exact(value: Number) -> Fraction:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{value} has no exact value")
    return Fraction(value)


def half_even(value: Number, places: int) -> Decimal:
    """The exact value rounded to `places` decimals, ties to the even digit."""
    scaled = _exact(value) * 10 ** places
    whole = math.floor(scaled)
    rest = scaled - whole
    if rest > Fraction(1, 2) or (rest == Fraction(1, 2) and whole % 2 == 1):
        whole += 1
    return Decimal(whole).scaleb(-places)


def fixed(value: Number, places: int) -> str:
    """half_even written with exactly `places` decimals, as a report prints it."""
    return f"{half_even(value, places):.{places}f}"


def _share(over: int, total: int) -> Fraction:
    if total <= 0 or not 0 <= over <= total:
        raise ValueError(f"no grade for {over} over target of {total}")
    return Fraction(over, total)


def grade(over: int, total: int) -> str:
    share = _share(over, total)
    if share == 0:
        return "A+"
    return next((letter for bound, letter in _BANDS if share < bound), "F")


def recency_weight(stamp: int, oldest: int, newest: int) -> float:
    if not oldest <= stamp <= newest:
        raise ValueError(f"commit time {stamp} lies outside [{oldest}, {newest}]")
    if newest == oldest:
        return 1.0
    t = Fraction(stamp - oldest, newest - oldest)
    return 1.0 / (1.0 + math.exp(float(-12 * t + 12)))


def churn_weight(stamps: list[int], oldest: int, newest: int) -> float:
    """One file's weight: its commits' recency weights summed exactly once."""
    return math.fsum(recency_weight(stamp, oldest, newest) for stamp in stamps)
