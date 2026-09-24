"""JUnit seam: report XML in, set of failed test ids out. Pure.

Both pytest --junitxml and vitest --reporter=junit emit this shape.
"""
import pytest

from crapkit.errors import ToolError
from crapkit.junitparse import failed_test_ids, suite_summary

XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="4" failures="1" errors="1" skipped="1">
    <testcase classname="tests.test_a" name="test_ok"/>
    <testcase classname="tests.test_a" name="test_broken">
      <failure message="assert 1 == 2">trace</failure>
    </testcase>
    <testcase classname="tests.test_b" name="test_crashed">
      <error message="boom">trace</error>
    </testcase>
    <testcase classname="tests.test_b" name="test_skipped">
      <skipped/>
    </testcase>
  </testsuite>
</testsuites>
"""


def test_failures_and_errors_count_skips_and_passes_do_not():
    ids = failed_test_ids(XML)
    assert ids == {"tests.test_a::test_broken", "tests.test_b::test_crashed"}


def test_nested_testsuites_are_walked():
    nested = XML.replace("<testsuites>", "<testsuites><testsuite name='outer'>").replace(
        "</testsuites>", "</testsuite></testsuites>")
    assert len(failed_test_ids(nested)) == 2


def test_malformed_xml_is_loud():
    with pytest.raises(ToolError, match="junit"):
        failed_test_ids("<not-closed")


def test_zero_testcase_report_is_loud_not_a_pass():
    empty = '<?xml version="1.0"?><testsuites><testsuite name="crashed" tests="0"></testsuite></testsuites>'
    with pytest.raises(ToolError, match="zero testcases"):
        failed_test_ids(empty)


def test_suite_counts_totals_and_skips():
    from crapkit.junitparse import suite_counts
    xml = ('<testsuite>'
           '<testcase classname="t" name="a"/>'
           '<testcase classname="t" name="b"><skipped/></testcase>'
           '<testcase classname="t" name="c"><failure/></testcase>'
           '</testsuite>')
    assert suite_counts(xml) == {"tests": 3, "skipped": 1}


def test_suite_summary_returns_both_answers_from_one_report():
    from crapkit.junitparse import suite_summary

    failed, counts = suite_summary(XML)
    assert failed == {"tests.test_a::test_broken", "tests.test_b::test_crashed"}
    assert counts == {"tests": 4, "skipped": 1}


def test_suite_summary_builds_the_dom_once(monkeypatch):
    """A lane's junit file reaches 5 MB; two DOMs of it is pure waste."""
    import xml.etree.ElementTree as ET

    from crapkit import junitparse

    parses = []
    real = ET.fromstring
    monkeypatch.setattr(junitparse.ET, "fromstring",
                        lambda text: parses.append(1) or real(text))
    junitparse.suite_summary(XML)
    assert len(parses) == 1


def test_suite_summary_is_loud_on_a_crashed_collector():
    empty = '<?xml version="1.0"?><testsuites><testsuite name="crashed" tests="0"></testsuite></testsuites>'
    from crapkit.junitparse import suite_summary

    with pytest.raises(ToolError, match="zero testcases"):
        suite_summary(empty)


# --- every shape another runner or a hand-rolled writer hands over ------------
#
# jest-junit, vitest, surefire and hand-rolled writers each leave out a
# different attribute. Each shape below either reads to the ids and counts the
# report means or is refused with a line saying why; none ends in a traceback.

_HEAD = '<?xml version="1.0" encoding="utf-8"?>'
_PASS = ['<testcase classname="tests.test_mod" name="test_grade" time="0.003" />',
         '<testcase classname="tests.test_mod" name="test_mean" time="0.001" />']


def _suite(cases, tests=None, root="testsuites"):
    count = len(cases) if tests is None else tests
    attr = "" if count is ... else f' tests="{count}"'
    body = f'<testsuite name="pytest" errors="0" failures="0"{attr}>' + "".join(cases) + "</testsuite>"
    return _HEAD + (f"<testsuites>{body}</testsuites>" if root == "testsuites" else body)


_READS = {
    "testcase-name-absent": (_suite(['<testcase classname="tests.test_mod" />', _PASS[1]]), set(), 2),
    "testcase-classname-absent": (_suite(['<testcase name="test_grade" file="tests/test_mod.py" />',
                                          _PASS[1]]), set(), 2),
    "testcase-both-absent-failing": (_suite(['<testcase><failure message="x"/></testcase>', _PASS[1]]),
                                     {"?::?"}, 2),
    "failure-without-message": (_suite(['<testcase classname="tests.test_mod" name="test_grade">'
                                        '<failure/></testcase>', _PASS[1]]),
                                {"tests.test_mod::test_grade"}, 2),
    "bare-testsuite-root": (_suite(_PASS, root="testsuite"), set(), 2),
    "tests-attribute-absent": (_suite(_PASS, tests=...), set(), 2),
    "non-ascii-test-name": (_suite(['<testcase classname="tests.test_mod" name="test_café[世]"><failure/>'
                                    '</testcase>', _PASS[1]]), {"tests.test_mod::test_café[世]"}, 2),
    "20000-testcases": (_suite([f'<testcase classname="t" name="test_{i}" />' for i in range(20000)]),
                        set(), 20000),
}


@pytest.mark.parametrize("shape", list(_READS))
def test_a_report_shape_reads_to_the_failures_and_count_it_means(shape):
    xml, failed, tests = _READS[shape]

    ids, counts = suite_summary(xml)

    assert (ids, counts["tests"]) == (failed, tests)


@pytest.mark.parametrize("xml, words", [
    (_suite(_PASS, tests=""), "test count does not match"),
    (_suite(_PASS, tests="2.0"), "test count does not match"),
    ("", "unparseable junit report"),
    (_suite([]), "zero testcases"),
], ids=["tests-attribute-empty", "tests-attribute-a-float", "empty-file", "zero-testcases"])
def test_a_report_that_cannot_be_counted_is_refused_by_name(xml, words):
    with pytest.raises(ToolError, match=words):
        suite_summary(xml)
