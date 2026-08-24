"""Verify seam: fresh scored rows + baseline + ratchet + changed ranges in, Verdict out. Pure."""
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow
from crapkit.verify import Verdict, evaluate


def scored(path="src/a.ts", name="f( )", start=1, end=9, ccn=5, cov=1.0, crap=None, scope="src", flag="measured"):
    c = crap if crap is not None else ccn * ccn * (1 - cov) ** 3 + ccn
    remedy = "decompose" if ccn > 6 else ("ok" if c <= 6 else "add-tests")
    return ScoredRow(scope, path, name, start, end, ccn + 1, ccn, ccn, 5, 1, 1, cov, flag, c, remedy)


def test_clean_change_passes():
    v = evaluate(
        fresh=[scored(ccn=4, cov=1.0)],
        changed_ranges={"src/a.ts": [(1, 9)]},
        ratchet=[],
        baseline_failures=set(),
        fresh_failures=set(),
        target=6,
    )
    assert isinstance(v, Verdict)
    assert v.ok
    assert v.gate_violations == [] and v.ratchet_regressions == [] and v.new_failures == []


def test_touched_function_over_target_is_a_gate_violation():
    v = evaluate(fresh=[scored(ccn=5, cov=0.0)], changed_ranges={"src/a.ts": [(3, 4)]},
                 ratchet=[], baseline_failures=set(), fresh_failures=set(), target=6)
    assert not v.ok
    assert v.gate_violations[0].long_name == "f( )"
    assert v.gate_violations[0].crap == 30.0


def test_untouched_function_over_target_is_not_a_gate_violation():
    v = evaluate(fresh=[scored(ccn=5, cov=0.0)], changed_ranges={},
                 ratchet=[], baseline_failures=set(), fresh_failures=set(), target=6)
    assert v.ok, "legacy debt is trend, not a blocker, until touched"


def test_cc7_at_full_coverage_still_fails_the_gate():
    v = evaluate(fresh=[scored(ccn=7, cov=1.0)], changed_ranges={"src/a.ts": [(1, 9)]},
                 ratchet=[], baseline_failures=set(), fresh_failures=set(), target=6)
    assert not v.ok and v.gate_violations[0].ccn == 7


def test_ratchet_regression_fires_even_untouched():
    entry = RatchetEntry(path="src/a.ts", long_name="f( )", crap=20.0)
    v = evaluate(fresh=[scored(ccn=5, cov=0.0)], changed_ranges={},
                 ratchet=[entry], baseline_failures=set(), fresh_failures=set(), target=6)
    assert not v.ok
    assert v.ratchet_regressions[0].fresh_crap == 30.0
    assert v.ratchet_regressions[0].recorded == 20.0


def test_ratchet_improvement_is_not_a_regression():
    entry = RatchetEntry(path="src/a.ts", long_name="f( )", crap=40.0)
    v = evaluate(fresh=[scored(ccn=5, cov=0.5)], changed_ranges={},
                 ratchet=[entry], baseline_failures=set(), fresh_failures=set(), target=6)
    assert v.ok


def test_new_test_failures_vs_baseline_fail_the_verdict():
    v = evaluate(fresh=[scored(ccn=2)], changed_ranges={},
                 ratchet=[], baseline_failures={"t_old"}, fresh_failures={"t_old", "t_new"}, target=6)
    assert not v.ok and v.new_failures == ["t_new"]


def test_preexisting_failures_do_not_fail_the_verdict():
    v = evaluate(fresh=[scored(ccn=2)], changed_ranges={},
                 ratchet=[], baseline_failures={"t_old"}, fresh_failures={"t_old"}, target=6)
    assert v.ok


def test_twin_functions_regression_cannot_hide_behind_its_sibling():
    entry = RatchetEntry(path="src/a.ts", long_name="handlers ( )", crap=20.0)
    twin_bad = scored(name="handlers ( )", start=1, end=1, ccn=9, cov=0.0)   # crap 90
    twin_ok = scored(name="handlers ( )", start=2, end=2, ccn=2, cov=1.0)
    v = evaluate(fresh=[twin_bad, twin_ok], changed_ranges={}, ratchet=[entry],
                 baseline_failures=set(), fresh_failures=set(), target=6)
    assert not v.ok and v.ratchet_regressions, "the WORST twin is compared against the mark"
    v2 = evaluate(fresh=[twin_ok, twin_bad], changed_ranges={}, ratchet=[entry],
                  baseline_failures=set(), fresh_failures=set(), target=6)
    assert not v2.ok, "input order must not decide"


def test_gate_uses_the_scope_ceiling():
    from crapkit.score import ScoredRow, crap
    from crapkit.verify import evaluate
    rows = [
        ScoredRow("src", "src/a.ts", "f( )", 1, 9, 8, 8, 8, 5, 1, 1, 1.0, "measured", crap(8, 1.0), "decompose"),
        ScoredRow("legacy", "old/b.py", "g( )", 1, 9, 8, 8, 8, 5, 1, 1, 1.0, "measured", crap(8, 1.0), "ok"),
    ]
    changed = {"src/a.ts": [(1, 9)], "old/b.py": [(1, 9)]}
    v = evaluate(fresh=rows, changed_ranges=changed, ratchet=[], baseline_failures=set(),
                 fresh_failures=set(), target=6, scope_targets={"src": 6, "legacy": 10})
    assert [g.path for g in v.gate_violations] == ["src/a.ts"], \
        "the legacy scope's ceiling of 10 admits ccn 8; src's ceiling of 6 does not"
