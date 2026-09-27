from calc.grade import curve, grade


def test_an_early_high_score_is_an_a():
    assert grade(95, 1, False, False) == "A"


def test_curve_lifts_to_the_floor():
    assert curve([40, 90], 50) == [50, 90]
