"""A junit report decodes the way its XML declaration and byte-order mark say.

pytest, vitest and jest write UTF-8, but a report can come from a runner on a
legacy code page or from a step that re-encodes it: `encoding="ISO-8859-1"`
with a test named `test_café`, or UTF-16 with a BOM. crapkit decoded every
report as UTF-8 before XML saw it, so a well-formed report ended `coverage`,
`verify` (--reuse-artifacts too, which promises a warning), the flake retest
and `doctor --tune` with a UnicodeDecodeError. Each reader now hands the
parser bytes.
"""
import codecs

import pytest

from crapkit.cli.admin import _junit_seconds
from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.junitparse import failed_test_ids, passed_test_ids, suite_seconds, suite_summary
from crapkit.lanes import _results_provenance, _results_summary, _retested_passes, _still_failed

BODY = ('<testsuites><testsuite name="py" tests="2" failures="1" time="1.5">'
        '<testcase classname="t" name="test_café" time="1.0"/>'
        '<testcase classname="t" name="test_broken" time="0.5"><failure message="no"/></testcase>'
        '</testsuite></testsuites>\n')


def _declared(encoding: str) -> str:
    return f'<?xml version="1.0" encoding="{encoding}"?>\n{BODY}'


REPORTS = [
    # id, the report's bytes
    ("results-latin1", _declared("ISO-8859-1").encode("latin-1")),
    ("results-cp1252", _declared("windows-1252").encode("cp1252")),
    ("results-utf16", codecs.BOM_UTF16_LE + _declared("UTF-16").encode("utf-16-le")),
    ("results-utf16-be", codecs.BOM_UTF16_BE + _declared("UTF-16").encode("utf-16-be")),
    ("results-bom", codecs.BOM_UTF8 + _declared("utf-8").encode()),
    ("results-crlf", _declared("utf-8").replace("\n", "\r\n").encode()),
    ("results-utf8", _declared("utf-8").encode()),
    ("results-undeclared-utf8", BODY.encode()),
]
FAILED = {"t::test_broken"}
PASSED = {"t::test_café"}
LANE = Lane(name="py", command="never runs", artifact="cov.json", parser="coveragepy",
            scopes=("src",), results_artifact="junit.xml")
IDS = [row[0] for row in REPORTS]
RAW = [row[1] for row in REPORTS]


@pytest.mark.parametrize("raw", RAW, ids=IDS)
def test_every_junit_reader_decodes_the_report_as_it_declares(raw):
    assert suite_summary(raw) == (FAILED, {"tests": 2, "skipped": 0})
    assert failed_test_ids(raw) == FAILED
    assert passed_test_ids(raw) == PASSED
    assert suite_seconds(raw) == 1.5


@pytest.mark.parametrize("raw", RAW, ids=IDS)
def test_a_lane_reads_its_results_artifact_in_any_declared_encoding(tmp_path, raw):
    """coverage and verify, and verify --reuse-artifacts."""
    (tmp_path / "junit.xml").write_bytes(raw)

    failed, counts, _ = _results_summary(tmp_path, LANE)
    reused = _results_provenance(tmp_path, LANE, reuse_artifact=True)

    assert (failed, counts) == (FAILED, {"tests": 2, "skipped": 0})
    assert (reused["failures"], reused["tests_total"]) == (sorted(FAILED), 2)


@pytest.mark.parametrize("raw", RAW, ids=IDS)
def test_the_flake_retest_reads_the_rerun_report_in_any_declared_encoding(tmp_path, raw):
    (tmp_path / "junit.xml").write_bytes(raw)

    assert _retested_passes(tmp_path, LANE, before=None) == PASSED
    assert _still_failed(tmp_path, LANE) == FAILED


@pytest.mark.parametrize("raw", RAW, ids=IDS)
def test_doctor_tune_costs_a_lane_from_a_report_in_any_declared_encoding(tmp_path, raw):
    (tmp_path / "junit.xml").write_bytes(raw)

    assert _junit_seconds(tmp_path / "junit.xml") == 1.5


def test_undeclared_latin1_bytes_are_an_unparseable_report_not_a_traceback(tmp_path, capsys):
    """XML reads a report with no declaration as UTF-8, so a raw 0xE9 is a
    malformed report: a refusal for a run, a warning for a reuse."""
    (tmp_path / "junit.xml").write_bytes(BODY.encode("latin-1"))

    with pytest.raises(ToolError, match="unparseable junit report"):
        _results_summary(tmp_path, LANE)
    assert _results_provenance(tmp_path, LANE, reuse_artifact=True) == {}
    assert "reused junit.xml and cannot check it: unparseable junit report" in capsys.readouterr().err
