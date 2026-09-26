"""Control: verify's flake retest reads a UTF-8 rerun report as 0.8.0 did, through verify.

test_junit_encodings.py pins this row by calling lanes._retested_passes with
0.8.1's two-argument signature, so on 0.8.0 it stops at a TypeError before its
verdict. This test replays the row through `verify`: t::c1 fails, the lane's
retest_command rewrites the junit report in one UTF-8 form with t::c1
passing, and verify clears it as a flake. 0.8.0 read these four forms and so
does every later tree; the other encodings are 0.8.1's addition.
"""
from __future__ import annotations

import codecs
import json

import pytest

from cli_inproc_repo import commit_all, repo, template_repo  # noqa: F401
from test_lane_results_absence import counted, junit, run  # noqa: F401

BODY = ('<testsuites><testsuite name="t" tests="1" time="1.5">'
        '<testcase classname="t" name="c1" time="1.0"/></testsuite></testsuites>\n')
DECLARED = f'<?xml version="1.0" encoding="utf-8"?>\n{BODY}'
FORMS = {
    "results-bom": codecs.BOM_UTF8 + DECLARED.encode(),
    "results-crlf": DECLARED.replace("\n", "\r\n").encode(),
    "results-utf8": DECLARED.encode(),
    "results-undeclared-utf8": BODY.encode(),
}


@pytest.mark.parametrize("form", sorted(FORMS))
def test_a_retest_report_in_a_utf8_form_clears_the_flake(counted, capsys, form):  # noqa: F811
    (counted / "passing.xml").write_bytes(FORMS[form])
    (counted / "retest.py").write_text(
        "import shutil\nshutil.copyfile('passing.xml', 'junit.xml')\n", encoding="utf-8")
    config = counted / "crapkit.toml"
    declared = 'results_artifact = "junit.xml"\n'
    config.write_text(config.read_text(encoding="utf-8").replace(
        declared, declared + 'retest_command = "python retest.py {tests}"\n', 1),
        encoding="utf-8")
    commit_all(counted, "retest command")
    junit(counted, 20, "t::c1")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], counted, capsys)

    payload = json.loads(out)
    assert code == 0, out + err
    assert (payload["new_failures"], payload["retried_passes"]) == ([], ["t::c1"])
