"""SARIF results for ratchet regressions and scope ceilings, and junit reports
read where their attributes run out.

SARIF 2.1.0: a result carries its ruleId, level, message and a location whose
uri is repo-relative with `/` kept and other characters percent-encoded.
junitparse.py: a case without a classname is named by its `file` and one
without a name by `?`; only `error` elements speak for the runner (a crashed
worker, a session or collection error), each note read from the message, the
body, or `no message`; and a report's refusals are quoted whole.
"""
import pytest

from crapkit import junitparse, sarif
from crapkit.errors import ToolError
from crapkit.score import ScoredRow
from crapkit.verify import RatchetRegression, Verdict, sarif_results


def test_a_ratchet_regression_is_an_error_on_the_file_s_first_line():
    regression = RatchetRegression("src/a b.py", "f( )", 12.0, 13.5)
    assert sarif_results(Verdict.passing()._replace(ratchet_regressions=[regression])) == [{
        "ruleId": "crapkit/ratchet-regression", "level": "error",
        "message": {"text": "f( ): recorded 12.0 -> fresh 13.5"},
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/a%20b.py"},
                                            "region": {"startLine": 1}}}]}]


def test_a_row_under_its_scope_s_ceiling_is_no_result():
    row = ScoredRow("lib", "src/a.py", "f( )", 3, 9, 4, 4, 4, 7, 1, 1, 0.5, "measured", 20.0,
                    "add-tests")

    assert sarif.over_target_results([row], {"lib": 30}, 6) == []


def test_the_document_names_the_tool_s_home():
    driver = sarif.sarif_document([])["runs"][0]["tool"]["driver"]

    assert driver["informationUri"] == "https://github.com/JeanFrancoisGagne/crapkit"


def test_a_case_s_id_falls_back_to_its_file_and_a_question_mark():
    xml = ('<testsuite tests="2"><testcase file="tests/a.py" name="t"><failure/></testcase>'
           '<testcase classname="c"><failure/></testcase></testsuite>')

    assert junitparse.suite_summary(xml) == ({"tests/a.py::t", "c::?"}, {"tests": 2, "skipped": 0})


def test_a_failure_that_says_collection_failure_or_crashed_is_still_a_failure():
    """Only an `error` speaks for the runner; a test's own failure text is its own."""
    xml = ('<testsuite tests="2"><testcase classname="c" name="a">'
           '<failure message="collection failure">x</failure></testcase>'
           '<testcase classname="c" name="b">'
           "<failure>worker 'gw0' crashed while running 'c::a'</failure></testcase></testsuite>")

    assert junitparse.suite_summary(xml)[0] == {"c::a", "c::b"}


def test_every_session_error_is_named_from_what_it_carries():
    xml = ('<testsuite><error>boom</error><error message="bad"/><error/>'
           '<testcase classname="c" name="a"/></testsuite>')

    with pytest.raises(ToolError) as refused:
        junitparse.suite_summary(xml)

    assert str(refused.value) == (
        "junit reports a run that did not finish, so its coverage measures a partial suite: "
        "session error: boom; session error: bad; session error: no message")


@pytest.mark.parametrize("xml, message", [
    ("<testsuite/>",
     "junit report contains zero testcases - the suite crashed before collecting, not a pass"),
    ('<testsuite tests="3"><testcase classname="c" name="a"/></testsuite>',
     "junit test count does not match its cases; the report is incomplete"),
])
def test_a_report_that_is_no_measurement_is_refused_in_words(xml, message):
    with pytest.raises(ToolError) as refused:
        junitparse.suite_summary(xml)

    assert str(refused.value) == message


def test_a_report_with_no_suite_time_sums_its_cases_alone():
    xml = ('<testsuites time="5"><testsuite><testcase classname="c" name="a" time="1.5"/>'
           '<testcase classname="c" name="b" time="1.5"/></testsuite></testsuites>')

    assert junitparse.suite_seconds(xml) == 3.0
