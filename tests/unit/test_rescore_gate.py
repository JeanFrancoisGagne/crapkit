"""`rescore --gate` decides on the pre-commit hook's policy, mid-session.

The hook gates on ccn against the file's scope ceiling and reads no coverage at
all, so the two selections worked by hand below are the whole contract: a fully
covered ccn-7 function is a violation, and a ccn-6 function at zero coverage
(CRAP 42) is not. Same ceilings the commit will use, hours earlier.

Parity is more than the policy: the hook judges the functions the diff touched,
and a ratchet mark is a recorded decision to carry a function as it stands. Both
filters run before the ceiling rule, or a repo with seeded debt gates red forever.
"""
from crapkit.cli import _ceiling_breaches, _unmarked_breaches
from crapkit.config import Config, Scope
from crapkit.hook import file_ceilings
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow
from crapkit.verify import touched_rows


def row(path: str, name: str, ccn: int, cov: float, crap: float, start: int = 10) -> ScoredRow:
    return ScoredRow("src", path, name, start, start + 5, ccn, ccn, ccn,
                     12, 2, 1, cov, "measured", crap, "decompose")


def test_coverage_never_saves_a_function_over_the_ceiling():
    rows = [row("src/a.py", "over( n )", 7, 1.0, 7.0)]

    breaches = _ceiling_breaches(rows, {"src/a.py": 6})

    assert [(b.path, b.ccn, b.cov, b.crap) for b in breaches] == [("src/a.py", 7, 1.0, 7.0)]


def test_a_ccn_at_the_ceiling_passes_however_bad_its_crap_is():
    rows = [row("src/a.py", "at_ceiling( n )", 6, 0.0, 42.0)]

    assert _ceiling_breaches(rows, {"src/a.py": 6}) == []


def test_breaches_are_ordered_worst_ccn_first_then_by_position():
    rows = [row("src/b.py", "mild( n )", 7, 1.0, 7.0, start=40),
            row("src/a.py", "worst( n )", 9, 1.0, 9.0, start=90),
            row("src/a.py", "mild( x )", 7, 0.5, 16.5, start=10)]

    breaches = _ceiling_breaches(rows, {"src/a.py": 6, "src/b.py": 6})

    assert [(b.path, b.start) for b in breaches] == [
        ("src/a.py", 90), ("src/a.py", 10), ("src/b.py", 40)]


def test_the_ceilings_come_from_the_hooks_own_per_scope_map():
    cfg = Config(target=6, scopes=(Scope("src", ("src",), ("python",)),
                                   Scope("legacy", ("legacy",), ("python",), target=10)))
    in_scope = {"src": ["src/a.py"], "legacy": ["legacy/old.py"]}

    ceilings = file_ceilings(cfg, in_scope, ["src/a.py", "legacy/old.py"])

    assert ceilings == {"src/a.py": 6, "legacy/old.py": 10}
    rows = [row("src/a.py", "eight( n )", 8, 1.0, 8.0),
            row("legacy/old.py", "eight( n )", 8, 1.0, 8.0)]
    assert [b.path for b in _ceiling_breaches(rows, ceilings)] == ["src/a.py"]


def test_only_functions_a_changed_span_overlaps_reach_the_ceiling_rule():
    rows = [row("src/a.py", "edited( n )", 9, 1.0, 9.0, start=10),
            row("src/a.py", "legacy( n )", 15, 0.4, 63.6, start=100)]

    assert [r.long_name for r in touched_rows(rows, {"src/a.py": [(12, 12)]})] == ["edited( n )"]


def test_a_file_with_no_changed_range_contributes_no_candidate():
    rows = [row("src/a.py", "legacy( n )", 15, 0.4, 63.6)]

    assert touched_rows(rows, {"src/b.py": [(1, 400)]}) == []


def test_a_change_at_the_last_line_of_a_function_still_selects_it():
    rows = [row("src/a.py", "edge( n )", 9, 1.0, 9.0, start=10)]  # spans 10..15

    assert len(touched_rows(rows, {"src/a.py": [(15, 15)]})) == 1


def test_a_breach_sitting_at_its_recorded_mark_is_carried_debt():
    breaches = _ceiling_breaches([row("src/a.py", "legacy( n )", 15, 0.4, 63.6)], {"src/a.py": 6})

    assert _unmarked_breaches(breaches, [RatchetEntry("src/a.py", "legacy( n )", 63.6)]) == []


def test_a_breach_past_its_recorded_mark_still_fires():
    breaches = _ceiling_breaches([row("src/a.py", "legacy( n )", 15, 0.4, 70.0)], {"src/a.py": 6})

    kept = _unmarked_breaches(breaches, [RatchetEntry("src/a.py", "legacy( n )", 63.6)])

    assert [b.crap for b in kept] == [70.0]


def test_a_mark_on_another_function_exempts_nothing():
    breaches = _ceiling_breaches([row("src/a.py", "fresh( n )", 9, 1.0, 9.0)], {"src/a.py": 6})

    kept = _unmarked_breaches(breaches, [RatchetEntry("src/a.py", "legacy( n )", 63.6)])

    assert [b.long_name for b in kept] == ["fresh( n )"]


def test_marks_compare_at_the_four_decimals_they_are_stored_at():
    breaches = _ceiling_breaches([row("src/a.py", "legacy( n )", 15, 0.4, 63.60001)],
                                 {"src/a.py": 6})

    assert _unmarked_breaches(breaches, [RatchetEntry("src/a.py", "legacy( n )", 63.6)]) == []
