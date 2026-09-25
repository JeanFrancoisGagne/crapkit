"""A check that names a test file which is not there proves nothing, so its tier fails.

tools/accuracy/run.py runs every selected pytest check in one session and reads
each check's outcome from the session's JUnit file. Its docstring says a tier
exits 0 only when every check passed. A check whose file is missing ran no
test, so the expected outcome is `fail`, worked out here from the file's
absence and not from run.py.

Without xdist pytest refuses a missing target as a usage error (exit 4), and
run.py fails the session's checks. Under `-n N`, the way CI runs the push tier,
pytest exits 5 with no message: run.py reads that as a session with no tests,
reports every check `empty` and passes the tier. Rulings row SS1 holds that
defect until run.py refuses a missing target.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

import hang_guard
from accuracy.kit import rulings

REPO = Path(__file__).resolve().parents[3]
RUN = REPO / "tools" / "accuracy" / "run.py"
PASSING = "def test_ok():\n    assert True\n"


def _plant(tmp_path: Path) -> tuple[Path, Path]:
    """A checks module with one check over a real test file and one over a missing one."""
    here, gone = tmp_path / "planted" / "test_here.py", tmp_path / "planted" / "test_gone.py"
    here.parent.mkdir()
    here.write_text(PASSING, encoding="utf-8")
    rows = [{"name": "here", "seconds": 0, "pytest": [here.as_posix()]},
            {"name": "gone", "seconds": 0, "pytest": [gone.as_posix()]}]
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / "planted.py").write_text(f"SHARD = 'planted'\nCHECKS = {rows!r}\n", encoding="utf-8")
    return checks, gone


def _tier(tmp_path: Path, workers: int) -> tuple[int, dict]:
    checks, gone = _plant(tmp_path)
    receipt = tmp_path / "receipt.json"
    argv = [sys.executable, str(RUN), "--tier", "push", "--checks", str(checks), "--receipt",
            str(receipt), "-n", str(workers)]
    done = hang_guard.run(argv, cwd=REPO, text=True, encoding="utf-8", errors="replace")
    assert not gone.exists() and receipt.is_file(), done.stdout + done.stderr
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    return done.returncode, {check["name"]: check["outcome"] for check in saved["checks"]}


@pytest.mark.nightly
@pytest.mark.process
def test_a_missing_file_fails_its_check_without_xdist(tmp_path):
    code, outcomes = _tier(tmp_path, workers=0)

    assert (code, outcomes["gone"]) == (1, "fail")


@rulings.applies("SS1")
@pytest.mark.nightly
@pytest.mark.process
def test_ss1_a_check_naming_a_missing_file_fails_under_xdist(tmp_path):
    code, outcomes = _tier(tmp_path, workers=2)

    rulings.pin_ruling("SS1", crapkit=outcomes["gone"], oracle="fail")
    assert code == 1
