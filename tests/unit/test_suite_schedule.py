"""The developer runner produces complete evidence from both test suites."""
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/testing/run.py"


def fixture_env(root):
    """The miniature package owns its tracing and cannot enter the parent data."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COVERAGE_", "COV_CORE_"))}
    env["PYTHONPATH"] = str(root / "src")
    return env


def fixture_repo(tmp_path, failure, *, contexts=True):
    for directory in ("src/crapkit", "tests/unit", "tests/e2e"):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "src/crapkit/__init__.py").write_text(
        "def choose(value):\n    if value:\n        return 11\n    return 22\n")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\npatch=["subprocess"]\n'
        '[tool.pytest.ini_options]\ntestpaths=["tests"]\n'
        f'addopts="{"--cov-context=test" if contexts else ""}"\n')
    (tmp_path / "tests/unit/test_one.py").write_text(
        "from crapkit import choose\ndef test_unit():\n"
        f"    assert choose(True) == {0 if failure == 'unit' else 11}\n")
    (tmp_path / "tests/e2e/test_two.py").write_text(
        "import subprocess, sys\ndef test_child():\n"
        "    result = subprocess.run([sys.executable, '-c', "
        "'from crapkit import choose; print(choose(False))'], capture_output=True, text=True)\n"
        f"    assert result.stdout.strip() == '{0 if failure == 'e2e' else 22}'\n")


@pytest.mark.parametrize(("failure", "contexts"), [
    ("", True), ("unit", True), ("e2e", True),
    pytest.param("", False, id="no-contexts")])
def test_real_runner_keeps_both_suites_and_subprocess_branches(tmp_path, failure, contexts):
    fixture_repo(tmp_path, failure, contexts=contexts)
    env = fixture_env(tmp_path)
    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(tmp_path),
                             "--coverage", "--workers", "2", "--unit-workers", "1",
                             "--output", ".crapkit/cov"], env=env,
                            capture_output=True, text=True)
    assert result.returncode == bool(failure), result.stdout + result.stderr
    junit = ET.parse(tmp_path / ".crapkit/cov/junit.xml")
    assert sorted(case.attrib["name"] for case in junit.findall(".//testcase")) == [
        "test_child", "test_unit"]
    assert len(junit.findall(".//failure")) == bool(failure)
    coverage = json.loads((tmp_path / ".crapkit/cov/py.json").read_text())
    files = {name.replace("\\", "/"): data for name, data in coverage["files"].items()}
    measured = files["src/crapkit/__init__.py"]
    assert measured["executed_branches"] == [[2, 3], [2, 4]]
    saved_contexts = {context for items in measured["contexts"].values() for context in items}
    if contexts:
        assert any("test_unit|run" in context for context in saved_contexts)
    else:
        assert saved_contexts == {""}
    for suite in ("unit", "e2e"):
        assert (tmp_path / ".crapkit/cov" / (suite + ".xml")).is_file()
        assert (tmp_path / ".crapkit/cov" / (suite + ".coverage")).is_file()


def test_startup_failure_replaces_old_passing_evidence_and_keeps_the_other_suite(tmp_path):
    fixture_repo(tmp_path, "")
    env = fixture_env(tmp_path)
    command = [sys.executable, str(SCRIPT), "--repo", str(tmp_path),
               "--coverage", "--workers", "2", "--unit-workers", "1", "--output", ".crapkit/cov"]
    first = subprocess.run(command, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stdout + first.stderr
    output = tmp_path / ".crapkit/cov"
    before = output.joinpath("junit.xml").read_bytes()
    (tmp_path / "tests/unit/conftest.py").write_text('raise RuntimeError("unit startup failed")\n')

    failed = subprocess.run(command, env=env, capture_output=True, text=True)

    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert output.joinpath("junit.xml").read_bytes() != before
    current = ET.parse(output / "junit.xml")
    assert len(current.findall(".//error")) == 1
    assert "test_child" in [case.attrib["name"] for case in current.findall(".//testcase")]
    assert "test_unit" not in [case.attrib["name"] for case in current.findall(".//testcase")]
    assert not output.joinpath("py.json").exists(), "old passing coverage must not survive"
    assert list(output.glob("incomplete/*/e2e.xml")), "retain this attempt's completed suite"


def test_empty_suite_records_an_infrastructure_failure_with_its_exit(tmp_path):
    fixture_repo(tmp_path, "")
    (tmp_path / "tests/unit/test_one.py").unlink()
    env = fixture_env(tmp_path)

    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(tmp_path),
                             "--workers", "2", "--unit-workers", "1", "--output", ".crapkit/cov"],
                            env=env, capture_output=True, text=True)

    assert result.returncode == 1
    current = ET.parse(tmp_path / ".crapkit/cov/junit.xml")
    error, = current.findall(".//error")
    assert "unit exited 5" in error.attrib["message"]
    assert "test_child" in [case.attrib["name"] for case in current.findall(".//testcase")]
