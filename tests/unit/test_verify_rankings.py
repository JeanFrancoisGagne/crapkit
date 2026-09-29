"""verify's lists, read in order: unmarked debt, and marks read at the 4 places they hold.

A mark is stored at 4 decimal places (ratchet.tsv), so a fresh score compares
with it rounded to 4 places, and equal scores at 4 places list by path.
"""
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow
from crapkit.verify import _ratchet_regressions, _within_mark, unmarked_over_ceiling


def row(path: str, crap: float, scope: str = "src", name: str = "f( )") -> ScoredRow:
    return ScoredRow(scope, path, name, 1, 9, 3, 3, 3, 5, 1, 1, 0.5, "measured", crap, "add-tests")


def test_unmarked_debt_lists_the_worst_first_equal_scores_by_path():
    """30.45 is worse than 30.4 at 4 places; 29.999999999999996 and 30.0 tie there."""
    fresh = [row("src/e.py", 30.4), row("src/d.py", 30.45), row("src/c.py", 30.0),
             row("src/b.py", 29.999999999999996), row("src/a.py", 40.0), row("src/f.py", 5.0)]

    listed = unmarked_over_ceiling(fresh, [], target=6)

    assert [r.path for r in listed] == ["src/a.py", "src/d.py", "src/e.py", "src/b.py", "src/c.py"]


def test_unmarked_debt_is_judged_against_the_scope_s_own_ceiling():
    fresh = [row("src/a.py", 10.0, scope="lib"), row("src/b.py", 10.0)]

    listed = unmarked_over_ceiling(fresh, [RatchetEntry("src/c.py", "f( )", 1.0)], target=6,
                                   scope_targets={"lib": 12})

    assert [r.path for r in listed] == ["src/b.py"]


def test_a_score_is_within_its_mark_only_at_or_under_it_at_4_places():
    marks = {("src/a.py", "f( )"): 12.0}

    assert [_within_mark(row("src/a.py", crap), ("src/a.py", "f( )"), marks)
            for crap in (12.00004, 12.3, 11.9)] == [True, False, True]


def test_a_mark_that_rose_reports_the_fresh_score_at_4_places():
    [regression] = _ratchet_regressions([row("src/a.py", 12.300049)],
                                        [RatchetEntry("src/a.py", "f( )", 12.0)], set())

    assert (regression.recorded, regression.fresh_crap) == (12.0, 12.3)
