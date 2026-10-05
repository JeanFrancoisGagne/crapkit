"""`rescore --gate` decides on the pre-commit hook's policy, mid-session.

The hook gates on ccn against the file's scope ceiling and reads no coverage at
all, so the two selections worked by hand below are the whole contract: a fully
covered ccn-7 function is a violation, and a ccn-6 function at zero coverage
(CRAP 42) is not. Same ceilings the commit will use, hours earlier.

Parity is more than the policy: the hook judges the functions the diff touched,
and a ratchet mark is a recorded decision to carry a function as it stands. Both
filters run before the ceiling rule, or a repo with seeded debt gates red forever.
Each case runs the rescore adapter's own path: its changed files, the gate
module's judge, and the breaches it fails on.
"""
from crapkit.cli.scoring import _gate_breaches, _gate_changes
from crapkit.config import Config, Scope
from crapkit.gate import judge
from crapkit.hook import file_ceilings
from crapkit.keys import MarkIndex
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow

CFG = Config(target=6, scopes=(Scope("src", ("src",), ("python",)),
                               Scope("legacy", ("legacy",), ("python",), target=10)))


def row(path: str, name: str, ccn: int, cov: float, crap: float, start: int = 10,
        scope: str = "src") -> ScoredRow:
    return ScoredRow(scope, path, name, start, start + 5, ccn, ccn, ccn,
                     12, 2, 1, cov, "measured", crap, "decompose")


def gated(rows: list, marks=(), ranges: dict | None = None) -> list:
    """The breaches rescore --gate fails on. With no `ranges` every file is
    untracked, so the gate takes each whole."""
    untracked = {r.path for r in rows} if ranges is None else set()
    changes = _gate_changes(rows, ranges or {}, untracked, {})
    return _gate_breaches(judge(changes, CFG.ceiling_of, lambda: MarkIndex(marks)), rows)


def test_coverage_never_saves_a_function_over_the_ceiling():
    rows = [row("src/a.py", "over( n )", 7, 1.0, 7.0)]

    breaches = gated(rows)

    assert [(b.path, b.ccn, b.cov, b.crap) for b in breaches] == [("src/a.py", 7, 1.0, 7.0)]


def test_a_ccn_at_the_ceiling_passes_however_bad_its_crap_is():
    rows = [row("src/a.py", "at_ceiling( n )", 6, 0.0, 42.0)]

    assert gated(rows) == []


def test_breaches_are_ordered_worst_ccn_first_then_by_position():
    rows = [row("src/b.py", "mild( n )", 7, 1.0, 7.0, start=40),
            row("src/a.py", "worst( n )", 9, 1.0, 9.0, start=90),
            row("src/a.py", "mild( x )", 7, 0.5, 16.5, start=10)]

    breaches = gated(rows)

    assert [(b.path, b.start) for b in breaches] == [
        ("src/a.py", 90), ("src/a.py", 10), ("src/b.py", 40)]


def test_the_ceilings_come_from_the_hooks_own_per_scope_map():
    in_scope = {"src": ["src/a.py"], "legacy": ["legacy/old.py"]}

    ceilings = file_ceilings(CFG, in_scope, ["src/a.py", "legacy/old.py"])

    assert ceilings == {"src/a.py": 6, "legacy/old.py": 10}
    rows = [row("src/a.py", "eight( n )", 8, 1.0, 8.0),
            row("legacy/old.py", "eight( n )", 8, 1.0, 8.0, scope="legacy")]
    assert [b.path for b in gated(rows)] == ["src/a.py"]


def test_only_functions_a_changed_span_overlaps_reach_the_ceiling_rule():
    rows = [row("src/a.py", "edited( n )", 9, 1.0, 9.0, start=10),
            row("src/a.py", "legacy( n )", 15, 0.4, 63.6, start=100)]

    assert [b.long_name for b in gated(rows, ranges={"src/a.py": [(12, 12)]})] == ["edited( n )"]


def test_a_file_with_no_changed_range_contributes_no_candidate():
    rows = [row("src/a.py", "legacy( n )", 15, 0.4, 63.6)]

    assert gated(rows, ranges={"src/b.py": [(1, 400)]}) == []


def test_a_change_at_the_last_line_of_a_function_still_selects_it():
    rows = [row("src/a.py", "edge( n )", 9, 1.0, 9.0, start=10)]  # spans 10..15

    assert len(gated(rows, ranges={"src/a.py": [(15, 15)]})) == 1


def test_a_breach_sitting_at_its_recorded_mark_is_carried_debt():
    rows = [row("src/a.py", "legacy( n )", 15, 0.4, 63.6)]

    assert gated(rows, [RatchetEntry("src/a.py", "legacy( n )", 63.6)]) == []


def test_a_breach_past_its_recorded_mark_still_fires():
    rows = [row("src/a.py", "legacy( n )", 15, 0.4, 70.0)]

    kept = gated(rows, [RatchetEntry("src/a.py", "legacy( n )", 63.6)])

    assert [b.crap for b in kept] == [70.0]


def test_a_mark_on_another_function_exempts_nothing():
    rows = [row("src/a.py", "fresh( n )", 9, 1.0, 9.0)]

    kept = gated(rows, [RatchetEntry("src/a.py", "legacy( n )", 63.6)])

    assert [b.long_name for b in kept] == ["fresh( n )"]


def test_marks_compare_at_the_four_decimals_they_are_stored_at():
    rows = [row("src/a.py", "legacy( n )", 15, 0.4, 63.60001)]

    assert gated(rows, [RatchetEntry("src/a.py", "legacy( n )", 63.6)]) == []
