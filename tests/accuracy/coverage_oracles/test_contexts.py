"""Per-line test contexts: which tests ran each line, against coverage.py's data file and the ground truth.

The lane is pytest-cov with `--cov-context=test` and `[json] show_contexts`
(docs/lanes.md#test-attribution-for-explain---tests). pytest-cov names a line's
context `<node id>|<phase>` (https://pytest-cov.readthedocs.io/en/latest/contexts.html),
and coverage.py gives a line run outside any test the empty context.

1. Live: this interpreter's pytest-cov runs both probe tests over a copy of the
   Python probe. crapkit's reading of the JSON report equals coverage.py's own
   CoverageData.contexts_by_lineno over the data file the same run wrote: each
   line's node ids with the phase cut and the empty context dropped.
2. Ground truth: the call scenario runs only test_call, which makes every call
   drive.py makes, so each line in a function's stmts_run carries test_call and
   no line carries any other test; the idle scenario's test calls nothing.
3. Every id crapkit reads names a testcase of the same run's JUnit report.
4. `explain --tests --json` lists test_call for a function the driver ran and
   says no context data for one no test ran.
"""
import os
from pathlib import Path
import shutil
import sys
from xml.etree import ElementTree

import pytest

import hang_guard
from accuracy.coverage_oracles import ground_table, mini_repo, probe_repo, under_test
from accuracy.kit import tiers

RECORDED = probe_repo.RECORDED / "pytest-cov-7.1.0-contexts"
PROBE = "py/shapes.py"
TEST_CALL = "py/test_drive.py::test_call"
COVERAGE_PY = under_test.crapkit("coverage_py")


def _crapkit(report: Path) -> dict[int, list[str]]:
    return COVERAGE_PY.parse_coveragepy_contexts_file(report, path_prefix="", source_path=PROBE)


def node_ids(contexts: list[str]) -> list[str]:
    """The tests behind one line's contexts: the node id before pytest-cov's
    `|phase`, the empty (outside any test) context left out."""
    return sorted({context.split("|")[0] for context in contexts if context})


def _nonempty(by_line: dict) -> dict[int, list[str]]:
    ids = {int(line): node_ids(contexts) for line, contexts in by_line.items()}
    return {line: tests for line, tests in ids.items() if tests}


# --- 1. live: the data file against the JSON report ----------------------------------------------

def _pytest_cov(work: Path) -> None:
    tiers.require_process("pytest")
    argv = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:randomly", "-q",
            "py/test_drive.py", "--cov", "--cov-config=py/coveragerc-contexts",
            "--cov-branch", "--cov-context=test", "--cov-report=json:report.json",
            "--junitxml=junit.xml", "-o", "junit_family=xunit2"]
    env = {**os.environ, "COVERAGE_FILE": str(work / ".coverage")}
    done = hang_guard.run(argv, cwd=work, env=env, text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """The work directory holding .coverage, report.json and junit.xml."""
    work = tmp_path_factory.mktemp("contexts")
    shutil.copytree(probe_repo.PROBES / "py", work / "py")
    _pytest_cov(work)
    return work


def _data_file_contexts(work: Path) -> dict[int, list[str]]:
    from coverage import CoverageData
    data = CoverageData(basename=str(work / ".coverage"))
    data.read()
    measured = next(name for name in data.measured_files() if name.replace("\\", "/").endswith(PROBE))
    return _nonempty(data.contexts_by_lineno(measured))


@pytest.mark.process
def test_contexts_match_the_coverage_data_file(live):
    found = _crapkit(live / "report.json")

    assert found == _data_file_contexts(live)
    assert {test for tests in found.values() for test in tests} == {TEST_CALL}
    assert {test for tests in found.values() for test in tests} <= junit_node_ids(live / "junit.xml")


# --- 2. the ground truth ------------------------------------------------------------------------

def _unmodelled_lines(scenario: str) -> set[int]:
    """Lines of the functions the table does not model or the pragma excludes."""
    rows = ground_table.rows_for("pytest-cov-7.1.0", scenario, probe_repo.PYTHON)
    return {line for row in rows if not row.modelled or row.unmeasured
            for line in range(row.start, row.end + 1)}


def _measured(scenario: str) -> list:
    rows = ground_table.rows_for("pytest-cov-7.1.0", scenario, probe_repo.PYTHON)
    return [row for row in rows if row.modelled and not row.unmeasured]


def hand_contexts(scenario: str) -> dict[int, list[str]]:
    """{line: [test_call]} for every function line the call driver ran."""
    lines = {int(line) for row in _measured(scenario) for line in row.stmts_run}
    return {line: [TEST_CALL] for line in sorted(lines)} if scenario == "call" else {}


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_each_line_the_driver_ran_names_the_test_that_ran_it(scenario):
    found = _crapkit(RECORDED / f"{scenario}.json")
    skip = _unmodelled_lines(scenario)

    assert {line: ids for line, ids in found.items() if line not in skip} == hand_contexts(scenario)


# --- 3. every id is a test the run reported -----------------------------------------------------

def junit_node_ids(path: Path) -> set[str]:
    """pytest's junit classname is the test module's dotted path, then any class."""
    root = ElementTree.parse(path).getroot()
    return {f"{case.get('classname').replace('.', '/')}.py::{case.get('name')}"
            for case in root.iter("testcase")}


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_every_context_id_is_a_testcase_of_the_run(scenario):
    ids = {test for tests in _crapkit(RECORDED / f"{scenario}.json").values() for test in tests}

    assert ids <= junit_node_ids(RECORDED / f"{scenario}-junit.xml")
    assert ids == ({TEST_CALL} if scenario == "call" else set())


# --- 4. explain --tests ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def explained(tmp_path_factory):
    """One repo holding the probe once per scenario, each under its own lane,
    scored once; explain answers from it."""
    scopes = [mini_repo.scope(name, [name], ["python"]) for name in probe_repo.SCENARIOS]
    lanes = [mini_repo.lane(name, "coveragepy", [name], path_prefix=name)
             for name in probe_repo.SCENARIOS]
    tree = {"crapkit.toml": mini_repo.config(scopes, lanes)}
    for name in probe_repo.SCENARIOS:
        tree[f"{name}/{PROBE}"] = (probe_repo.PROBES / PROBE).read_bytes()
        tree[f"recorded/{name}.json"] = (RECORDED / f"{name}.json").read_bytes()
    driver = mini_repo.build(tmp_path_factory.mktemp("explain") / "repo", tree)
    assert driver.run("coverage").code == 0
    return driver


@pytest.mark.process
@pytest.mark.parametrize("scenario, tests", [("call", [TEST_CALL]), ("idle", None)])
def test_explain_lists_the_tests_that_ran_the_function(explained, scenario, tests):
    payload = explained.json("explain", f"{scenario}/{PROBE}", "if_else", "--tests")["functions"][0]

    assert payload["tests"] == tests
    assert ("tests_note" in payload) == (tests is None)
