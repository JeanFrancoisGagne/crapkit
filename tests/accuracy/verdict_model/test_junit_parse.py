"""JUnit results: which tests crapkit reads as failed, and which reports it refuses.

The reports under recorded/ are real: pytest 8.3, 9.0 and 9.1, vitest 5.0.1
and jest-junit 17 ran the suites record_junit.py writes (a pass, a failure,
setup and teardown errors, a skip, an xfail and an xpass, a parametrized id,
a class method; a collection error; a crashed xdist worker; a suite with no
tests). Two outside readers say what each holds:

- junitparser 5 reads the XML: a test failed when its <testcase> holds a
  <failure> or an <error>, and its id is `classname::name` (agent-json.md,
  Findings: new_failures);
- pytest-reportlog, which recorded the same pytest run: a test failed when any
  of its setup, call or teardown reports failed, its nodeid mapped to the
  classname pytest's junitxml writes (the dotted module, then any class).

crapkit reads each report as a lane's results_artifact. `coverage --json`
lists the lane's failures and counts, and `verify --json` its new failures
against a baseline that had none. The refusals follow the table in
docs/lanes.md#a-junit-that-says-the-run-did-not-finish.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path

from junitparser import Error, Failure, JUnitXml, Skipped, TestSuite
import pytest

from accuracy.verdict_model import verdict_world as vw

RECORDED = Path(__file__).resolve().parent / "recorded"
PYTESTS = ("8.3", "9.0", "9.1")
MIXED = [f"pytest-{version}/mixed.xml" for version in PYTESTS] + [
    "vitest-5.0.1/mixed.xml", "jest-junit-17.0.0/mixed.xml"]
# docs/lanes.md#a-junit-that-says-the-run-did-not-finish, and :1339 for zero testcases.
REFUSED = {f"pytest-{version}/{suite}.xml": reason for version in PYTESTS
           for suite, reason in (("collection_error", "collection failure"),
                                 ("worker_crash", "crashed while running"),
                                 ("zero_testcases", "zero testcases"))}
BASE = vw.World().with_fn("app", vw.Fn("f", 1, 2)).with_fn("lib", vw.Fn("g", 1, 2)) \
    .with_test(vw.Test("t1")).with_test(vw.Test("t2", lane="b"))


@dataclass(frozen=True)
class Reading:
    failed: frozenset
    total: int
    skipped: int


def _cases(path: Path) -> list:
    xml = JUnitXml.fromfile(str(path))
    suites = [xml] if isinstance(xml, TestSuite) else list(xml)
    return [case for suite in suites for case in suite]


def _has(case, kinds) -> bool:
    return any(isinstance(result, kinds) for result in case.result)


def junitparser_reading(path: Path) -> Reading:
    cases = _cases(path)
    failed = frozenset(f"{c.classname}::{c.name}" for c in cases if _has(c, (Failure, Error)))
    return Reading(failed, len(cases), sum(1 for c in cases if _has(c, Skipped)))


def junit_id(nodeid: str) -> str:
    """pytest's junitxml classname: the module path dotted, then any class."""
    parts = nodeid.split("::")
    module = parts[0].removesuffix(".py").replace("/", ".")
    return ".".join([module, *parts[1:-1]]) + "::" + parts[-1]


def reportlog_failures(path: Path) -> frozenset:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return frozenset(junit_id(row["nodeid"]) for row in rows
                     if row["$report_type"] == "TestReport" and row["outcome"] == "failed")


@pytest.fixture(scope="module")
def clean(seeded_world):
    """A baseline whose lanes both passed every test."""
    return seeded_world(BASE)


def _with_report(clean, tmp_path, text: str) -> vw.Scenario:
    scenario = clean.copy(tmp_path / "repo")
    return scenario.set(replace(BASE, raw_junit=(("a", text),)))


def _text(name: str) -> str:
    return (RECORDED / name).read_bytes().decode("utf-8")


# --- the two readers agree before crapkit is asked ------------------------------------------

@pytest.mark.parametrize("name", [n for n in MIXED if n.startswith("pytest")])
def test_junitparser_and_reportlog_name_the_same_failures(name):
    report = RECORDED / name
    assert junitparser_reading(report).failed == reportlog_failures(report.with_suffix(".jsonl"))


# --- what crapkit reads ------------------------------------------------------------------------

@pytest.mark.process
@pytest.mark.parametrize("name", MIXED)
def test_lane_failures_and_counts_match_junitparser(clean, tmp_path, name):
    scenario = _with_report(clean, tmp_path, _text(name))
    expected = junitparser_reading(RECORDED / name)

    lane = scenario.json("coverage")["lanes"]["a"]

    assert (frozenset(lane["failures"]), lane["tests_total"], lane["tests_skipped"]) == \
        (expected.failed, expected.total, expected.skipped)


@pytest.mark.process
@pytest.mark.parametrize("name", MIXED)
def test_new_failures_are_the_report_s_failures(clean, tmp_path, name):
    """Against a baseline whose lanes failed nothing, every failure is new."""
    scenario = _with_report(clean, tmp_path, _text(name))

    result = scenario.run("verify", "--json")

    assert result.code == 8, result.stderr
    assert frozenset(result.json()["new_failures"]) == junitparser_reading(RECORDED / name).failed


@pytest.mark.process
@pytest.mark.parametrize("version", PYTESTS)
def test_teardown_errors_across_pytest(clean, tmp_path, version):
    """A teardown error fails its test, on a test that passed and on one that
    had already failed, in every pytest the lanes are recorded under."""
    name = f"pytest-{version}/mixed.xml"
    scenario = _with_report(clean, tmp_path, _text(name))

    failures = frozenset(scenario.json("coverage")["lanes"]["a"]["failures"])

    teardown = {"tests.test_mixed::test_teardown_error", "tests.test_mixed::test_fail_and_teardown_error"}
    assert teardown <= reportlog_failures(RECORDED / name.replace(".xml", ".jsonl"))
    assert teardown <= failures


# --- reports that say the run did not finish ----------------------------------------------------

def _refusal(scenario: vw.Scenario) -> str:
    result = scenario.run("coverage", "--json")
    assert result.code == 5, result.stdout + result.stderr
    payload = result.json()
    assert payload["kind"] == "partial" and "a" not in payload["lanes"]
    return payload["lane_failures"]["a"]


@pytest.mark.process
@pytest.mark.parametrize("name", sorted(REFUSED))
def test_collection_error_and_worker_crash_refuse(clean, tmp_path, name):
    """The lane fails, its scope scores no-lane, the run is partial; the
    refusal names what the report admits."""
    reason = _refusal(_with_report(clean, tmp_path, _text(name)))

    assert REFUSED[name] in reason


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_junitparser_sees_what_each_refused_report_admits(name):
    cases = _cases(RECORDED / name)
    messages = [result.message or "" for case in cases for result in case.result]
    admitted = {"collection failure": "collection failure" in messages,
                "crashed while running": any("crashed while running" in m for m in messages),
                "zero testcases": not cases}
    assert admitted[REFUSED[name]]


# --- hand-written reports, one per row of the docs table -----------------------------------------

def _case(name: str, body: str = "") -> str:
    return f'<testcase classname="tests.test_a" name="{name}">{body}</testcase>'


def _suite(cases: str, tests: str | None) -> str:
    declared = "" if tests is None else f' tests="{tests}"'
    return f'<?xml version="1.0"?><testsuites><testsuite name="s"{declared}>{cases}</testsuite></testsuites>'


PASS2 = _case("t1") + _case("t2")
HAND = [
    ("count-matches", _suite(PASS2, "2"), None, "docs/lanes.md:1316-1319"),
    ("count-short", _suite(PASS2, "3"), "declared", "lanes.md:1314 a tests count that differs"),
    ("count-negative", _suite(PASS2, "-2"), "declared", "lanes.md:1317 nonnegative decimal integers"),
    ("count-not-decimal", _suite(PASS2, "2.0"), "declared", "lanes.md:1317 decimal integers"),
    ("no-count", _suite(PASS2, None), None, "lanes.md:1318-1319 no declared counts is accepted"),
    ("wrapper-count", _suite(PASS2, "2").replace("<testsuites>", '<testsuites tests="5">'), "declared",
     "lanes.md:1318 aggregate wrappers are checked"),
    ("session-error", _suite(PASS2 + '<error message="session broke"/>', "2"), "error",
     "lanes.md:1312 an <error> outside every <testcase>"),
    ("errored-test", _suite(_case("t1", '<error message="boom"/>') + _case("t2"), "2"), None,
     "lanes.md:1322 an ordinary errored test is still just a failed test"),
]


@pytest.mark.process
@pytest.mark.parametrize("case", HAND, ids=[row[0] for row in HAND])
def test_hand_written_reports_follow_the_docs_table(clean, tmp_path, case):
    _, text, refused, _source = case
    scenario = _with_report(clean, tmp_path, text)

    if refused:
        assert _refusal(scenario)
        return
    lane = scenario.json("coverage")["lanes"]["a"]
    assert frozenset(lane["failures"]) == junitparser_reading_text(tmp_path, text).failed


def junitparser_reading_text(tmp_path: Path, text: str) -> Reading:
    path = tmp_path / "report.xml"
    path.write_text(text, encoding="utf-8")
    return junitparser_reading(path)
