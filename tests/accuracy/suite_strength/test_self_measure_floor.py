"""The self-measurement floor: crapkit's own coverage lane measures its CLI entry points.

tests/e2e drives every `cmd_*` in src/crapkit/cli/ through a child process, so
a coverage run that does not follow children reads them at 0% and every
function under them looks untested. That happened once without a sound (R97):
pytest-cov 7.0.0 dropped its subprocess hook, and on
tests/e2e/test_init_doctor_e2e.py src/crapkit/cli/admin.py read 0 of 498
statements until pyproject.toml set [tool.coverage.run] patch = ["subprocess"]
(8ec9449's commit message has both measurements).

The floor: every top-level `cmd_*` function in src/crapkit/cli/ has at least one
executed body line in the lane's coverage.py JSON report. The report is read
with json.load and the functions with ast: no crapkit code decides what counts.

- test_cli_entry_points_measured reads the lane's own report
  (CRAPKIT_SELF_MEASURE, default .crapkit/cov/py.json). It is a release-tier
  test, and CI's verdict job runs it by node id after the measured join.
- The two recorded reports under recorded/ are coverage.py JSON from
  tests/e2e/test_init_doctor_e2e.py at 8ec9449's parent and at 8ec9449, run
  under pytest-cov 7.1.0 (retro/probes/R97.py wrote both): the rule must pass
  the fix's report and name cmd_init and cmd_doctor in the parent's.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RECORDED = HERE / "recorded"
REPORT_ENV = "CRAPKIT_SELF_MEASURE"
DEFAULT_REPORT = Path(".crapkit") / "cov" / "py.json"
# The two entry points tests/e2e/test_init_doctor_e2e.py drives.
INIT_DOCTOR = ("admin.py:cmd_doctor", "admin.py:cmd_init")


def entry_points(cli: Path) -> dict[str, range]:
    """{'admin.py:cmd_init': its body lines} for every top-level cmd_* function."""
    found = {}
    for path in sorted(cli.glob("*.py")):
        for node in ast.parse(path.read_bytes()).body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("cmd_"):
                found[f"{path.name}:{node.name}"] = range(node.body[0].lineno, node.end_lineno + 1)
    return found


def _executed(report: dict, name: str) -> set[int]:
    """The executed lines of crapkit/cli/<name> in a coverage.py JSON report,
    whatever separator or prefix its file key carries."""
    wanted = f"crapkit/cli/{name}"
    for key, data in report["files"].items():
        if key.replace("\\", "/").endswith(wanted):
            return set(data["executed_lines"])
    return set()


def unmeasured(report: dict, entries: dict[str, range]) -> list[str]:
    """The entry points with no executed body line."""
    return [entry for entry, body in sorted(entries.items())
            if not _executed(report, entry.split(":")[0]).intersection(body)]


def load(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _report_path() -> Path:
    named = os.environ.get(REPORT_ENV)
    return Path(named) if named else REPO / DEFAULT_REPORT


@pytest.mark.release
def test_cli_entry_points_measured():
    path = _report_path()
    assert path.is_file(), (f"no coverage report at {path}: run the py lane "
                            f"(crapkit coverage) or set {REPORT_ENV}")
    entries = entry_points(REPO / "src" / "crapkit" / "cli")

    assert unmeasured(load(path), entries) == []


def _init_doctor(entries: dict) -> dict:
    return {name: entries[name] for name in INIT_DOCTOR}


def test_the_fix_s_report_measures_init_and_doctor():
    """R97, at 8ec9449: the patch key makes coverage follow the CLI children."""
    entries = _init_doctor(entry_points(RECORDED / "r97-cli-8ec9449"))

    assert unmeasured(load(RECORDED / "r97-fix-py.json"), entries) == []


def test_the_parent_s_report_reads_init_and_doctor_unmeasured():
    """R97, at 8ec9449's parent: pytest-cov 7.1.0 measured no child at all."""
    entries = _init_doctor(entry_points(RECORDED / "r97-cli-8ec9449"))

    assert unmeasured(load(RECORDED / "r97-before-py.json"), entries) == list(INIT_DOCTOR)
