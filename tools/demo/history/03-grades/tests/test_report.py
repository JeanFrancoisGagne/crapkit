from calc.report import render, tally


def test_tally_counts_every_bucket():
    rows = [{"score": 10}, {"score": 50}, {"score": 95}]
    assert tally(rows, 40, 90) == {"low": 1, "mid": 1, "high": 1}


def test_render_clips_the_bar_at_width():
    assert render({"low": 5, "mid": 0, "high": 1}, 3) == " low ### 5\n mid  0\nhigh # 1"
