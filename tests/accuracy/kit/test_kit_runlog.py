"""Notes a test hands run.py: written only when run.py asked, summed on read."""
import pytest

from accuracy.kit import runlog


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

    assert runlog.summarize(runlog.read(log)) == {
        "infra": ["radon is not installed"],
        "skipped_files": {"ast": 5},
        "events": {"R28": 5, "R27": 2},
    }


def test_a_missing_log_reads_empty(tmp_path):
    assert runlog.read(tmp_path / "absent.jsonl") == []


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="unknown run-log kind 'weather'"):
        runlog.note("weather")
