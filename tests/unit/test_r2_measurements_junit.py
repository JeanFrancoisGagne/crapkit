"""Normal measurements and retries agree about declared test counts."""
import pytest

from crapkit.errors import ToolError
from crapkit.junitparse import passed_test_ids, suite_summary
from crapkit.lanes import run_lane
from test_lane_abort import lane_over, writing

CASE = '<testcase classname="tests.a" name="works"/>'


@pytest.mark.parametrize("xml", [
    f'<testsuite tests="10">{CASE}</testsuite>',
    f'<testsuites tests="2"><testsuite tests="1">{CASE}</testsuite></testsuites>',
    f'<testsuites><testsuite tests="0">{CASE}</testsuite></testsuites>',
])
def test_fresh_lane_refuses_declared_count_mismatch(tmp_path, xml):
    lane = lane_over(tmp_path, xml, writing(tmp_path, "cov.json", "junit.xml"))
    with pytest.raises(ToolError, match="test count"):
        run_lane(tmp_path, lane)


@pytest.mark.parametrize("reader", [suite_summary, passed_test_ids])
@pytest.mark.parametrize("declared", ['-1', '1.5', 'true', '2'])
def test_aggregate_declared_counts_are_admitted(reader, declared):
    xml = f'<testsuites tests="{declared}"><testsuite>{CASE}</testsuite></testsuites>'
    with pytest.raises(ToolError, match="test count"):
        reader(xml)


@pytest.mark.parametrize("xml", [
    f'<testsuite>{CASE}</testsuite>',
    f'<testsuites tests="1"><testsuite tests="1">{CASE}</testsuite></testsuites>',
    f'<testsuite tests="1"><testsuite tests="1">{CASE}</testsuite></testsuite>',
])
def test_absent_and_nested_aggregate_counts_remain_compatible(xml):
    assert suite_summary(xml) == (set(), {"tests": 1, "skipped": 0})
    assert passed_test_ids(xml) == {"tests.a::works"}


def test_completed_brownfield_failure_is_a_measurement():
    xml = '<testsuite tests="2">' + CASE + '<testcase classname="tests.a" name="old"><failure/></testcase></testsuite>'
    assert suite_summary(xml) == ({"tests.a::old"}, {"tests": 2, "skipped": 0})
    assert passed_test_ids(xml) == {"tests.a::works"}


def test_explicit_reuse_keeps_coverage_but_drops_incomplete_junit(tmp_path, capsys):
    lane = lane_over(tmp_path, f'<testsuite tests="10">{CASE}</testsuite>')
    outcome = run_lane(tmp_path, lane, reuse_artifact=True)
    assert outcome.coverage
    assert "tests_total" not in outcome.provenance
    assert "failures" not in outcome.provenance
    assert "test count" in capsys.readouterr().err
