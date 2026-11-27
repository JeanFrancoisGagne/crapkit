"""JUnit XML for `crapkit score --junit FILE`: one test case per scored
function, failed when its CRAP is over the limit the text report uses.

CI systems that show test reports read it as they read a test run. Each case's
classname and file are the repo-relative path and its name the function's
ratchet key name: the long name, with `#2`, `#3` on later functions a file gives
the same one, so no reader drops a twin as a duplicate. The file is replaced
whole, so a reader never sees part of one.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from . import __version__
from .keys import key_names, key_of
from .score import over_ceiling

if TYPE_CHECKING:
    from pathlib import Path

    from .score import ScoredRow

FORMULA = "CRAP = ccn^2 * (1 - cov)^3 + ccn"


def _source_order(row: ScoredRow) -> tuple:
    return (row.path, row.start, row.occurrence, row.long_name)


def _case(suite: ET.Element, row: ScoredRow, name: str, limit: int) -> bool:
    """Append the row's test case, with a failure when its CRAP is over
    `limit`; True when it failed."""
    case = ET.SubElement(suite, "testcase", classname=row.path, name=name, file=row.path)
    failed = over_ceiling(row.crap, limit)
    if failed:
        message = f"CRAP {row.crap:.1f} over {limit} (ccn {row.ccn}, cov {row.cov:.0%})"
        ET.SubElement(case, "failure", message=message).text = f"{row.path}:{row.start}"
    return failed


def report_bytes(scored: list[ScoredRow], limit: int) -> bytes:
    """UTF-8 XML: one <testsuites> holding one <testsuite name="crapkit">
    whose tests and failures count its cases, whose properties name the
    crapkit version and the formula, and whose cases follow source order."""
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", name="crapkit")
    properties = ET.SubElement(suite, "properties")
    for name, value in (("version", __version__), ("formula", FORMULA)):
        ET.SubElement(properties, "property", name=name, value=value)
    keys = key_names(scored)
    failures = sum(_case(suite, row, key_of(keys, row)[1], limit)
                   for row in sorted(scored, key=_source_order))
    suite.set("tests", str(len(scored)))
    suite.set("failures", str(failures))
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def write(path: Path, scored: list[ScoredRow], limit: int) -> None:
    """The report written beside `path` and moved over it, so a write that
    fails leaves the file that was there and no partial one."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(report_bytes(scored, limit))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
