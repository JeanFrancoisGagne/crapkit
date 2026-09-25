"""Each tools/ function this packet's calcs.tsv names runs under its calc's
independent test.

kit/reach.py measures the golden CLI run with `source = crapkit`, so it cannot
see a function under tools/: the Action's bash steps run
tools/action/comment.py, and the accuracy tools import wheel_diff.py; no
crapkit command calls either. This runs the independent test of each row that
names a tools/ function under coverage.py, measuring tools/action and
tools/accuracy with subprocess children included, and asserts that each named
tools/ function ran a body line. A test of a copy of a rule proves nothing
about the rule (16d7bfd).
"""
from pathlib import Path
import subprocess
import sys

import pytest

from accuracy.kit import calcs, reach

REPO = Path(__file__).resolve().parents[3]
SOURCES = (REPO / "tools" / "action", REPO / "tools" / "accuracy")
RC = "[run]\nsource =\n{sources}\nbranch = false\nparallel = true\ndata_file = {data}\n" \
     "patch = subprocess\n"


def tools_functions(row: calcs.Calc) -> list[str]:
    return [function for function in row.functions if function.startswith("tools/")]


def _rows() -> list[calcs.Calc]:
    return [row for row in calcs.load()
            if row.packet == "corpus_goldens" and tools_functions(row)]


def _rc(base: Path) -> Path:
    data = base / "coverage-data"
    data.mkdir(parents=True)
    rc = base / "coveragerc"
    sources = "\n".join(f"    {path.as_posix()}" for path in SOURCES)
    rc.write_text(RC.format(sources=sources, data=(data / ".coverage").as_posix()),
                  encoding="utf-8")
    return rc


def measured_lines(node_id: str, base: Path) -> dict[Path, set[int]]:
    """{source file: executed lines} over one coverage-measured run of `node_id`."""
    import coverage
    rc = _rc(base)
    argv = [sys.executable, "-m", "coverage", "run", f"--rcfile={rc}", "-m", "pytest", node_id,
            "-p", "no:cacheprovider", "-p", "no:randomly", "-q"]
    done = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    cov = coverage.Coverage(config_file=str(rc))
    cov.combine([str(base / "coverage-data")], keep=True)
    got = cov.get_data()
    return {Path(name).resolve(): set(got.lines(name) or ()) for name in got.measured_files()}


def test_every_tools_row_is_found():
    assert sorted(row.calc for row in _rows()) == ["PR comment", "Wheel diff moved-row map"]


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("row", _rows(), ids=lambda row: row.calc)
def test_each_tools_function_runs_under_the_independent_test(row, tmp_path):
    measured = measured_lines(row.independent_test, tmp_path)

    assert reach.unreached(tools_functions(row), measured, REPO) == []
