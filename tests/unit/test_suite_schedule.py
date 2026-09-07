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


def fixture_repo(tmp_path, failure):
    for directory in ("src/crapkit", "tests/unit", "tests/e2e"):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "src/crapkit/__init__.py").write_text(
        "def choose(value):\n    if value:\n        return 11\n    return 22\n")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\npatch=["subprocess"]\n'
        '[tool.pytest.ini_options]\ntestpaths=["tests"]\n')
    (tmp_path / "tests/unit/test_one.py").write_text(
        "from crapkit import choose\ndef test_unit():\n"
        f"    assert choose(True) == {0 if failure == 'unit' else 11}\n")
    (tmp_path / "tests/e2e/test_two.py").write_text(
        "import subprocess, sys\ndef test_child():\n"
        "    result = subprocess.run([sys.executable, '-c', "
        "'from crapkit import choose; print(choose(False))'], capture_output=True, text=True)\n"
        f"    assert result.stdout.strip() == '{0 if failure == 'e2e' else 22}'\n")


@pytest.mark.parametrize("failure", ["", "unit", "e2e"])
def test_real_runner_keeps_both_suites_and_subprocess_branches(tmp_path, failure):
    fixture_repo(tmp_path, failure)
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "src"))
    for key in ("COVERAGE_PROCESS_START", "COVERAGE_FILE", "COV_CORE_SOURCE",
                "COV_CORE_CONFIG", "COV_CORE_DATAFILE", "COVERAGE_RCFILE"):
        env.pop(key, None)
    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(tmp_path),
                             "--coverage", "--workers", "2"], env=env,
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
    contexts = {context for items in measured["contexts"].values() for context in items}
    assert any("test_unit" in context for context in contexts)
