"""A packet rejudges a one-line Python def to split-lines, as the coverage run does.

The coverage run floors a Python def written on one line: its only line is
the `def` statement, which runs at import, so coverage.py cannot show a call.
A packet rejudged a row the run judged `ok` against today's ceiling and asked
only whether another function shares its lines, so after a ceiling cut it told
that def to add tests, which cannot lower its score.
"""
from crapkit import packet
from crapkit.score import ScoredRow


def one_line(path: str = "src/app.py", flag: str = "untested") -> ScoredRow:
    """ccn 1 at cov 0 scores 1 * 1 * 1 + 1 = 2: ok at a ceiling of 6, over a ceiling of 1."""
    return ScoredRow("src", path, "one( x )", 30, 30, 1, 1, 1, 1, 0, 0,
                     0.0, flag, 2.0, "ok", 0, 1)


def refuse(path):
    raise AssertionError(f"a one-line def answers without a read of {path}")


def test_a_one_line_python_def_the_run_judged_ok_is_told_to_split_lines():
    assert packet.rejudged(one_line(), 1, refuse).remedy == "split-lines"


def test_a_one_line_typescript_function_is_told_to_add_tests():
    """istanbul counts calls per function, so its one-liners keep their number."""
    one = one_line(path="src/app.ts")

    assert packet.rejudged(one, 1, lambda path: [one]).remedy == "add-tests"


def test_a_one_line_python_def_with_no_lane_is_told_to_add_tests():
    one = one_line(flag="no-lane")

    assert packet.rejudged(one, 1, refuse).remedy == "add-tests"
