"""The README quickstarts, held to what 0.8.1 prints on the same steps.

A new user diffs each block against their terminal. Three blocks kept 0.8.0's
output after 0.8.1 changed it: init writes the `{python}` launcher token where
the page showed a bare `python`, a doctor on the coverage.py the page named
FAILs the lane, and `rescore` prints `-` and `(coverage not measured)` for a
function the stale run never saw where the page showed `0%`.
"""
import re
from pathlib import Path

from crapkit.cli.scoring import _rescore_cov
from crapkit.coverage_py import REGIONS_FLOOR
from crapkit.doctor import coverage_floor_gap
from crapkit.lane_command import expand_launchers, python_token

ROOT = Path(__file__).resolve().parents[2]


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


PY = "## Quickstart: Python"
TS = "## Quickstart: TypeScript"


def _quickstart(which: str) -> str:
    text = _readme()
    start = text.index(f"\n{which}\n")
    end = text.index("\n## ", start + 1)
    return text[start:end]


# --- step 1: the config init writes ------------------------------------------------

def test_the_python_config_block_runs_the_launcher_token_init_writes():
    """0.8.1 init writes `{python}` in the lane and the scoped-tests entry where
    no venv in the tree carries pytest; the page showed 0.8.0's bare `python`."""
    quickstart = _quickstart(PY)
    token = python_token()

    assert f'command = "{token} -m pytest --cov' in quickstart
    assert f'calc = "{token} -m pytest tests -q -p no:cacheprovider"' in quickstart
    assert 'command = "python -m pytest' not in quickstart
    assert 'calc = "python -m pytest' not in quickstart


def test_the_python_quickstart_says_what_the_token_is_and_links_its_rules():
    quickstart = _quickstart(PY)

    assert "configuration.md#the-launcher-token" in quickstart
    assert "`{python:.venv}`" in quickstart, "a repo .venv that holds pytest gets the DIR form"


# --- step 2: the doctor report -------------------------------------------------------

LANE = re.compile(r"^ok   lane 'py': (?P<word>\S+) -> (?P<exe>\S+) \(pytest [\d.]+, "
                  r"pytest-cov [\d.]+, coverage (?P<coverage>[\d.]+)\)$", re.M)


def _doctor_report() -> str:
    step = _quickstart(PY)
    step = step[step.index("### 2. Check the config against the repo"):]
    return step[:step.index("\n### ")]


def test_the_doctor_transcript_runs_a_coverage_that_doctor_passes():
    """On coverage 7.10.6 doctor FAILs the lane (exit 1) and `coverage` exits 5,
    as the quickstart's own intro says; the transcript still printed it with
    `doctor: no problems found`."""
    (lane,) = LANE.finditer(_doctor_report())
    version = lane["coverage"]

    assert tuple(map(int, version.split("."))) >= tuple(map(int, REGIONS_FLOOR.split("."))), version
    assert coverage_floor_gap("py", lane["exe"], version, "pip") == ()
    assert "doctor: no problems found" in _doctor_report()


def test_the_doctor_transcript_names_the_python_the_token_reads_as_on_its_os():
    """The report is a Linux one (`/home/you/...`), where `{python}` reads as
    `python3`; a `python` there would be a lane nobody's init wrote."""
    (lane,) = LANE.finditer(_doctor_report())

    assert lane["exe"].startswith("/home/you/")
    assert lane["word"] == expand_launchers(python_token(), windows=False)


# --- TypeScript step 5: rescore over a stale run -------------------------------------

def _rescore_rows() -> list[str]:
    step = _quickstart(TS)
    step = step[step.index("$ crapkit rescore src/grade.ts --gate"):]
    block = step[:step.index("```")]
    return [line for line in block.splitlines() if re.match(r"^\s+\d+ ", line)]


class _Row:
    """A row the overlay found no run row to join, as rescore keys it: by identity."""
    flag = "measured"
    cov = 0.0


NEW = ("band ( score )", "demote ( letter , row Row )", "penalty ( attempts , late )")


def test_the_rescore_rows_run_1_never_saw_print_what_rescore_prints_for_them():
    """classify split into penalty, band and demote after run 1; rescore has no
    measurement for them and says so, where 0.8.0 printed 0%."""
    unmeasured = _Row()
    cell, tail = _rescore_cov(unmeasured, {unmeasured})
    rows = {name: line for line in _rescore_rows() for name in NEW if f"  {name}" in line}

    assert sorted(rows) == sorted(NEW), _rescore_rows()
    for name, line in rows.items():
        assert line.endswith(f"{name}{tail}"), line
        assert re.match(rf"^\s+\d+ {re.escape(cell)} ", line), line


def _is_new(line: str) -> bool:
    return any(name in line for name in NEW)


def test_the_rescore_rows_run_1_measured_keep_their_percentage():
    measured = [line for line in _rescore_rows() if not _is_new(line)]

    assert measured
    for line in measured:
        assert re.match(r"^\s+\d+\s+\d+% ", line), line
        assert "(coverage not measured)" not in line, line
