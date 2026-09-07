"""An xdist collection error cannot support a full run or forgive a failure."""
import pytest

from crapkit.errors import ToolError
from crapkit.junitparse import failed_test_ids, passed_test_ids, suite_summary
from crapkit.lanes import run_lane
from test_lane_abort import lane_over, writing
from test_retest_evidence import retry


# The marker and testcase placement come from real xdist 3.8 output. A completed
# pass beside the collection error tests the retry path that could forgive it.
COLLECTION_FAILURE = (
    '<testsuite tests="2"><testcase classname="test_math" name="fails"/>'
    '<testcase classname="" name="tests.unit.test_broken">'
    '<error message="collection failure">SyntaxError: invalid syntax</error>'
    '</testcase></testsuite>')


@pytest.mark.parametrize("reader", [suite_summary, failed_test_ids, passed_test_ids])
def test_collection_failure_refuses_every_measurement_reader(reader):
    with pytest.raises(ToolError, match="collection"):
        reader(COLLECTION_FAILURE)


def test_collection_failure_refuses_a_lane_that_just_wrote_coverage(tmp_path):
    lane = lane_over(tmp_path, COLLECTION_FAILURE, writing(tmp_path, "cov.json", "junit.xml"))
    with pytest.raises(ToolError, match="collection") as failure:
        run_lane(tmp_path, lane)
    assert failure.value.exit_code == 5


def test_collection_failure_cannot_forgive_the_requested_pass_it_recorded(tmp_path):
    assert retry(tmp_path, COLLECTION_FAILURE) == set()


def test_ordinary_fixture_error_does_not_claim_that_collection_stopped():
    report = COLLECTION_FAILURE.replace('message="collection failure"',
                                        'message="failed on setup with RuntimeError"')
    failed, counts = suite_summary(report)
    assert failed == {"?::tests.unit.test_broken"}
    assert counts == {"tests": 2, "skipped": 0}
    assert passed_test_ids(report) == {"test_math::fails"}
