"""Parse JUnit XML into the set of failed test ids, and refuse a report that
says the run never finished. Pure.

An id is classname::name. Failures and errors count; skips and passes do not.
stdlib ElementTree is deliberate: the XML comes from the repo's own test
runner, the same trust class as the lane commands themselves. That trust runs
one way: the runner is believed about what it ran, including when it says it
stopped.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .errors import ToolError

# pytest-xdist logs a dead worker as a session-level report, so pytest's junitxml
# writes it as <error> rather than <failure>. The nodeid it names is the test the
# worker was in when it died, and the rest of that worker's queue is missing.
_CRASHED_WORKER = re.compile(r"worker '([^']+)' crashed while running '([^']+)'")


def _is_failure(case: ET.Element) -> bool:
    return case.find("failure") is not None or case.find("error") is not None


def _case_id(case: ET.Element) -> str:
    classname = case.get("classname") or case.get("file") or "?"
    return f"{classname}::{case.get('name', '?')}"


def _root(xml_text: str) -> ET.Element:
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ToolError(f"unparseable junit report: {exc}") from exc


def _walk(root: ET.Element) -> tuple[set[str], dict]:
    """One pass over the testcases: failed ids and the totals, together."""
    failed = set()
    tests = skipped = 0
    for case in root.iter("testcase"):
        tests += 1
        if case.find("skipped") is not None:
            skipped += 1
        if _is_failure(case):
            failed.add(_case_id(case))
    return failed, {"tests": tests, "skipped": skipped}


def _error_text(elem: ET.Element) -> str:
    """An error's message and body together. A runner that supplies neither
    still owes the reader a note, so the placeholder stands in for both."""
    return f"{elem.get('message') or ''} {elem.text or ''}".strip() or "no message"


def _crash_notes(root: ET.Element) -> list[str]:
    """Every crashed xdist worker the report names, with the test it died on."""
    found = (_CRASHED_WORKER.search(_error_text(e)) for e in root.iter("error"))
    return [f"worker {m.group(1)!r} crashed while running {m.group(2)!r}" for m in found if m]


def _session_notes(root: ET.Element) -> list[str]:
    """Errors outside every testcase: the runner errored the SESSION, so the
    cases it did write are what it managed before stopping, not the suite."""
    in_case = {id(e) for case in root.iter("testcase") for e in case.iter("error")}
    return [f"session error: {_error_text(e)}"
            for e in root.iter("error") if id(e) not in in_case]


def _refuse_unfinished(root: ET.Element) -> None:
    """A report that admits the run stopped early is not a measurement.

    pytest-xdist 3.8 does not reschedule a crashed worker's queue: the tests
    still in it never run and never appear in the report. coverage.py writes its
    JSON at session end regardless, so the lane reads as a complete suite while
    a quarter of the scope scores cov 0. Reported on a 15,300-test lane where
    one dead worker left 4,626 tests unexecuted (#21).
    """
    notes = _crash_notes(root) + _session_notes(root)
    if notes:
        raise ToolError("junit reports a run that did not finish, so its coverage measures "
                        f"a partial suite: {'; '.join(notes)}")


def suite_summary(xml_text: str) -> tuple[set[str], dict]:
    """(failed ids, {tests, skipped}) from ONE DOM and ONE walk.

    A lane needs both, and the two helpers below each parsed the same text, so
    every lane built and threw away a second DOM of a report that runs to 5 MB.
    Measured on a 2.8 MiB, 20,603-testcase report: 0.053s for the pair, 0.024s
    here.

    Refuses a report the runner did not finish, the way it refuses one with no
    testcases at all: both describe a suite that never ran, and a caller reading
    either as an answer records an untrustworthy lane as a measured one.
    """
    root = _root(xml_text)
    _refuse_unfinished(root)
    failed, counts = _walk(root)
    if counts["tests"] == 0:
        raise ToolError("junit report contains zero testcases — the suite crashed before collecting, not a pass")
    return failed, counts


def suite_counts(xml_text: str) -> dict:
    """Total and skipped testcase counts — suite decay (fewer tests, more skips)
    is invisible to a pass/fail check. Counts a zero-testcase report rather than
    rejecting it; the failed-id readers are the ones that must refuse it."""
    return _walk(_root(xml_text))[1]


def failed_test_ids(xml_text: str) -> set[str]:
    return suite_summary(xml_text)[0]


def _seconds(raw: str | None) -> float:
    """A hand-edited or absent time attribute costs nothing, never a crash."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _sum_times(elements) -> float:
    return sum(_seconds(e.get("time")) for e in elements)


def suite_seconds(xml_text: str) -> float:
    """Wall seconds the report claims, for costing a lane that did not run here.

    Suite totals first; a runner that times only its cases is summed case by
    case. Zero means the report carries no timing at all.
    """
    root = _root(xml_text)
    return _sum_times(root.iter("testsuite")) or _sum_times(root.iter("testcase"))
