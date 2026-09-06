"""Only a newly recorded passing test can clear a new failure."""
import sys

import pytest

from crapkit.config import Lane
from crapkit.lanes import retest_lane


def retry(tmp_path, report, code=0):
    script = tmp_path / "retry.py"
    script.write_text(
        "from pathlib import Path\n"
        f"Path('junit.xml').write_text({report!r}, encoding='utf-8')\n"
        f"raise SystemExit({code})\n", encoding="utf-8")
    lane = Lane("unit", "unused", "cov.json", "coveragepy", ("src",),
                results_artifact="junit.xml", timeout_seconds=5,
                retest_command=f'"{sys.executable}" "{script}"')
    return retest_lane(tmp_path, lane, {"test_math::fails"})


def test_an_unrelated_pass_does_not_clear_a_requested_failure(tmp_path):
    report = '<testsuite><testcase classname="other" name="passes"/></testsuite>'
    assert retry(tmp_path, report, code=2) == set()


@pytest.mark.parametrize("case,code,expected", [
    ('<testcase classname="test_math" name="fails"/>', 0, {"test_math::fails"}),
    ('<testcase classname="other" name="passes"/>', 0, set()),
    ('<testcase classname="test_math" name="fails"><skipped/></testcase>', 0, set()),
    ('<testcase classname="test_math" name="fails"/>', 2, set()),
    ('<testcase classname="test_math" name="fails"><failure/></testcase>', 1, set()),
])
def test_retry_requires_a_completed_requested_pass(tmp_path, case, code, expected):
    assert retry(tmp_path, f'<testsuite tests="1">{case}</testsuite>', code) == expected


def test_a_partial_report_cannot_forgive_a_case_it_did_record(tmp_path):
    report = '<testsuite tests="2"><testcase classname="test_math" name="fails"/></testsuite>'
    assert retry(tmp_path, report) == set()


def test_a_stale_passing_report_cannot_forgive_a_failure(tmp_path):
    (tmp_path / 'junit.xml').write_text(
        '<testsuite><testcase classname="test_math" name="fails"/></testsuite>', encoding='utf-8')
    lane = Lane('unit', 'unused', 'cov.json', 'coveragepy', ('src',),
                results_artifact='junit.xml', retest_command=f'"{sys.executable}" -c "pass"')
    assert retest_lane(tmp_path, lane, {'test_math::fails'}) == set()


@pytest.mark.parametrize('name', ['value[%CRAPKIT_LITERALS%]', 'value[!CRAPKIT_LITERALS!]',
                                'value[$(echo nope)]', 'value[a" & echo nope | ^ <>]'])
def test_retry_delivers_literal_test_ids(tmp_path, monkeypatch, name):
    monkeypatch.setenv('CRAPKIT_LITERALS', 'expanded')
    test_id = 'test_math::' + name
    script = tmp_path / 'args.py'
    script.write_text(
        'import sys, xml.etree.ElementTree as ET\n'
        'root = ET.Element("testsuite")\n'
        'for arg in sys.argv[1:]:\n'
        '    classname, name = arg.split("::", 1)\n'
        '    ET.SubElement(root, "testcase", classname=classname, name=name)\n'
        'ET.ElementTree(root).write("junit.xml", encoding="utf-8")\n', encoding='utf-8')
    lane = Lane('unit', 'unused', 'cov.json', 'coveragepy', ('src',),
                results_artifact='junit.xml', timeout_seconds=5,
                retest_command=f'"{sys.executable}" "{script}" {{tests}}')
    assert retest_lane(tmp_path, lane, {test_id}) == {test_id}
