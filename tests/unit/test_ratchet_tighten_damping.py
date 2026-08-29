"""Tightening a mark claims the code improved. The same commit measured twice cannot have.

Reported against a Python repo whose coverage attribution races: one function
measured CRAP 20.0 on run 9 and 72.0 on run 10, same sha, same bytes. A mark
seeded at the pessimistic value is pulled down by the lucky run and fails the
unlucky one, so an unstable input became an unstable gate. These pin the damping
seam: a measurement that jumped between two runs of one commit holds its mark.
"""
import pytest

from crapkit.ratchet import RatchetEntry, unstable_marks, update_ratchet
from crapkit.score import ScoredRow

JUDGE = "app/hook.py"
NAME = "_judge( raw )"


def scored(crap: float, path: str = JUDGE, name: str = NAME, ccn: int = 8) -> ScoredRow:
    """One scored row at a stated CRAP. The reporter's function is ccn 8, and its
    two measurements differ only in the coverage that fed them."""
    cov = 1.0 - ((crap - ccn) / (ccn * ccn)) ** (1 / 3)
    return ScoredRow("src", path, name, 1, 40, ccn, ccn, ccn, 20, 1, 2,
                     cov, "measured", crap, "decompose")


def held(marks, fresh, previous, *, max_jump=2.0):
    """The marks after one damped tighten, keyed for lookup."""
    refusals = unstable_marks(marks, fresh, previous, max_jump=max_jump)
    updated = update_ratchet(marks, fresh, target=6,
                             hold=frozenset((r.path, r.long_name) for r in refusals))
    return refusals, {(e.path, e.long_name): e.crap for e in updated}


def test_a_measurement_that_jumped_on_one_commit_does_not_tighten_the_mark():
    """The reporter's numbers: 20.0 on one run of bb83d64, 72.0 on the next. A
    mark of 90.0 would tighten to 72.0 and re-arm the next 20.0-vs-72.0 flip."""
    refusals, marks = held([RatchetEntry(JUDGE, NAME, 90.0)], [scored(72.0)],
                           {(JUDGE, NAME): 20.0})

    assert marks[(JUDGE, NAME)] == 90.0, "a bouncing measurement may not move a mark"
    assert [(r.previous, r.fresh) for r in refusals] == [(20.0, 72.0)]


def test_the_lucky_run_cannot_pull_the_mark_down_either():
    """The oscillator as reported: the pessimistic 72.0 is the mark, a lucky
    20.0 tightens it, and the next unlucky run fails at exit 7."""
    refusals, marks = held([RatchetEntry(JUDGE, NAME, 72.0)], [scored(20.0)],
                           {(JUDGE, NAME): 72.0})

    assert marks[(JUDGE, NAME)] == 72.0
    assert [(r.previous, r.fresh) for r in refusals] == [(72.0, 20.0)]


def test_a_stable_improvement_still_tightens():
    """Damping is about disagreement between two runs of one commit, not about
    the size of the gain. Two runs that agree let the whole improvement land."""
    refusals, marks = held([RatchetEntry(JUDGE, NAME, 90.0)], [scored(20.0)],
                           {(JUDGE, NAME): 20.0})

    assert refusals == []
    assert marks[(JUDGE, NAME)] == 20.0


def test_a_move_inside_the_factor_still_tightens():
    """20.0 -> 39.0 is under 2x: real work moves scores by less than the race does."""
    refusals, marks = held([RatchetEntry(JUDGE, NAME, 90.0)], [scored(39.0)],
                           {(JUDGE, NAME): 20.0})

    assert refusals == []
    assert marks[(JUDGE, NAME)] == 39.0


def test_the_factor_is_the_edge_and_the_edge_still_tightens():
    """Exactly 2x is not more than 2x. A boundary that refused would make the
    documented default one notch stricter than the number says."""
    refusals, _ = held([RatchetEntry(JUDGE, NAME, 90.0)], [scored(40.0)],
                       {(JUDGE, NAME): 20.0})
    just_past, _ = held([RatchetEntry(JUDGE, NAME, 90.0)], [scored(40.1)],
                        {(JUDGE, NAME): 20.0})

    assert refusals == []
    assert len(just_past) == 1


def test_a_stricter_factor_refuses_a_smaller_move():
    refusals, marks = held([RatchetEntry(JUDGE, NAME, 90.0)], [scored(39.0)],
                           {(JUDGE, NAME): 20.0}, max_jump=1.5)

    assert len(refusals) == 1
    assert marks[(JUDGE, NAME)] == 90.0


def test_a_function_the_earlier_run_never_scored_is_not_damped():
    """No second measurement, no disagreement. A first-ever run of a commit
    tightens exactly as it did before."""
    refusals, marks = held([RatchetEntry(JUDGE, NAME, 90.0)], [scored(20.0)], {})

    assert refusals == []
    assert marks[(JUDGE, NAME)] == 20.0


def test_an_unmarked_function_is_never_a_refusal():
    """Only a mark can be tightened, so only a marked function can be held. An
    unstable score on unmarked code is the gate's business, not the ratchet's."""
    assert unstable_marks([], [scored(72.0)], {(JUDGE, NAME): 20.0}, max_jump=2.0) == []


def test_a_held_mark_is_not_dropped_by_a_run_that_reads_under_the_ceiling():
    """The drop is the deepest tighten there is: the row leaves the file. A
    measurement that bounced to 4.0 may not repay debt it did not repay."""
    _, marks = held([RatchetEntry(JUDGE, NAME, 72.0)], [scored(4.0, ccn=2)],
                    {(JUDGE, NAME): 72.0})

    assert marks[(JUDGE, NAME)] == 72.0, "a repayment nobody can reproduce is not a repayment"


def test_the_worst_twin_is_what_the_jump_is_measured_against():
    """Twins share (path, long_name) and the worst one owns the mark, so the
    fresh side of the comparison has to be the same twin the mark answers to."""
    fresh = [scored(20.0), scored(72.0)]

    (refusal,) = unstable_marks([RatchetEntry(JUDGE, NAME, 90.0)], fresh,
                                {(JUDGE, NAME): 20.0}, max_jump=2.0)

    assert (refusal.previous, refusal.fresh) == (20.0, 72.0)


def test_the_refusal_line_names_the_function_and_both_values():
    from crapkit.cli import _no_tighten_line
    from crapkit.ratchet import TightenRefusal

    line = _no_tighten_line(TightenRefusal(JUDGE, NAME, 20.0, 72.0))

    assert line == (f"  NO TIGHTEN  {JUDGE}  {NAME}: measurement moved 20.0 -> 72.0 "
                    "on the same commit; not tightening")


def test_marks_are_compared_at_the_precision_they_are_stored_at():
    """cov is a division, so a fresh score carries long decimals. Comparing the
    unrounded value against a 4dp mark is what wedges an unchanged tree."""
    from crapkit.score import crap

    row = scored(crap(10, 2 / 3))
    (refusal,) = unstable_marks([RatchetEntry(JUDGE, NAME, 90.0)], [row],
                                {(JUDGE, NAME): 3.0}, max_jump=2.0)

    assert refusal.fresh == 13.7037


@pytest.mark.parametrize("value", [0.5, 0, "2", True])
def test_a_factor_under_one_is_a_config_error(value):
    """Below 1 refuses every tighten, including one where nothing moved: the
    ratchet would stop falling and nobody would be told why."""
    from crapkit.config import load_config_text
    from crapkit.errors import ConfigError

    text = ('[crapkit]\ntarget = 6\ntighten_max_jump = ' + repr(value).lower()
            + '\n\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n')
    with pytest.raises(ConfigError, match="tighten_max_jump"):
        load_config_text(text)


def test_the_default_factor_is_two():
    from crapkit.config import Config, load_config_text

    text = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
            'languages = ["python"]\n')

    assert load_config_text(text).tighten_max_jump == 2.0
    assert Config().tighten_max_jump == 2.0


def test_the_factor_is_read_off_the_config():
    from crapkit.config import load_config_text

    text = ('[crapkit]\ntarget = 6\ntighten_max_jump = 5\n\n[[scope]]\nname = "src"\n'
            'paths = ["src"]\nlanguages = ["python"]\n')

    assert load_config_text(text).tighten_max_jump == 5.0
