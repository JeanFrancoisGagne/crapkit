"""Debt policy over the burn-down report: marks that outstay their welcome and
repayment that stalls are findings, not vibes."""
from crapkit.ratchet_report import DAY, policy_violations


def _report(open_ages_days, dropped_last_30d):
    anchor = 1000 + 400 * DAY
    return {
        "open": len(open_ages_days),
        "oldest": [{"path": f"src/f{i}.py", "long_name": f"f{i}( )", "age_days": age}
                   for i, age in enumerate(open_ages_days)],
        "dropped_total": dropped_last_30d,
        "dropped_last_30d": dropped_last_30d,
        "dropped_last_90d": dropped_last_30d,
        "anchor_ts": anchor,
    }


def test_no_knobs_means_no_violations():
    assert policy_violations(_report([500, 10], 0), None, None) == []


def test_expired_marks_are_named():
    violations = policy_violations(_report([200, 10], 5), 6, None)
    assert len(violations) == 1
    assert "f0( )" in violations[0] and "200" in violations[0]


def test_stalled_repayment_is_a_violation_only_with_open_debt():
    assert policy_violations(_report([50], 0), None, 2) != []
    assert policy_violations(_report([], 0), None, 2) == [], \
        "zero open marks cannot owe repayments"


def test_quota_met_is_quiet():
    assert policy_violations(_report([50], 3), None, 2) == []
