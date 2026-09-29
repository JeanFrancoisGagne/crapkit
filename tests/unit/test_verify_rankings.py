"""verify's lists, read in order: unmarked debt, and marks read at the 4 places they hold.

A mark is stored at 4 decimal places (ratchet.tsv), so a fresh score compares
with it rounded to 4 places, and equal scores at 4 places list by path.
"""
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow
from crapkit.verify import (UncoveredViolation, _ratchet_regressions, _within_mark, dirty_failure_ids,
                            evaluate, parse_baseline_tsv, unmarked_over_ceiling, with_diff_coverage)


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


def test_marks_that_rose_list_the_largest_rise_first_at_4_places():
    """b rose 1.4 and a 1.2: rounded to whole numbers both rose 1, and a would lead."""
    marks = [RatchetEntry("src/a.py", "f( )", 10.0), RatchetEntry("src/b.py", "f( )", 10.0)]

    regressions = _ratchet_regressions([row("src/a.py", 11.2), row("src/b.py", 11.4)], marks, set())

    assert [r.path for r in regressions] == ["src/b.py", "src/a.py"]


def test_gate_violations_list_the_worst_first_with_each_row_s_remedy():
    fresh = [row("src/a.py", 20.0), row("src/b.py", 40.0)._replace(remedy="decompose")]

    verdict = evaluate(fresh=fresh, changed_ranges={"src/a.py": [(1, 9)], "src/b.py": [(1, 9)]},
                       ratchet=[], baseline_failures=set(), fresh_failures=set(), target=6)

    assert [(v.path, v.remedy) for v in verdict.gate_violations] == [
        ("src/b.py", "decompose"), ("src/a.py", "add-tests")]


def test_an_edited_typescript_file_dirties_no_python_module_of_its_stem():
    """`web/a.ts` is not `web/a.py`: the failure in module web.a stays committed."""
    assert dirty_failure_ids(["web.a::test_x", "web/a.ts::t"], {"web/a.ts"}) == ["web/a.ts::t"]


def test_a_breached_changed_line_ceiling_keeps_each_line_and_whether_it_is_dirty():
    base = evaluate(fresh=[], changed_ranges={}, ratchet=[], baseline_failures=set(),
                    fresh_failures=set(), target=6)

    verdict = with_diff_coverage(base, [("src/a.py", 3), ("src/b.py", 8)], 1, {"src/b.py"})

    assert (verdict.ok, verdict.uncovered_violations) == (
        False, (UncoveredViolation("src/a.py", 3, False), UncoveredViolation("src/b.py", 8, True)))


def test_a_baseline_stamp_value_keeps_every_equals_sign_after_the_first():
    parsed = parse_baseline_tsv('# commit=abc run_kind=verify results={"u":{"failures":["t::a=b"]}}\n')

    assert (parsed.commit, parsed.lanes) == ("abc", {"u": {"failures": ["t::a=b"]}})
