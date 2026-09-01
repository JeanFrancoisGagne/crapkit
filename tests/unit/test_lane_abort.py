"""A junit that says the run did not finish is not a measurement.

Reported against 0.4.2 (#21). pytest-xdist 3.8 does not reschedule a crashed
worker's queue: on a 15,300-test lane one dead worker left 4,626 tests
unexecuted, coverage.py wrote its JSON at session end anyway, and crapkit
recorded the lane as finished and the run as a full coverage baseline. The
unexecuted functions scored cov 0 and every number downstream was wrong.

The fixtures are recorded junit: one carrying the crash error, one clean.
"""
import json
import sys
from pathlib import Path

import pytest

from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.junitparse import suite_summary
from crapkit.lanes import run_lane

RECORDED = Path(__file__).resolve().parent.parent / "fixtures" / "recorded"
CRASHED = (RECORDED / "junit_xdist_worker_crash.xml").read_text(encoding="utf-8")
CLEAN = (RECORDED / "junit_xdist_clean.xml").read_text(encoding="utf-8")
EMPTY = '<?xml version="1.0" encoding="utf-8"?><testsuites />'
NOOP = f'"{sys.executable}" -c ""'


def istanbul(root: Path) -> str:
    """One measured function, so the coverage half of the lane is beyond doubt."""
    key = str(root / "src" / "a.py")
    return json.dumps({key: {
        "path": key,
        "fnMap": {"0": {"name": "hot", "decl": {"start": {"line": 1}},
                        "loc": {"start": {"line": 1}, "end": {"line": 4}}}},
        "f": {"0": 1}, "branchMap": {}, "b": {},
        "statementMap": {"1": {"start": {"line": 2}, "end": {"line": 2}}}, "s": {"1": 1},
    }})


def lane_over(root: Path, junit: str, command: str = "never runs") -> Lane:
    (root / "cov.json").write_text(istanbul(root), encoding="utf-8")
    (root / "junit.xml").write_text(junit, encoding="utf-8")
    return Lane(name="py", command=command, artifact="cov.json", parser="istanbul",
                scopes=("src",), results_artifact="junit.xml")


def test_the_crash_report_names_the_worker_and_the_test_it_died_on():
    with pytest.raises(ToolError) as exc:
        suite_summary(CRASHED)

    assert "gw1" in str(exc.value)
    assert "tests/integration/test_pipeline.py::test_bulk_extract" in str(exc.value)


def test_a_crashed_worker_fails_the_lane_the_way_a_missing_artifact_does(tmp_path):
    """Exit 5: the lane is recorded failed, its scopes fall back to no-lane, the
    run is typed partial and no baseline reader will take it. The lane RAN here,
    which is where the refusal belongs: crapkit watched the suite produce this
    report, so the report is the whole story of what it measured."""
    lane = lane_over(tmp_path, CRASHED, NOOP)

    with pytest.raises(ToolError, match="gw1") as exc:
        run_lane(tmp_path, lane)

    assert exc.value.exit_code == 5


def test_a_missing_junit_still_fails_a_lane_that_ran(tmp_path):
    """A declared results_artifact the run did not write makes the no-NEW-failures
    check pass vacuously, so the lane fails instead."""
    lane = lane_over(tmp_path, CLEAN, NOOP)
    (tmp_path / "junit.xml").unlink()

    with pytest.raises(ToolError, match="results_artifact junit.xml is missing"):
        run_lane(tmp_path, lane)


def test_a_reused_junit_that_never_finished_warns_instead_of_failing(tmp_path, capsys):
    """--reuse-artifacts says run nothing, read what is on disk. One reporter got
    there by combining a killed run's coverage shards by hand: the coverage JSON
    was good, the junit beside it was the killed run's empty one, and the refusal
    left them deleting results_artifact from the config, which gives up the check
    on every future run instead of on this one."""
    lane = lane_over(tmp_path, EMPTY)

    outcome = run_lane(tmp_path, lane, reuse_artifact=True)

    assert "tests_total" not in outcome.provenance
    assert "failures" not in outcome.provenance
    assert outcome.coverage, "the coverage artifact is what the reuse came for"
    err = capsys.readouterr().err
    assert "junit.xml" in err and "zero testcases" in err
    assert "no-new-failures" in err


def test_a_reused_junit_that_is_gone_warns_instead_of_failing(tmp_path, capsys):
    """Same door, the other half: the file the config names is not there at all."""
    lane = lane_over(tmp_path, CLEAN)
    (tmp_path / "junit.xml").unlink()

    outcome = run_lane(tmp_path, lane, reuse_artifact=True)

    assert "tests_total" not in outcome.provenance
    assert "results_artifact junit.xml is missing" in capsys.readouterr().err


def test_a_clean_junit_still_measures_the_lane(tmp_path):
    lane = lane_over(tmp_path, CLEAN)

    outcome = run_lane(tmp_path, lane, reuse_artifact=True)

    assert outcome.provenance["tests_total"] == 3
    assert outcome.provenance["tests_skipped"] == 1
    assert outcome.provenance["failures"] == []


def test_an_error_outside_every_testcase_is_a_session_that_stopped():
    """A runner that errors the session itself writes the cases it managed
    before stopping. Counting those as the suite is the same lie."""
    xml = ('<testsuites><testsuite name="pytest" tests="1">'
           '<error message="INTERNALERROR: config teardown blew up" />'
           '<testcase classname="t" name="a" /></testsuite></testsuites>')

    with pytest.raises(ToolError, match="config teardown blew up"):
        suite_summary(xml)


def test_a_testcase_error_that_is_not_a_crash_is_still_just_a_failed_test():
    """The refusal is for reports that never finished. An ordinary errored test
    is a failure the lane already knows how to report."""
    xml = ('<testsuite tests="2"><testcase classname="t" name="a" />'
           '<testcase classname="t" name="b"><error message="fixture blew up" /></testcase>'
           '</testsuite>')

    failed, counts = suite_summary(xml)

    assert failed == {"t::b"} and counts == {"tests": 2, "skipped": 0}


def test_a_lane_that_ran_far_fewer_tests_than_the_last_trusted_run_is_named():
    """The reporter's own numbers: 10,674 of 15,300 after one worker died."""
    from crapkit.lanes import suite_drops

    (note,) = suite_drops({"py": {"tests_total": 15300}}, {"py": {"tests_total": 10674}})

    assert "lane 'py' ran 10674 tests, 4626 fewer" in note


def test_a_handful_of_deleted_tests_is_not_a_drop():
    """Deleting a test file is routine. A warning that fires on it is noise, and
    noise is what makes the real one invisible."""
    from crapkit.lanes import suite_drops

    assert suite_drops({"py": {"tests_total": 15300}}, {"py": {"tests_total": 15290}}) == []


def test_a_lane_the_last_trusted_run_never_measured_cannot_drop():
    """A new lane, or one whose junit the old run had no count for. There is
    nothing to compare, and inventing zero would flag every first run."""
    from crapkit.lanes import suite_drops

    assert suite_drops({}, {"py": {"tests_total": 10}}) == []
    assert suite_drops({"py": {}}, {"py": {"tests_total": 10}}) == []


def test_coverage_warns_off_the_last_trusted_run_before_writing_this_one(tmp_path, capsys):
    """The wiring: the comparison reads a run this command has not touched."""
    from crapkit.cli.scoring import _warn_suite_drop
    from crapkit.snapshot import InventoryRow
    from crapkit.store import SnapshotStore

    store = SnapshotStore(tmp_path / "crap.sqlite")
    rows = [InventoryRow("src", "src/a.py", "hot( n )", 1, 9, 7, 5, 5, 8, 1, 2)]
    store.write_run(commit="a" * 40, tool_versions={}, rows=rows, kind="coverage",
                    lanes={"py": {"tests_total": 15300}})

    _warn_suite_drop(store, {"py": {"tests_total": 10674}})

    assert "4626 fewer" in capsys.readouterr().err


def test_a_crash_during_a_flake_retest_keeps_every_failure(tmp_path):
    """`retest_lane` only drops ids the rerun's own artifact says passed. A
    report that crashed proves nothing, so nothing drops."""
    from crapkit.lanes import _still_failed

    (tmp_path / "junit.xml").write_text(CRASHED, encoding="utf-8")
    lane = Lane(name="py", command="never runs", artifact="cov.json", parser="istanbul",
                scopes=("src",), results_artifact="junit.xml")

    assert _still_failed(tmp_path, lane) is None
