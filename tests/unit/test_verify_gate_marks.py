"""A ratchet mark exempts the function it names from verify's gate, at or under it.

Three gates, one rule (#29). `hook-precommit` skips a marked function on the
mark's existence and `rescore --gate` skips it at or under the recorded value,
so an edit inside signed debt passed both and then met verify's exit 6, which
read no marks: a one-line change in a function marked 10.5 and reading 10.5
failed a landing gate on 2026-08-30. verify's gate now applies rescore's rule.
Touched and at or under its mark, the function is the debt the repo signed
for; above it, the gate fires as rescore's would, and the ratchet check reports
the rise beside it.
"""
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow
from crapkit.verify import evaluate

TOUCHED = {"src/a.ts": [(3, 4)]}


def row(name: str = "f( )", ccn: int = 5, cov: float = 0.0) -> ScoredRow:
    crap = ccn * ccn * (1 - cov) ** 3 + ccn
    return ScoredRow("src", "src/a.ts", name, 1, 9, ccn + 1, ccn, ccn, 5, 1, 1, cov,
                     "measured", crap, "add-tests")


def verdict(fresh: list[ScoredRow], ratchet: list[RatchetEntry]):
    return evaluate(fresh=fresh, changed_ranges=TOUCHED, ratchet=ratchet,
                    baseline_failures=set(), fresh_failures=set(), target=6)


def test_a_touched_function_at_its_mark_is_signed_debt_not_a_violation():
    v = verdict([row()], [RatchetEntry("src/a.ts", "f( )", 30.0)])

    assert v.ok, v
    assert v.gate_violations == [] and v.ratchet_regressions == []


def test_under_its_mark_passes_too():
    assert verdict([row()], [RatchetEntry("src/a.ts", "f( )", 31.5)]).ok


def test_above_its_mark_fails_the_gate_as_rescore_would_and_the_ratchet_beside_it():
    v = verdict([row()], [RatchetEntry("src/a.ts", "f( )", 20.0)])

    assert not v.ok
    assert [g.crap for g in v.gate_violations] == [30.0]
    assert [(r.recorded, r.fresh_crap) for r in v.ratchet_regressions] == [(20.0, 30.0)]


def test_a_mark_on_another_function_exempts_nothing():
    v = verdict([row()], [RatchetEntry("src/a.ts", "g( )", 30.0)])

    assert [g.long_name for g in v.gate_violations] == ["f( )"]


def test_the_mark_is_compared_at_the_precision_it_is_stored_at():
    """Marks live as 4dp strings, and cov = covered/total makes longer decimals
    routine; an unrounded compare would fail an unchanged function against
    its own mark, as the ratchet check once did."""
    fresh = row(cov=1 / 3)
    v = verdict([fresh], [RatchetEntry("src/a.ts", "f( )", round(fresh.crap, 4))])

    assert v.ok, v
