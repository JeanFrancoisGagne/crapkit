"""packet.rejudged: one stored row, judged again against a ceiling.

The shared-span half is the one a stored run cannot always answer. A row the run
judged `add-tests` or `split-lines` already says whether another function
declares its lines; a row it judged `ok` or `decompose` does not, and only that
row may cost a read of its file. The rows below score CRAP = ccn^2 x
(1 - cov)^3 + ccn by hand.
"""
from crapkit import packet
from crapkit.score import ScoredRow


def row(name: str, *, ccn: int = 2, crap: float = 6.0, remedy: str = "ok",
        flag: str = "untested", scope: str = "src", start: int = 30, end: int = 30,
        occurrence: int = 1) -> ScoredRow:
    return ScoredRow(scope, "src/app.py", name, start, end, ccn, ccn, ccn, 1, 0, 0,
                     0.0, flag, crap, remedy, 0, occurrence)


def refuse(path):
    raise AssertionError(f"the stored verdict answers this; nothing may read {path}")


def test_a_row_the_run_judged_split_lines_keeps_it_without_a_file_read():
    split = row("left( )", remedy="split-lines")

    assert packet.rejudged(split, 4, refuse).remedy == "split-lines"


def test_a_row_the_run_judged_add_tests_keeps_it_without_a_file_read():
    alone = row("lean( )", ccn=3, crap=4.125, remedy="add-tests", flag="measured")

    assert packet.rejudged(alone, 4, refuse).remedy == "add-tests"


def test_an_unchanged_verdict_hands_back_the_row_it_was_given():
    kept = row("mid( )", ccn=5, crap=5.025, remedy="ok", flag="measured")

    assert packet.rejudged(kept, 6, refuse) is kept


def test_a_neighbour_on_the_same_lines_makes_an_ok_row_split_lines():
    left, right = row("left( )"), row("right( )")

    assert packet.rejudged(left, 4, lambda path: [left, right]).remedy == "split-lines"


def test_a_row_alone_on_its_lines_is_told_to_add_tests():
    left = row("left( )")
    below = row("below( )", start=31, end=31)

    assert packet.rejudged(left, 4, lambda path: [left, below]).remedy == "add-tests"


def test_the_same_function_under_a_second_scope_is_not_a_neighbour():
    here, there = row("left( )"), row("left( )", scope="web")

    assert packet.rejudged(here, 4, lambda path: [here, there]).remedy == "add-tests"


def test_a_no_lane_row_is_never_told_to_split_lines():
    """Scoring leaves a row no artifact joins out of the shared-span check: its
    cov is 0 for want of a lane, not because two functions share the lines."""
    left, right = row("left( )", flag="no-lane"), row("right( )", flag="no-lane")

    assert packet.rejudged(left, 4, lambda path: [left, right]).remedy == "add-tests"


def test_a_no_lane_neighbour_does_not_make_the_span_shared():
    left, right = row("left( )"), row("right( )", flag="no-lane")

    assert packet.rejudged(left, 4, lambda path: [left, right]).remedy == "add-tests"
