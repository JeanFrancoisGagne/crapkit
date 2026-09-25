"""A new user's first hour: install crapkit the way the README says, then walk
the 60-second start and the two quickstarts on a repo of that shape.

Every command comes from the fence that prints it. Each step asserts its exit
code and the line the user acts on next: the pytest-cov hint init prints, the
container guard's refusal and the docs/lanes.md#containers rule that answers
it, the MISSING DEPENDENCY a vitest repo meets before the README's `npm i -D`
line, the verdict verify prints. A quickstart's printed lines are held to the
page's transcript by shape: numbers and commit ids vary, the words may not.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
from pathlib import Path

import pytest

from kit import docsnip, installers, repos, wheels
from kit.cells import cell
from kit.installers import README, START, said, shape

PACKET = "deploy-channels"
WINDOWS = os.name == "nt"
PYCOV_HINT = re.compile(r"cannot import pytest_cov - run `([^`]+)`")
GUARD = "runs the python suite, which is host-only (container runs OOM)"


# --- the 60-second start -------------------------------------------------------------

def _init(box, repo, line):
    step = box.script(line, cwd=repo, expect=0)
    assert "detected 1 lane(s) from this repo's own files: py - next: run `crapkit coverage`" in step.stdout
    hint = PYCOV_HINT.search(said(step))
    if hint:
        box.script(hint[1], cwd=repo, expect=0, note="the command init's note names")
    return step


def _doctor(box, repo, line):
    step = box.script(line, cwd=repo, expect=0)
    assert "ok   lizard 1.24.0" in step.stdout
    return step


def _coverage(box, repo, line):
    """A coverage.py lane in a container meets the guard first; the user applies
    the rule the refusal names from docs/lanes.md#containers and reruns."""
    if installers.in_container():
        refused = box.script(line, cwd=repo, expect=5)
        assert GUARD in said(refused) and "set container_ok = true" in said(refused)
        box.transcript.note(f"applied docs/lanes.md#containers: {installers.allow_containers(repo)}")
    step = box.script(line, cwd=repo, expect=0)
    assert "-> next: crapkit worklist" in step.stdout
    return step


def _worklist(box, repo, line):
    step = box.script(line, cwd=repo, expect=0)
    assert re.search(r"calc/grade\.py:\d+\s+grade\( score , attempts , late , bonus \)", step.stdout)
    return step


def _seed(box, repo, line):
    step = box.script(line, cwd=repo, expect=0)
    assert said(step).startswith("crapkit-ratchet.tsv: added 1, tightened 0")
    return step


def _plain(box, repo, line):
    return box.script(line, cwd=repo, expect=0, env=box.commit_env())


START_STEPS = {"crapkit init": _init, "crapkit doctor": _doctor, "crapkit coverage": _coverage,
               "crapkit worklist": _worklist, "crapkit ratchet seed": _seed}


def _step_rule(line: str):
    return next((rule for prefix, rule in START_STEPS.items() if line.startswith(prefix)), _plain)


def readme_start(box, repo: Path) -> dict[str, object]:
    """The README's 60-second start after its install line, then the verify its
    prose says establishes the first passing verdict. `cd your-repo` is the
    cell's cwd."""
    steps = {}
    for line in installers.fence_commands(README, START)[1:]:
        if not line.startswith("cd "):
            steps[line] = _step_rule(line)(box, repo, line)
    box.run(["git", "commit", "-q", "-m", "adopt crapkit"], cwd=repo, env=box.commit_env(), expect=0)
    verify = installers.inline(README, START, "crapkit verify")
    steps[verify] = box.script(verify, cwd=repo, expect=0)
    assert said(steps[verify]).startswith("verify OK")
    return steps


def pip_start(box, templates, candidate, python: str) -> dict[str, object]:
    install = installers.pip_venv(box, python)
    assert candidate.version in install.run(box, box.root, "--version").stdout
    repo = repos.checkout(box, "py-pytest", cache=templates)
    return readme_start(box, repo)


@cell("lin-pip-start-py311", channel="pip venv (wheelhouse + candidate)", harness="none (sh + git)",
      scenario="fresh: README 60-second start verbatim; container refusal at coverage, then docs/lanes.md#containers "
               "fix; worklist; seed", use_cases="60-second start, init, doctor, coverage, worklist, ratchet seed",
      os="linux", image="core", cadence="push")
def test_the_readme_start_on_311_meets_the_container_guard_and_its_documented_rule(box, templates, candidate):
    assert installers.in_container(), "a container cell: the guard is its scenario"
    pip_start(box, templates, candidate, "3.11")

    assert "container_ok = true" in (box.root / "py-pytest" / "crapkit.toml").read_text(encoding="utf-8")


@cell("lin-pip-start-py311", channel="pip venv", harness="none (sh + git)",
      scenario="fresh: doctor names the container guard before coverage refuses", use_cases="doctor",
      os="linux", image="core", cadence="push")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-channels-1: in a container `crapkit doctor` exits 0 "
                                       "with no WARN, then `crapkit coverage` refuses the python lane with exit 5")
def test_doctor_warns_about_the_container_guard_before_coverage_refuses(box, templates, candidate):
    installers.pip_extra(box, "3.11")
    repo = repos.checkout(box, "py-pytest", cache=templates)
    box.run(["crapkit", "init"], cwd=repo, expect=0)
    doctor = box.run(["crapkit", "doctor"], cwd=repo, expect=0)
    box.run(["crapkit", "coverage"], cwd=repo, expect=5)

    assert [line for line in doctor.stdout.splitlines() if line.startswith("WARN") and "container_ok" in line]


@cell("lin-pip-start-py314", channel="pip venv", harness="none", scenario="fresh: same start on CPython 3.14",
      use_cases="60-second start", os="linux", image="core", cadence="push")
def test_the_readme_start_runs_on_314(box, templates, candidate):
    steps = pip_start(box, templates, candidate, "3.14")

    assert "3.14" in box.run(["python", "--version"], expect=0).stdout
    assert steps


@cell("lin-native-start", channel="pip venv", harness="none (bare runner)",
      scenario="fresh: lin-pip-start-py311 and Route 1 on a bare runner, no container_ok",
      use_cases="60-second start, commit gate", os="linux", image=None, cadence="push")
def test_the_readme_start_and_route_1_on_a_bare_runner(box, templates, candidate):
    if installers.in_container():
        pytest.skip("a bare-runner cell: `run.py --native` on ubuntu-24.04 runs it (deploy-linux-native)")
    pip_start(box, templates, candidate, "3.11")
    repo = box.root / "py-pytest"
    route_1(box, repo)

    assert "container_ok" not in (repo / "crapkit.toml").read_text(encoding="utf-8")


@cell("win-pip-start", channel="pip venv", harness="none (cmd.exe, PortableGit)",
      scenario="fresh: start verbatim, no container_ok, cp1252 console", use_cases="60-second start",
      os="windows", image=None, cadence="push")
def test_the_readme_start_on_windows_needs_no_container_rule(box, templates, candidate):
    steps = pip_start(box, templates, candidate, "3.12")

    assert "container_ok" not in (box.root / "py-pytest" / "crapkit.toml").read_text(encoding="utf-8")
    assert all(line in steps for line in ("crapkit init", "crapkit coverage", "crapkit ratchet seed"))


# --- Route 1, for the bare-runner start ------------------------------------------------

ROUTE_1 = "Route 1: `.git/hooks/pre-commit` (local, not committed)"
GATE_BREACH = '''

def breach(a, b, c, d):
    if a and b:
        return 1
    if b or c:
        return 2
    if c and d:
        return 3
    return 4 if a else 5
'''


def route_1(box, repo: Path) -> None:
    """README Route 1: the hook refuses a staged breach, then passes its fix."""
    box.script(docsnip.fence(README, ROUTE_1).text, cwd=repo, expect=0, note="README Route 1, the whole heredoc")
    grade = repo / "calc" / "grade.py"
    grade.write_text(grade.read_text(encoding="utf-8") + GATE_BREACH, encoding="utf-8")
    box.run(["git", "add", "calc/grade.py"], cwd=repo, expect=0)
    refused = box.run(["git", "commit", "-q", "-m", "add breach"], cwd=repo, env=box.commit_env(), expect=1)
    assert "crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6" in said(refused)
    grade.write_text(grade.read_text(encoding="utf-8").replace(GATE_BREACH, "\n\ndef fine(a):\n    return a\n"),
                     encoding="utf-8")
    box.run(["git", "add", "calc/grade.py"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "add fine"], cwd=repo, env=box.commit_env(), expect=0)
