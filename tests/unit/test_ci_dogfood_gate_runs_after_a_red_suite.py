"""The dogfood job's event-base gate runs even when the suite step before it fails.

The gate step had no `if:`, and GitHub skips a step whose earlier step failed
unless its condition says otherwise. Run 36979999812 had a red suite step and
showed the gate step skipped, so the push went unjudged for complexity and the
log gave no sign of what the gate would have said. The gate reads the checked-out
tree and the event base, not the suite's evidence, so it runs after any outcome
short of a cancelled run.
"""
import yaml

from test_ci_verdict import ROOT

GATE = "gate changed functions against the event base"
SUITE = "require the action's complete passing test suite"


def _steps():
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]
    return jobs["dogfood"]["steps"]


def test_the_gate_step_runs_after_a_failed_suite_step():
    names = [step.get("name") for step in _steps()]
    gate = _steps()[names.index(GATE)]

    assert names.index(SUITE) < names.index(GATE)
    assert "hook-precommit --base" in gate["run"]
    assert gate.get("if") == "${{ !cancelled() }}"
