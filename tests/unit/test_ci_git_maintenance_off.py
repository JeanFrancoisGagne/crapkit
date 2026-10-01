"""CI's test jobs start no git maintenance writer behind a test's back.

git 2.47 and later start `git maintenance run --auto` detached after a commit,
and the writer holds .git/objects/maintenance.lock for a moment after the commit
returns. GitHub's runners carry such a git; the local boxes and the accuracy
image carry 2.43, which never detaches. A fixture that commits and a test that
copies its repository race the writer: on 2026-10-01 main went red in two Ubuntu
test legs at 62118e3e when shutil.copytree listed the lock and the writer removed
it (tests/e2e/test_invariant_refusal_e2e.py's guards template). The dogfood job
lost the same race in 0.8.1. Each job that runs tests or crapkit's lanes turns
automatic maintenance off in git's system config, which a test that swaps HOME
or drops the GIT_* variables still reads. The deploy jobs keep git as a user's
machine has it.
"""
import re

import yaml

from test_ci_verdict import ROOT

STEP = "git starts no maintenance writer while tests copy repositories"
RUNS_TESTS = re.compile(r"pytest|tools/testing/(run|ci)\.py|tools/accuracy/run\.py|\bcrapkit\b")


def _jobs():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]


def _first_test_step(steps):
    return next((i for i, step in enumerate(steps)
                 if RUNS_TESTS.search(step.get("run") or "") or step.get("uses", "").startswith("./")),
                None)


def _maintenance_step(steps):
    return next((i for i, step in enumerate(steps) if step.get("name") == STEP), None)


def test_every_job_that_runs_tests_turns_git_maintenance_off_before_them():
    late = {}
    for name, job in _jobs().items():
        first = _first_test_step(job.get("steps", []))
        if name.startswith("deploy") or first is None:
            continue
        off = _maintenance_step(job["steps"])
        if off is None or off > first:
            late[name] = (off, first)

    assert late == {}


def test_the_step_writes_both_keys_to_git_s_system_config_on_every_os():
    runs = {job["steps"][_maintenance_step(job["steps"])]["run"] for job in _jobs().values()
            if _maintenance_step(job.get("steps", [])) is not None}

    assert len(runs) == 1
    run = runs.pop()
    assert "git config --system maintenance.auto false" in run
    assert "git config --system gc.auto 0" in run
    assert "RUNNER_OS" in run and "sudo" in run


def test_a_deploy_job_keeps_git_as_a_user_s_machine_has_it():
    kept = [name for name, job in _jobs().items()
            if name.startswith("deploy") and _maintenance_step(job.get("steps", [])) is not None]

    assert kept == []
