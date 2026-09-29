"""PHPUnit's CRAP index, transcribed from php-code-coverage.

Source: https://github.com/sebastianbergmann/php-code-coverage/blob/ee9fdcf1dd94a247cda6185981993486ab96aef7/src/Node/CrapIndex.php
(CrapIndex::asString). PHP floats are IEEE 754 doubles, so the arithmetic
below is the same double arithmetic PHP performs. Its published test,
tests/tests/Node/CrapIndexTest.php at the same commit, fixes four values:
(2, 0.0) -> '6', (3, 100.0) -> '3', (5, 95.0) -> '5', (4, 50.0) -> '6.00'.

Two places part from crapkit on purpose, both rulings rows: PHPUnit prints
ccn at 95 percent coverage and above (SM-PHPUNIT-95), and it formats with
PHP's sprintf('%01.2F'), whose rounding this transcription does not claim to
reproduce; callers compare away from 2 dp ties only.
"""
from __future__ import annotations


def crap_index(ccn: int, coverage_percent: float) -> str:
    """What CrapIndex(ccn, coverage_percent)->asString() returns."""
    if coverage_percent == 0.0:
        return str(ccn ** 2 + ccn)
    if coverage_percent >= 95:
        return str(ccn)
    value = ccn ** 2 * (1 - coverage_percent / 100) ** 3 + ccn
    return f"{value:.2f}"


def value(ccn: int, coverage_percent: float) -> float:
    """The unformatted double PHPUnit formats below 95 percent."""
    return ccn ** 2 * (1 - coverage_percent / 100) ** 3 + ccn
