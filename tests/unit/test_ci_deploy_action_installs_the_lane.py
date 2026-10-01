"""CI's deploy-action job runs the Action the way the README's recipe does.

consumer.py seeds the consumer in a throwaway venv that holds pytest and
pytest-cov, then the Action runs on the runner's own Python. The README's
recipe installs what the lanes run before the Action, and calls it the step
people leave out; the job left it out, and the first push of 0.8.1 to main
failed with `No module named pytest` in the py lane, no verdict and exit 5
where the cell expects the gate's 7.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _steps() -> list[dict]:
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    return jobs["deploy-action"]["steps"]


def test_the_lane_s_packages_are_installed_before_the_action_runs():
    steps = _steps()
    action = next(index for index, step in enumerate(steps) if step.get("uses") == "./crapkit")
    installs = " ".join(str(step.get("run", "")) for step in steps[:action] if "pip install" in str(step.get("run", "")))

    assert "pytest" in installs.split() and "pytest-cov" in installs.split(), installs
