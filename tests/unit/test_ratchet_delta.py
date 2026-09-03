"""`ratchet_delta`: what one green verify did to the marks file, as two counts.

The OK line and the JSON receipt print these, so they have to come off the
entries themselves and not off a diff of the file's text: a mark that stayed put
is neither dropped nor tightened, however the file around it was rewritten.
"""
from crapkit.ratchet import RatchetEntry, ratchet_delta

PAID = RatchetEntry("app/m.py", "pick( a , b , c )", 9.0)
BETTER = RatchetEntry("app/m.py", "route( a , b )", 20.0)
HELD = RatchetEntry("lib/x.py", "walk( t )", 30.0)


def test_a_mark_that_left_the_file_counts_as_dropped():
    delta = ratchet_delta([PAID, HELD], [HELD])

    assert (delta.dropped, delta.tightened) == (1, 0)


def test_a_mark_that_fell_counts_as_tightened():
    delta = ratchet_delta([BETTER, HELD], [BETTER._replace(crap=12.0), HELD])

    assert (delta.dropped, delta.tightened) == (0, 1)


def test_a_mark_that_stayed_put_counts_as_neither():
    assert ratchet_delta([HELD], [HELD]) == (0, 0)


def test_both_kinds_are_counted_in_one_pass():
    updated = [BETTER._replace(crap=12.0), HELD]

    assert ratchet_delta([PAID, BETTER, HELD], updated) == (1, 1)


def test_no_marks_is_no_change():
    assert ratchet_delta([], []) == (0, 0)
