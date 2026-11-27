"""`ratchet_delta`: what one green verify did to the marks file, as three counts.

The OK line and the JSON receipt print these, so they have to come off the
entries themselves and not off a diff of the file's text: a mark that stayed put
is neither dropped nor tightened, however the file around it was rewritten.
With the fresh rows the update read, a mark that moved to the key of the
function a signature edit carried it to counts as rekeyed, not as dropped.
"""
import pytest

from crapkit.ratchet import RatchetDelta, RatchetEntry, ratchet_delta, update_ratchet
from crapkit.score import ScoredRow

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
    assert ratchet_delta([HELD], [HELD]) == (0, 0, 0)


def test_both_kinds_are_counted_in_one_pass():
    updated = [BETTER._replace(crap=12.0), HELD]

    assert ratchet_delta([PAID, BETTER, HELD], updated) == (1, 1, 0)


def test_no_marks_is_no_change():
    assert ratchet_delta([], []) == (0, 0, 0)


# --- the re-key: the probe and its neighbours ---------------------------------------

PATH = "calc/grade.py"
OLD, NEW = "classify( a , b )", "classify( a , b , c = None )"
PROBE = RatchetEntry(PATH, OLD, 7.0)
FIXED = RatchetEntry(PATH, "classify( a )", 9.0)


def row(name: str, crap: float, start: int = 1) -> ScoredRow:
    return ScoredRow("calc", PATH, name, start, start + 9, 7, 7, 7, 9, 3, 1, 0.0, "cc_only", crap,
                     "decompose")


# (case, prior marks, fresh rows, hold, the updated marks, (dropped, tightened, rekeyed))
REKEYS = [
    ("the probe re-keys at its value", [PROBE], [row(NEW, 7.0)], frozenset(),
     [RatchetEntry(PATH, NEW, 7.0)], (0, 0, 1)),
    ("a re-key plus a tighten", [PROBE], [row(NEW, 6.5)], frozenset(),
     [RatchetEntry(PATH, NEW, 6.5)], (0, 1, 1)),
    ("a re-key plus a fall under the ceiling", [PROBE], [row(NEW, 5.0)], frozenset(), [], (1, 0, 0)),
    ("a fixed classify( a ) beside a carried classify( a , b )", [FIXED, PROBE],
     [row("classify( a )", 4.0), row(NEW, 7.0, 20)], frozenset(), [RatchetEntry(PATH, NEW, 7.0)],
     (1, 0, 1)),
    ("a held mark stays under its old key", [PROBE], [row(NEW, 6.5)], frozenset({(PATH, OLD)}),
     [PROBE], (0, 0, 0)),
    ("twins carry nothing", [PROBE], [row(NEW, 6.5), row(NEW, 6.5, 20)], frozenset(), [PROBE],
     (0, 0, 0)),
]


@pytest.mark.parametrize("case, prior, fresh, hold, marks, counts", REKEYS, ids=[r[0] for r in REKEYS])
def test_the_update_re_keys_a_carried_mark_and_counts_it(case, prior, fresh, hold, marks, counts):
    updated = update_ratchet(prior, fresh, target=6, hold=hold)

    assert updated == marks
    assert ratchet_delta(prior, updated, fresh) == RatchetDelta(*counts)


def test_without_fresh_rows_a_re_key_reads_as_today_s_drop():
    updated = update_ratchet([PROBE], [row(NEW, 7.0)], target=6)

    assert ratchet_delta([PROBE], updated) == RatchetDelta(1, 0, 0)
