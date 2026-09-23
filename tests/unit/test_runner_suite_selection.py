"""The runner runs one pytest session when CI gives each session its own job."""
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from test_suite_schedule import SCRIPT, fixture_env, fixture_repo


def run(root, *extra):
    command = [sys.executable, str(SCRIPT), "--repo", str(root), "--workers", "2",
               "--unit-workers", "1", *extra, "--output", ".crapkit/cov"]
    return subprocess.run(command, env=fixture_env(root), capture_output=True, text=True)


def cases(root):
    report = ET.parse(root / ".crapkit/cov/junit.xml")
    return sorted(case.get("name") for case in report.iter("testcase"))


@pytest.mark.parametrize(("suite", "ran", "skipped"), [
    ("unit", ["test_unit"], "e2e"), ("e2e", ["test_child"], "unit")])
def test_one_suite_runs_alone_and_the_other_leaves_no_evidence(tmp_path, suite, ran, skipped):
    # The suite left out would fail, so a zero exit proves it never ran.
    fixture_repo(tmp_path, skipped)

    result = run(tmp_path, "--suite", suite)

    assert result.returncode == 0, result.stdout + result.stderr
    assert cases(tmp_path) == ran
    output = tmp_path / ".crapkit/cov"
    assert (output / (suite + ".xml")).is_file()
    assert not (output / (skipped + ".xml")).exists()


def test_naming_both_suites_is_the_default_schedule(tmp_path):
    fixture_repo(tmp_path, "")

    result = run(tmp_path, "--suite", "unit", "e2e")

    assert result.returncode == 0, result.stdout + result.stderr
    assert cases(tmp_path) == ["test_child", "test_unit"]


def test_the_selected_suite_still_fails_the_run(tmp_path):
    fixture_repo(tmp_path, "e2e")

    result = run(tmp_path, "--suite", "e2e")

    assert result.returncode == 1, result.stdout + result.stderr
    assert cases(tmp_path) == ["test_child"]


def test_one_suite_measures_only_the_branches_it_ran(tmp_path):
    # tests/unit calls choose(True): lines 1-3 and the branch 2->3, never 2->4.
    fixture_repo(tmp_path, "")

    result = run(tmp_path, "--suite", "unit", "--coverage")

    assert result.returncode == 0, result.stdout + result.stderr
    coverage = json.loads((tmp_path / ".crapkit/cov/py.json").read_text(encoding="utf-8"))
    files = {name.replace("\\", "/"): data for name, data in coverage["files"].items()}
    measured = files["src/crapkit/__init__.py"]
    assert measured["executed_branches"] == [[2, 3]]
    assert measured["executed_lines"] == [1, 2, 3]


def test_an_unknown_suite_is_refused_before_anything_runs(tmp_path):
    fixture_repo(tmp_path, "")

    result = run(tmp_path, "--suite", "integration")

    assert result.returncode == 2
    assert "invalid choice: 'integration'" in result.stderr
    assert not (tmp_path / ".crapkit").exists()
