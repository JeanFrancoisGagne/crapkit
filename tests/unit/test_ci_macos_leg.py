"""CI runs the letter-case rows on macOS, whose APFS opens a file in any letter case.

The case rows feed `SRC/app.ts` for a tracked `src/app.ts` through each reader.
They skip on a disk that keeps the two names apart. CI ran Ubuntu, whose ext4
does, so every case row skipped there, and Windows, whose NTFS folds case but
also reads a backslash as a separator and a drive letter as a root. macOS pairs
a case-folding disk with POSIX path rules, and no job had that pairing, so
nothing showed a case row passing on a disk where `/` is the only separator.
"""
import sys

import pytest

from path_spellings import case_insensitive
from test_ci_parallel_jobs import RUNNER, arguments, matrix_rows, rendered, step, workflow

MACOS = "macos-latest"


def _macos_rows(job, event) -> list[dict]:
    return [row for row in matrix_rows(job["strategy"]["matrix"], events=(event,)) if row["os"] == MACOS]


@pytest.mark.parametrize(("event", "python"), [
    ("push", "3.13"), ("pull_request", "3.13"),
    # 3.13 has os.waitid on macOS and 3.11 does not. 0.8.0 crashed every lane
    # on a macOS Python without it, and no CI leg ran one, so the nightly does.
    ("schedule", "3.11"),
])
def test_one_macos_job_runs_both_suites_on_the_python_of_its_event(event, python):
    job = workflow()["jobs"]["test"]
    command = step(job, "run", "python tools/testing/run.py")["run"]
    rows = _macos_rows(job, event)

    assert [row["python"] for row in rows] == [python], rows
    args = arguments(RUNNER["parse_arguments"], rendered(command, rows[0]), "tools/testing/run.py")
    assert tuple(args.suite) == RUNNER["SUITES"]


@pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS")
def test_the_macos_job_stands_on_a_disk_that_folds_case(tmp_path):
    """The case rows skip by this probe alone, so a runner image whose disk
    kept case apart would turn the macOS job into a second Ubuntu job with
    every case row skipped and nothing red."""
    assert case_insensitive(tmp_path), (
        f"{tmp_path} keeps letter case apart, so every case row skipped on macOS; "
        "the macos-latest job exists to run them on APFS")
