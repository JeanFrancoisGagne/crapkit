"""Notes a test hands run.py: written only when run.py asked, summed on read."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from accuracy.kit import runlog
from hang_guard import HANG_SECONDS

TESTS = Path(__file__).resolve().parents[2]
WRITER = ("import sys; sys.path.insert(0, sys.argv[1])\n"
          "from accuracy.kit import runlog\n"
          "for _ in range(2000): runlog.note('events', counts={'n': 1}, pad='x' * 200)\n")


def test_notes_from_processes_writing_at_once_all_come_back(tmp_path):
    """pytest-xdist workers note at the same time. On Windows, appends from four
    processes to one file kept 9,257 to 9,350 of 12,000 notes and once left a line
    json could not read, which ended run.py in the Windows push job (2026-10-01)."""
    log = tmp_path / "run.jsonl"
    env = {**os.environ, runlog.LOG_ENV: str(log)}
    writers = [subprocess.Popen([sys.executable, "-c", WRITER, str(TESTS)], env=env)
               for _ in range(4)]

    assert [writer.wait(timeout=HANG_SECONDS) for writer in writers] == [0, 0, 0, 0]
    assert runlog.summarize(runlog.read(log))["events"] == {"n": 8000}


def test_without_a_log_a_note_goes_nowhere(monkeypatch, tmp_path):
    monkeypatch.delenv(runlog.LOG_ENV, raising=False)

    runlog.note("infra", message="radon is not installed")

    assert list(tmp_path.iterdir()) == []


def test_notes_sum_per_oracle_and_per_event(monkeypatch, tmp_path):
    log = tmp_path / "check.jsonl"
    monkeypatch.setenv(runlog.LOG_ENV, str(log))

    runlog.note("skipped_files", oracle="ast", count=3)
    runlog.note("skipped_files", oracle="ast", count=2)
    runlog.note("events", counts={"R28": 4})
    runlog.note("events", counts={"R28": 1, "R27": 2})
    runlog.note("infra", message="radon is not installed")
    runlog.note("oracle", name="radon", version="6.0.1")
    runlog.note("oracle", name="radon", version="6.0.1")
    runlog.note("digest", name="small/scored.tsv", value="ab12")

    assert runlog.summarize(runlog.read(log)) == {
        "infra": ["radon is not installed"],
        "skipped_files": {"ast": 5},
        "events": {"R28": 5, "R27": 2},
        "oracles": {"radon": "6.0.1"},
        "exports": {"small/scored.tsv": "ab12"},
    }


def test_a_note_names_the_test_that_wrote_it(monkeypatch, tmp_path):
    log = tmp_path / "check.jsonl"
    monkeypatch.setenv(runlog.LOG_ENV, str(log))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/accuracy/x/test_a.py::test_b[1] (call)")

    runlog.note("infra", message="radon is not installed")

    assert runlog.read(log)[0]["test"] == "tests/accuracy/x/test_a.py::test_b[1]"
    assert runlog.infra_tests(runlog.read(log)) == {"tests/accuracy/x/test_a.py::test_b[1]"}


def test_a_missing_log_reads_empty(tmp_path):
    assert runlog.read(tmp_path / "absent.jsonl") == []


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="unknown run-log kind 'weather'"):
        runlog.note("weather")
