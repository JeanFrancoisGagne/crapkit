"""The 16d7bfd guard for this packet's calcs: every function a suite_strength
calcs.tsv row names runs a body line while that row's independent test runs.

tests/accuracy/kit/test_kit_contract.py checks the same rule over the golden
corpus run. That run measures src/crapkit only and starts no `crapkit mutate`,
so it cannot reach the mutate pool or the accuracy and release tools these calcs
live in. Here each independent test runs alone under coverage.py, with its
subprocess children and pool workers measured, and kit.reach names each named
function whose body never ran. A test of a copy of the rule would leave the
production function unrun and fail here.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from accuracy.kit import calcs, reach
import hang_guard

REPO = Path(__file__).resolve().parents[3]
PACKET = Path(__file__).resolve().parent.name
ROWS = [row for row in calcs.load() if row.packet == PACKET]
RC = """\
[run]
source =
    {src}
    {tools}
    {kit}
branch = false
parallel = true
data_file = {data}
patch = subprocess
concurrency = multiprocessing,thread
"""
# `crapkit mutate` runs a killer suite per mutant, each child under coverage: minutes, not a hang.
BOUND = 4 * hang_guard.HANG_SECONDS


def _rc(base: Path) -> Path:
    data = base / "data"
    data.mkdir()
    rc = base / "coveragerc"
    rc.write_text(RC.format(src=(REPO / "src" / "crapkit").as_posix(), tools=(REPO / "tools").as_posix(),
                            kit=(REPO / "tests" / "accuracy" / "kit").as_posix(),
                            data=(data / ".coverage").as_posix()), encoding="utf-8")
    return rc


def measured(test: str, base: Path) -> dict[Path, set[int]]:
    """{source file: executed lines} over one coverage-measured run of the node id `test`."""
    import coverage
    rc = _rc(base)
    argv = [sys.executable, "-m", "coverage", "run", f"--rcfile={rc}", "-m", "pytest", test,
            "-q", "-p", "no:randomly"]
    env = {**os.environ, "CRAPKIT_ACCURACY_COLLECT_ALL": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    done = hang_guard.run(argv, cwd=REPO, env=env, timeout=BOUND, text=True, encoding="utf-8",
                          errors="replace")
    assert done.returncode == 0, f"{test} failed under coverage:\n{done.stdout[-2000:]}{done.stderr[-2000:]}"
    cov = coverage.Coverage(config_file=str(rc))
    cov.combine([str(base / "data")], keep=True)
    got = cov.get_data()
    return {Path(name).resolve(): set(got.lines(name) or ()) for name in got.measured_files()}


def test_this_packet_names_calcs():
    assert {row.calc for row in ROWS} >= {"Mutation results", "Retro replay verdict",
                                          "Mutation survivor verdict", "Release accuracy gate"}


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("row", ROWS, ids=[row.calc for row in ROWS])
def test_the_independent_test_runs_every_function_its_calc_names(row, tmp_path):
    assert reach.unreached(list(row.functions), measured(row.independent_test, tmp_path)) == []


def test_a_function_whose_body_never_ran_is_named(tmp_path):
    """The rule itself, on a module whose one function the measurement never entered."""
    module = tmp_path / "m.py"
    module.write_bytes(b"def f(x):\n    return x + 1\n")

    assert reach.unreached(["m.py:f"], {module.resolve(): {1}}, repo=tmp_path) == [
        "m.py:f: no line of its body ran"]
    assert reach.unreached(["m.py:f"], {module.resolve(): {1, 2}}, repo=tmp_path) == []
