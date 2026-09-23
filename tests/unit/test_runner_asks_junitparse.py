"""The test runner asks junitparse whether pytest finished.

The lane, CI's JUnit probe and the runner judge the same report. They share one
rule, junitparse's, so the runner cannot publish coverage for a report the lane
then refuses, and its incomplete-suite record says what the lane would say.
"""
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from crapkit.errors import ToolError
from crapkit.junitparse import suite_summary
from test_suite_schedule import SCRIPT, fixture_env, fixture_repo


def run(root, *extra, env=None):
    command = [sys.executable, str(SCRIPT), "--repo", str(root), "--coverage",
               "--workers", "2", *extra, "--output", ".crapkit/cov"]
    return subprocess.run(command, env=env or fixture_env(root), capture_output=True, text=True)


def runner_record(root, suite):
    """The error the runner adds when it refuses a suite's report."""
    report = ET.parse(root / ".crapkit/cov/junit.xml")
    messages = [error.get("message", "") for error in report.iter("error")]
    return [message for message in messages if message.startswith(f"{suite} exited ")]


CRASH = "crashed while running 'tests/unit/test_crash.py::test_worker_dies'"


def crashed_run(root):
    """The runner's report of a unit session whose xdist worker died mid-test."""
    fixture_repo(root, "")
    (root / "tests/unit/test_crash.py").write_text(
        "import os\ndef test_worker_dies():\n    os._exit(13)\n")
    environment = fixture_env(root)
    environment["PYTEST_ADDOPTS"] = "--max-worker-restart=0"
    return run(root, env=environment)


def test_a_crashed_worker_is_refused_in_junitparse_words(tmp_path):
    result = crashed_run(tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    record, = runner_record(tmp_path, "unit")
    assert "junit reports a run that did not finish" in record, record
    assert CRASH in record, record


def test_the_lane_names_a_crash_the_runner_recorded_once(tmp_path):
    """The runner's record repeats junitparse's words for the crash, so the lane
    reading the combined report finds the same crash in two errors."""
    crashed_run(tmp_path)

    with pytest.raises(ToolError) as refused:
        suite_summary((tmp_path / ".crapkit/cov/junit.xml").read_text(encoding="utf-8"))

    assert str(refused.value).count(CRASH) == 1, refused.value


# Rewrites the finished report so it declares one test more than it holds: the
# shape of a report cut short, which junitparse refuses for the lane.
TRUNCATING_CONFTEST = '''import re
from pathlib import Path

def pytest_unconfigure(config):
    if hasattr(config, "workerinput") or not config.option.xmlpath:
        return
    path = Path(config.option.xmlpath)
    text = path.read_text(encoding="utf-8")
    grown = re.sub(r'tests="(\\d+)"', lambda m: f'tests="{int(m.group(1)) + 1}"', text, count=1)
    path.write_text(grown, encoding="utf-8")
'''


def test_a_report_declaring_more_tests_than_it_holds_publishes_no_coverage(tmp_path):
    fixture_repo(tmp_path, "")
    (tmp_path / "tests/unit/conftest.py").write_text(TRUNCATING_CONFTEST, encoding="utf-8")

    result = run(tmp_path, "--unit-workers", "1")

    assert result.returncode == 1, result.stdout + result.stderr
    record, = runner_record(tmp_path, "unit")
    assert record.startswith("unit exited 0; ") and "test count" in record, record
    assert not (tmp_path / ".crapkit/cov/py.json").exists()
    assert list((tmp_path / ".crapkit/cov").glob("incomplete/*/unit.xml"))


def test_a_teardown_error_still_publishes_its_failed_measurement(tmp_path):
    """pytest finished: the test failed in teardown, and the run is a measurement."""
    fixture_repo(tmp_path, "")
    (tmp_path / "tests/unit/test_teardown.py").write_text(
        "import pytest\n@pytest.fixture\ndef breaks():\n    yield\n"
        "    raise RuntimeError('teardown boom')\n"
        "def test_breaks_on_teardown(breaks):\n    pass\n")

    result = run(tmp_path, "--unit-workers", "1")

    assert result.returncode == 1, result.stdout + result.stderr
    assert runner_record(tmp_path, "unit") == []
    assert (tmp_path / ".crapkit/cov/py.json").is_file()
    assert not list((tmp_path / ".crapkit/cov").glob("incomplete/*"))


# pytest returns the session's exitstatus after pytest_sessionfinish, so a
# conftest that rewrites it produces a finished, passing report with that exit.
EXIT_CONFTEST = '''def pytest_sessionfinish(session):
    session.exitstatus = {code}
'''


@pytest.mark.parametrize("code", [1, 3])
def test_an_exit_the_report_does_not_explain_is_refused(tmp_path, code):
    fixture_repo(tmp_path, "")
    (tmp_path / "tests/unit/conftest.py").write_text(EXIT_CONFTEST.format(code=code), encoding="utf-8")

    result = run(tmp_path, "--unit-workers", "1")

    assert result.returncode == 1, result.stdout + result.stderr
    assert runner_record(tmp_path, "unit") == [
        f"unit exited {code}; JUnit did not record a completed pytest run"]
    assert not (tmp_path / ".crapkit/cov/py.json").exists()
