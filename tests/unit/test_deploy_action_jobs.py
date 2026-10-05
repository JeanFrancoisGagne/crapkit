"""deploy.yml's Action jobs reach the verdict their cells expect.

Each of these jobs builds the consumer with consumer.py and then runs the
Action at the workspace root, as ci.yml's deploy-action job does. They failed
on every deploy run from 2026-10-02 (run 37188668270) for three reasons a
reader of the YAML can see: the job's Python had no pytest for the py lane,
the container job's root user met a workspace git does not trust, and the tag
upgrade seeded its consumer with the candidate instead of the release its
old step runs. A fourth sat in action.yml: inside a `container:` job the
Action's install step ran a pip script whose interpreter path the container
does not have.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from hang_guard import HANG_SECONDS

ROOT = Path(__file__).resolve().parents[2]
ACTION = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
WORKFLOWS = ROOT / ".github" / "workflows"
CI = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
DEPLOY = yaml.safe_load((WORKFLOWS / "deploy.yml").read_text(encoding="utf-8"))
CONSUMER = "tools/deploy/consumer.py"
# The deploy.yml jobs that run the Action on the runner's own Python.
RUNNER_JOBS = {"gha-action-tag-upgrade", "gha-action-windows", "published-action-tag"}


def _builds_consumer(step: dict) -> bool:
    return CONSUMER in step.get("run", "")


def _local_action(step: dict) -> bool:
    return step.get("uses", "").startswith("./")


def consumer_jobs() -> dict[str, dict]:
    """Every job of ci.yml and deploy.yml that builds the consumer, by name."""
    return {name: job for workflow in (CI, DEPLOY) for name, job in workflow["jobs"].items()
            if any(_builds_consumer(step) for step in job.get("steps", []))}


def runs_before(job: dict, predicate) -> list[str]:
    """The run lines of the steps before the first step `predicate` holds for."""
    steps = job["steps"]
    first = next(index for index, step in enumerate(steps) if predicate(step))
    return [step.get("run", "") for step in steps[:first]]


def test_each_runner_action_job_installs_the_lanes_pytest_before_the_action():
    """consumer.py seeds in a throwaway venv, so the job's own Python has no
    pytest, and the py lane `init` wrote fails with "No module named pytest":
    coverage exits 5 where the cell expects the gate's 7. ci.yml's deploy-action
    job installs it after consumer.py; the container job needs none, because
    the container guard refuses its lane first."""
    jobs = {name: job for name, job in consumer_jobs().items() if "container" not in job}
    missing = [name for name, job in jobs.items()
               if not any("pip install pytest pytest-cov" in run for run in runs_before(job, _local_action))]

    assert RUNNER_JOBS <= set(jobs)
    assert missing == []


def test_the_container_job_marks_the_workspace_safe_before_git_runs_in_it():
    """The job's container runs as root over a workspace the runner's uid owns,
    so `git add -A` in the repository consumer.py makes there exits 128
    ("detected dubious ownership") unless the job adds the workspace to
    safe.directory first. actions/checkout trusts only the path it checked out."""
    before = runs_before(DEPLOY["jobs"]["gha-action-container-job"], _builds_consumer)

    assert any("safe.directory" in run and "$GITHUB_WORKSPACE" in run for run in before)


def test_the_tag_upgrade_seeds_the_consumer_with_the_release_its_old_step_runs():
    """assert_action expects the candidate's stamp refusal for marks recorded
    under 0.7.6's analysis version, and the act cell seeds with crapkit==0.7.6
    for that reason. Seeded by the candidate, the marks carry the candidate's
    stamp, and the old step meets a lane command 0.7.6 cannot run
    (`{python} -m pytest`, exit 127)."""
    job = DEPLOY["jobs"]["gha-action-tag-upgrade"]
    old = next(step["uses"] for step in job["steps"] if step.get("id") == "old")
    build = next(step["run"] for step in job["steps"] if _builds_consumer(step))

    assert f"--seed-from {old}" in build


def _bash() -> str:
    """The bash a `shell: bash` step runs under: the one on PATH, or Git's in
    place of the WSL launcher under System32, which cannot read the files this
    test writes."""
    bash = shutil.which("bash")
    if bash is None or "system32" in bash.lower():
        git = shutil.which("git")
        candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
        bash = str(candidate) if candidate and candidate.is_file() else None
    if bash is None:
        pytest.skip("no bash on PATH to run the step under")
    return bash


def test_the_install_step_runs_pip_through_the_python_on_path(tmp_path):
    """A `container:` job mounts the runner's tool cache at /__t, but the
    scripts in it were written on the host, so pip's `#!` names
    /opt/hostedtoolcache/.../bin/python, a path the container does not have:
    `pip install` exits 127 ("required file not found") and the Action never
    reaches its verdict. `python` is the interpreter itself, found on the PATH
    setup-python set, and it runs pip as a module wherever the cache sits.
    The tool cache here is one whose pip names a missing interpreter and whose
    python records what it was asked to run."""
    step = next(step for step in ACTION["runs"]["steps"] if "pip install" in step.get("run", ""))
    tools, called = tmp_path / "toolcache" / "bin", tmp_path / "python.args"
    tools.mkdir(parents=True)
    scripts = {"pip": "#!/opt/hostedtoolcache/Python/3.12/x64/bin/python\nimport pip\n",
               "python": f'#!/bin/sh\nprintf "%s\\n" "$@" > "{called.as_posix()}"\n'}
    for name, text in scripts.items():
        (tools / name).write_text(text, encoding="utf-8", newline="\n")
        (tools / name).chmod(0o755)
    script = tmp_path / "install.sh"
    script.write_text(step["run"], encoding="utf-8", newline="\n")
    env = {**os.environ, "GITHUB_ACTION_PATH": "/action/checkout",
           "PATH": os.pathsep.join([str(tools), os.environ["PATH"]])}

    done = subprocess.run([_bash(), "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
                          env=env, capture_output=True, text=True, timeout=HANG_SECONDS)

    assert done.returncode == 0, done.stderr
    assert called.read_text(encoding="utf-8").splitlines() == ["-P", "-m", "pip", "install", "-e", "/action/checkout"]
