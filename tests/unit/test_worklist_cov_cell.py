"""The cov cell on a printed worklist row.

A no-lane or cc-only row scores with a cov of 0.0 that no artifact measured;
`brief` says `cov not measured` and `rescore` prints `-` for it. The worklist
row printed `0%` for the same function, which reads as a measured, untested one.
"""
import pytest

from crapkit.cli.queue import _row_text
from crapkit.worklist import WorklistEntry


def entry(flag, cov, crap=8.0, remedy="decompose"):
    return WorklistEntry("src", "src/m.go", "F a int , b int", 3, 26, 8, 8, 20, 1, 1,
                         1.0, 8.0, flag=flag, remedy=remedy, crap=crap, cov=cov)


@pytest.mark.parametrize("flag", ["cc-only", "no-lane"])
def test_a_row_no_artifact_measured_prints_a_dash_for_its_cov(flag):
    row = _row_text(entry(flag, 0.0))

    assert "crap     8.0  cov    -    1c/1a" in row, row
    assert "0%" not in row, row


def test_an_untested_row_keeps_its_measured_zero():
    row = _row_text(entry("untested", 0.0, crap=72.0))

    assert "crap    72.0  cov   0%    1c/1a" in row, row


def test_a_measured_row_prints_its_percentage():
    row = _row_text(entry("measured", 0.75, crap=8.1))

    assert "cov  75%" in row, row


def test_an_inventory_only_row_prints_dashes_for_both_numbers():
    row = _row_text(entry(None, None, crap=None, remedy=None))

    assert "crap       -  cov    -    1c/1a" in row, row
