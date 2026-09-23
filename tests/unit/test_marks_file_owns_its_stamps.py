"""The marks file owns its two stamps on every publish.

The metric stamp says which rules produced the numbers; the key stamp says how
twin ordinals are counted. Every writer used to pick its own stamp, and most
took the running metric by omission, so a write that added no numbers, or added
numbers under one metric to marks another metric recorded, relabeled the whole
file and verify's stamp refusal went quiet. The rules now live in RatchetFile:

- a write that adds no measured number keeps both recorded stamps;
- a write that adds measured numbers is refused by marks another metric recorded;
- seed is the one write that changes the metric stamp, to the run it read.
"""
import sys

import pytest

from crapkit.errors import ConfigError
from crapkit.override import record_override
from crapkit.ratchet import (RatchetEntry, dump_ratchet, load_ratchet, read_key_version,
                             read_stamp)
from crapkit.ratchetfile import RatchetFile
from crapkit.store import SnapshotStore
from crapkit.verify import GateViolation

OLD = "crapkit-analysis=7 lizard=1.17.10"
NEW = "crapkit-analysis=10 lizard=1.24.0"
MARK = RatchetEntry("src/a.py", "f( )", 50.0)
VIOLATION = GateViolation("src/b.py", "g( )", 3, 9, 0.0, 90.0, "decompose")


def marks_file(tmp_path, stamp: str = OLD, keys: int = 1):
    path = tmp_path / "marks.tsv"
    path.write_text(dump_ratchet([MARK], stamp=stamp, key_version=keys), encoding="utf-8")
    return path


def stamps(text: str) -> tuple[str, int]:
    return read_stamp(text), read_key_version(text)


# --- a write that adds no measured number --------------------------------------

def test_a_kept_write_carries_both_recorded_stamps(tmp_path):
    saved = RatchetFile.read(marks_file(tmp_path))

    text = saved.kept([MARK._replace(path="lib/a.py")])

    assert stamps(text) == (OLD, 1)
    assert load_ratchet(text) == [MARK._replace(path="lib/a.py")]


def test_a_kept_write_on_a_file_written_before_stamping_stays_unstamped(tmp_path):
    saved = RatchetFile.read(marks_file(tmp_path, stamp="", keys=0))

    assert stamps(saved.kept([MARK], new_file_metric=NEW)) == ("", 0)


def test_a_kept_write_takes_a_proved_key_format_and_keeps_the_metric(tmp_path):
    saved = RatchetFile.read(marks_file(tmp_path, keys=0))

    assert stamps(saved.kept([MARK], keys=1)) == (OLD, 1)


def test_a_file_that_does_not_exist_yet_takes_the_metric_of_its_first_numbers(tmp_path):
    saved = RatchetFile.read(tmp_path / "absent.tsv")

    assert read_stamp(saved.kept([MARK], new_file_metric=NEW)) == NEW


# --- a write that adds measured numbers ----------------------------------------

def test_a_measured_write_refuses_marks_another_metric_recorded(tmp_path):
    saved = RatchetFile.read(marks_file(tmp_path))

    with pytest.raises(ConfigError, match=r"recorded under \[crapkit-analysis=7 lizard=1.17.10\] "
                                          r"but this run measures \[crapkit-analysis=10"):
        saved.measured([MARK], NEW)


def test_a_measured_write_under_the_recorded_metric_keeps_it(tmp_path):
    saved = RatchetFile.read(marks_file(tmp_path, stamp=NEW))

    assert stamps(saved.measured([MARK], NEW)) == (NEW, 1)


def test_a_measured_write_stamps_a_file_written_before_stamping(tmp_path):
    """The tighten's `restamped`: verify compared every mark before it wrote."""
    saved = RatchetFile.read(marks_file(tmp_path, stamp="", keys=0))

    assert stamps(saved.measured([MARK], NEW)) == (NEW, 0)


def test_the_stamp_conflict_is_the_one_verify_refuses_with(tmp_path):
    saved = RatchetFile.read(marks_file(tmp_path))

    assert saved.stamp_conflict(OLD) is None
    assert "re-baseline with" in saved.stamp_conflict(NEW)
    assert RatchetFile.read(tmp_path / "absent.tsv").stamp_conflict(NEW) is None


# --- seed ----------------------------------------------------------------------

def test_a_reseed_stamps_the_metric_it_was_handed_and_keeps_the_key_format(tmp_path):
    saved = RatchetFile.read(marks_file(tmp_path, stamp=NEW))

    assert stamps(saved.reseeded([MARK], OLD)) == (OLD, 1)


# --- the override grant ----------------------------------------------------------

def alert(tmp_path):
    log = tmp_path / "alert.log"
    return (f'"{sys.executable}" -c "import sys,pathlib; '
            f'pathlib.Path(r\'{log}\').open(\'a\').write(sys.stdin.read())"'), log


def grant(tmp_path, store, **kwargs):
    command, log = alert(tmp_path)
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="marks.tsv",
                    alert_command=command, violations=[VIOLATION], reason="hotfix", **kwargs)
    return run_id, log


def test_a_measured_grant_on_marks_another_metric_recorded_grants_nothing(tmp_path):
    """Refused before the alert: three records or nothing."""
    path = marks_file(tmp_path)
    before = path.read_bytes()
    store = SnapshotStore(tmp_path / "db.sqlite")

    with pytest.raises(ConfigError, match="recorded under"):
        grant(tmp_path, store, metric=NEW)

    assert path.read_bytes() == before
    assert not (tmp_path / "alert.log").exists()
    assert all(not store.read_overrides(run["id"]) for run in store.list_runs())


def test_a_measured_grant_under_the_recorded_metric_writes_the_debt(tmp_path):
    path = marks_file(tmp_path, stamp=NEW)
    store = SnapshotStore(tmp_path / "db.sqlite")

    grant(tmp_path, store, metric=NEW)

    text = path.read_text(encoding="utf-8")
    assert stamps(text) == (NEW, 1)
    assert RatchetEntry("src/b.py", "g( )", 90.0) in load_ratchet(text)


def test_a_grant_that_names_no_metric_keeps_the_recorded_stamps(tmp_path):
    path = marks_file(tmp_path)
    store = SnapshotStore(tmp_path / "db.sqlite")

    grant(tmp_path, store)

    assert stamps(path.read_text(encoding="utf-8")) == (OLD, 1)
