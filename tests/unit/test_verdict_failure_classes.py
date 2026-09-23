"""The Verdict names every class of finding it settles without failing on.

A fresh failure is new, or forgiven because the baseline carries it too. A new
failure that passes its flake retry is a retried pass. A gate violation an
--override granted is overridden. Each class lives on the Verdict, so verify
prints, serializes and stores what the Verdict holds, and settling re-derives
every field that depends on what is left.
"""
from pathlib import Path
from types import SimpleNamespace

from crapkit.cli import verifying
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


def _grant(monkeypatch, verdict, reason):
    """verify's own --override grant, with the audit write it makes recorded
    instead of performed: the marks file, alert and store are record_override's."""
    granted = []
    monkeypatch.setattr("crapkit.override.record_override",
                        lambda **kw: granted.append(kw["violations"]))
    cfg = SimpleNamespace(ratchet_file="crapkit-ratchet.tsv", alert_command=None)
    return verifying._apply_verify_override(None, 7, Path("."), cfg, verdict, reason), granted


def test_a_granted_gate_violation_stays_on_the_verdict_as_overridden(monkeypatch):
    gated = settle_verdict(found(fresh_failures={OLD})._replace(gate_violations=[GATE]))

    verdict, granted = _grant(monkeypatch, gated, "shipping the spike")

    assert granted == [[GATE]]
    assert verdict.ok is True
    assert verdict.gate_violations == []
    assert verdict.overridden == (GATE,)
    assert verdict.forgiven_failures == (OLD,)


def test_an_override_with_no_reason_grants_nothing(monkeypatch):
    gated = settle_verdict(found(fresh_failures={OLD})._replace(gate_violations=[GATE]))

    verdict, granted = _grant(monkeypatch, gated, None)

    assert granted == []
    assert verdict.ok is False
    assert verdict.gate_violations == [GATE]
    assert verdict.overridden == ()
