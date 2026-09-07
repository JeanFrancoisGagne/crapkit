"""The hosted schedule keeps platform coverage without running a duplicate suite."""
import itertools
from pathlib import Path
import runpy
import shlex
import subprocess
import sys
import tomllib

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]


def step(steps, key, prefix):
    return next((item for item in steps if str(item.get(key, "")).startswith(prefix)), None)


def matrix_platforms(matrix):
    excluded = {(row["os"], row["python"]) for row in matrix.get("exclude", [])}
    return set(itertools.product(matrix["os"], matrix["python"])) - excluded


def assert_source_install(job):
    assert step(job["steps"], "run", 'pip install -e ".[dev]"') is not None
    assert step(job["steps"], "run", "crapkit --version") is not None


def test_scoped_source_tests_use_the_same_unit_schedule_as_ci():
    schedule = runpy.run_path(str(ROOT / "tools/testing/run.py"))
    config = tomllib.loads((ROOT / "crapkit.toml").read_text(encoding="utf-8"))
    assert shlex.split(config["crapkit"]["scoped_tests"]["src"]) == schedule["test_commands"]()[0]
    assert schedule["UNIT_WORKERS"] == 4


def test_source_platform_matrix_has_one_owner_and_keeps_install_and_gate_contracts():
    jobs = workflow()
    matrix = jobs["test"]["strategy"]["matrix"]
    combinations = matrix_platforms(matrix)
    dogfood = jobs["dogfood"]
    setup = step(dogfood["steps"], "uses", "actions/setup-python@")
    owner = (dogfood["runs-on"], setup["with"]["python-version"])
    assert combinations | {owner} == set(itertools.product(
        ["ubuntu-latest", "windows-latest"], ["3.11", "3.12", "3.13"]))
    assert owner not in combinations, "the action already measures this source suite"
    for job in (jobs["test"], dogfood):
        assert_source_install(job)
    action = step(dogfood["steps"], "uses", "./")
    assert action["with"]["python-version"] == "3.12"
    assert step(dogfood["steps"], "run", 'python -m crapkit hook-precommit --base "$BASE_REF"') is not None
    config = tomllib.loads((ROOT / "crapkit.toml").read_text(encoding="utf-8"))
    assert config["lane"][0]["command"] == "python tools/testing/run.py --coverage --output .crapkit/cov"
    assert step(jobs["verdict"]["steps"], "run", 'python tools/testing/ci.py --base "$BASE_REF"') is not None


@pytest.mark.parametrize(("xml", "passes"), [
    ('<testsuite tests="1"><testcase name="works"/></testsuite>', True),
    ('<testsuite tests="1"><testcase name="fails"><failure/></testcase></testsuite>', False),
    ('<testsuite tests="1"><testcase name="errors"><error/></testcase></testsuite>', False),
    ('<testsuite tests="0"/>', False),
    ('<unfinished', False),
    (None, False),
])
def test_dogfood_suite_check_uses_actual_junit_and_fails_for_every_failed_attempt(tmp_path, xml, passes):
    steps = workflow()["dogfood"]["steps"]
    check = step(steps, "name", "require the action's complete passing test suite")
    assert check is not None, "an advisory action cannot replace the test job without checking its suite"
    assert "continue-on-error" not in check
    assert "if" not in check
    output = tmp_path / ".crapkit/cov"
    output.mkdir(parents=True)
    if xml is not None:
        (output / "junit.xml").write_text(xml, encoding="utf-8")
    command = shlex.split(check["run"])
    assert command[:2] == ["python", "-c"]
    result = subprocess.run([sys.executable, *command[1:]], cwd=tmp_path, capture_output=True, text=True)
    assert (result.returncode == 0) is passes, result.stdout + result.stderr


def test_dogfood_cannot_admit_old_junit_when_the_coverage_launcher_never_starts(tmp_path):
    steps = workflow()["dogfood"]["steps"]
    cleanup = step(steps, "name", "discard prior action test evidence")
    assert cleanup is not None, "a launch failure must not leave a previous passing JUnit available"
    output = tmp_path / ".crapkit/cov/junit.xml"
    output.parent.mkdir(parents=True)
    output.write_text('<testsuite tests="1"><testcase name="old passing case"/></testsuite>')
    clear = shlex.split(cleanup["run"])
    subprocess.run([sys.executable, *clear[1:]], cwd=tmp_path, check=True)
    assert not output.exists()
    check = step(steps, "name", "require the action's complete passing test suite")
    command = shlex.split(check["run"])
    result = subprocess.run([sys.executable, *command[1:]], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    action_index = steps.index(step(steps, "uses", "./"))
    assert steps.index(cleanup) < action_index < steps.index(check)


def test_dogfood_prints_the_sorted_failed_test_ids_before_refusing(tmp_path):
    check = step(workflow()["dogfood"]["steps"], "name", "require the action's complete passing test suite")
    output = tmp_path / ".crapkit/cov/junit.xml"
    output.parent.mkdir(parents=True)
    output.write_text('<testsuite tests="2"><testcase classname="z" name="last"><failure/>'
                      '</testcase><testcase classname="a" name="first"><error/></testcase></testsuite>')
    command = shlex.split(check["run"])
    result = subprocess.run([sys.executable, *command[1:]], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout.splitlines() == ["a::first", "z::last"]


def test_dogfood_retains_junit_and_test_logs_even_when_a_prior_step_failed(tmp_path):
    upload = step(workflow()["dogfood"]["steps"], "uses", "actions/upload-artifact@v4")
    assert upload is not None, "failed action suites need retained JUnit and logs"
    assert upload["if"] == "always()"
    settings = upload["with"]
    assert settings["include-hidden-files"] is True
    assert settings["if-no-files-found"] == "warn"
    assert settings["name"] == "crapkit-dogfood-${{ github.run_id }}-${{ github.run_attempt }}"
    expected = [".crapkit/cov/junit.xml", ".crapkit/cov/unit.xml", ".crapkit/cov/e2e.xml",
                ".crapkit/cov/incomplete/run/pytest.log", ".crapkit/lane-py.log"]
    for name in expected:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("test evidence")
    selected = {path.relative_to(tmp_path).as_posix() for pattern in settings["path"].splitlines()
                for path in tmp_path.glob(pattern)}
    assert selected == set(expected)
