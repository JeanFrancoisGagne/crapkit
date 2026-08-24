"""Headline grade: one letter summarizing over-target density, for humans scanning a digest."""
from crapkit.score import grade


def test_grade_boundaries():
    assert grade(0, 100) == "A+"
    assert grade(1, 100) == "A"  # under 2%
    assert grade(2, 100) == "B"  # 2% is no longer an A
    assert grade(4, 100) == "B"
    assert grade(5, 100) == "C"
    assert grade(19, 100) == "D"
    assert grade(20, 100) == "F"


def test_grade_of_an_empty_run_is_perfect():
    assert grade(0, 0) == "A+"
