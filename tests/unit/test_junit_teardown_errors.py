"""A pytest run whose test errors in teardown finished, and JUnit admission says so.

pytest's junitxml counts records, not testcases, in `tests=`. A teardown error
is its own record: it sits beside the test's own result in one testcase (a pass,
a skip or a setup error), or, after a call failure, in a second testcase with the
same id, and pytest then subtracts that split. The reports below are what pytest
8.3.3 wrote for each shape, trimmed of timings and tracebacks.
"""
import subprocess
import sys

import pytest

from crapkit.errors import ToolError
from crapkit.junitparse import failed_test_ids, passed_test_ids, suite_summary

TEARDOWN = 'failed on teardown with &quot;RuntimeError: teardown boom&quot;'

PASS_THEN_TEARDOWN = (
    '<testsuites><testsuite name="pytest" errors="1" failures="0" skipped="0" tests="2">'
    '<testcase classname="test_m" name="test_pass_teardown_err">'
    f'<error message="{TEARDOWN}"/></testcase></testsuite></testsuites>')
SKIP_THEN_TEARDOWN = (
    '<testsuites><testsuite name="pytest" errors="1" failures="0" skipped="1" tests="2">'
    '<testcase classname="test_m" name="test_skip_teardown_err">'
    '<skipped type="pytest.skip" message="s"/>'
    f'<error message="{TEARDOWN}"/></testcase></testsuite></testsuites>')
SETUP_THEN_TEARDOWN = (
    '<testsuites><testsuite name="pytest" errors="2" failures="0" skipped="0" tests="2">'
    '<testcase classname="test_s" name="test_setup_and_teardown_err">'
    '<error message="failed on setup with &quot;RuntimeError: setup boom&quot;"/>'
    f'<error message="{TEARDOWN}"/></testcase></testsuite></testsuites>')
FAIL_THEN_TEARDOWN = (
    '<testsuites><testsuite name="pytest" errors="1" failures="1" skipped="0" tests="1">'
    '<testcase classname="test_m" name="test_fail_teardown_err"><failure message="assert False"/></testcase>'
    '<testcase classname="test_m" name="test_fail_teardown_err">'
    f'<error message="{TEARDOWN}"/></testcase></testsuite></testsuites>')


@pytest.mark.parametrize(("xml", "failed"), [
    (PASS_THEN_TEARDOWN, "test_m::test_pass_teardown_err"),
    (SKIP_THEN_TEARDOWN, "test_m::test_skip_teardown_err"),
    (SETUP_THEN_TEARDOWN, "test_s::test_setup_and_teardown_err"),
    (FAIL_THEN_TEARDOWN, "test_m::test_fail_teardown_err"),
], ids=["pass", "skip", "setup-error", "call-failure"])
def test_a_teardown_error_is_a_finished_failure_not_a_partial_report(xml, failed):
    assert suite_summary(xml)[0] == {failed}
    assert failed_test_ids(xml) == {failed}
    assert passed_test_ids(xml) == set()


def test_a_report_still_declaring_more_than_its_records_is_refused():
    truncated = PASS_THEN_TEARDOWN.replace('tests="2"', 'tests="3"')
    with pytest.raises(ToolError, match="test count"):
        suite_summary(truncated)


def test_a_split_record_still_needs_the_failure_it_continues():
    """The second testcase is pytest's split only when its id already ran."""
    lone = FAIL_THEN_TEARDOWN.replace('name="test_fail_teardown_err"><failure',
                                      'name="other"><failure')
    with pytest.raises(ToolError, match="test count"):
        suite_summary(lone)


MODULE = '''import pytest

@pytest.fixture
def breaks_on_teardown():
    yield
    raise RuntimeError("teardown boom")

@pytest.fixture
def breaks_on_both(breaks_on_teardown):
    raise RuntimeError("setup boom")

def test_passes(breaks_on_teardown):
    pass

def test_skips(breaks_on_teardown):
    pytest.skip("skipped")

def test_fails(breaks_on_teardown):
    assert False

def test_setup_fails(breaks_on_both):
    pass

def test_clean():
    pass
'''


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_pytest_reports_with_teardown_errors_are_admitted(tmp_path, workers):
    (tmp_path / "test_teardown.py").write_text(MODULE, encoding="utf-8")
    report = tmp_path / "junit.xml"
    done = subprocess.run([sys.executable, "-m", "pytest", "test_teardown.py", "-p", "no:randomly",
                           "-p", "no:cacheprovider", *workers, f"--junitxml={report}"],
                          cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 1, done.stdout + done.stderr
    failed, _ = suite_summary(report.read_text(encoding="utf-8"))
    assert failed == {f"test_teardown::{name}" for name in (
        "test_passes", "test_skips", "test_fails", "test_setup_fails")}
