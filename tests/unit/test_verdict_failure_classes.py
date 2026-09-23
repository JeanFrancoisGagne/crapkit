"""The Verdict names every class of finding it settles without failing on.

A fresh failure is new, or forgiven because the baseline carries it too. A new
failure that passes its flake retry is a retried pass. A gate violation an
--override granted is overridden. Each class lives on the Verdict, so verify
prints, serializes and stores what the Verdict holds, and settling re-derives
every field that depends on what is left.
"""
from crapkit.verify import GateViolation, evaluate, settle_flake_retry, settle_verdict

OLD, FLAKY, REAL = "tests/t.py::old", "tests/t.py::flaky", "tests/t.py::real"
GATE = GateViolation("src/a.py", "f( x )", 3, 9, 0.5, 84.0, "decompose")


def found(**kw):
    """Three fresh failures against a baseline that already failed `old`."""
    return evaluate(**{"fresh": [], "changed_ranges": {}, "ratchet": [],
                       "baseline_failures": {OLD, "tests/t.py::gone"},
                       "fresh_failures": {OLD, FLAKY, REAL}, "target": 6, **kw})


def test_a_failure_the_baseline_carries_is_forgiven_and_not_new():
    verdict = found()

    assert verdict.new_failures == [FLAKY, REAL]
    assert verdict.forgiven_failures == (OLD,)
    assert verdict.retried_passes == ()


def test_a_new_failure_that_passed_its_retry_is_a_retried_pass():
    verdict = settle_flake_retry(found(dirty_paths={"tests/t.py"}), {REAL})

    assert verdict.new_failures == [REAL]
    assert verdict.retried_passes == (FLAKY,)
    assert verdict.forgiven_failures == (OLD,), "a retry does not change what the baseline carries"
    assert verdict.dirty_failures == [REAL]
    assert verdict.ok is False


def test_a_retry_that_cleared_every_new_failure_passes_the_verdict():
    verdict = settle_flake_retry(found(), set())

    assert verdict.ok is True
    assert verdict.new_failures == []
    assert verdict.retried_passes == (FLAKY, REAL)


def test_a_granted_gate_violation_stays_on_the_verdict_as_overridden():
    gated = found(fresh_failures={OLD})._replace(ok=False, gate_violations=[GATE])

    granted = settle_verdict(gated._replace(gate_violations=[], overridden=(GATE,)))

    assert granted.ok is True
    assert granted.overridden == (GATE,)
    assert granted.forgiven_failures == (OLD,)
