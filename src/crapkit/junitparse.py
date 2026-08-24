"""Parse JUnit XML into the set of failed test ids. Pure.

An id is classname::name. Failures and errors count; skips and passes do not.
stdlib ElementTree is deliberate: the XML comes from the repo's own test
runner, the same trust class as the lane commands themselves.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .errors import ToolError


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


def suite_summary(xml_text: str) -> tuple[set[str], dict]:
    """(failed ids, {tests, skipped}) from ONE DOM and ONE walk.

    A lane needs both, and the two helpers below each parsed the same text, so
    every lane built and threw away a second DOM of a report that runs to 5 MB.
    Measured on a 2.8 MiB, 20,603-testcase report: 0.053s for the pair, 0.024s
    here.
    """
    failed, counts = _walk(_root(xml_text))
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
