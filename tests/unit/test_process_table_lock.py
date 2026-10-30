"""A test that puts a process named in bytes that are not UTF-8 on the host,
and a test that runs a crapkit release older than 0.8.1, never overlap.

Before 0.8.1 the Linux measurement owner decoded every /proc/<pid>/stat on the
host in the locale, so one such name anywhere stopped the owner with
`measurement owner stopped before confirming ownership` (exit 5). The suite
keeps a host process named `caf\\xe9-daemon` alive in
test_child_env_e2e, and runs 0.4.15, 0.7.6 and 0.8.0 from the tag history in
two other files; under xdist the old release failed whenever the two ran at
once, a different row each run. process_table.hold is one lock over the host's
process table: exclusive while a test names such a process, shared while an
old release runs.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

import process_table

TESTS = Path(__file__).resolve().parents[1]
LINUX_ONLY = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="only Linux has a /proc an owner scans, so only Linux locks")


@pytest.fixture
def own_lock(tmp_path, monkeypatch):
    """Points process_table.LOCK at a file under tmp_path. Under xdist another worker
    holds the suite's lock while it runs an old release or names a host process, so a
    mode test that read the suite's lock would see that worker's hold, not hold()'s."""
    monkeypatch.setattr(process_table, "LOCK", tmp_path / "process-table.lock")


def _try(mode: int) -> bool:
    """Whether another open of the lock file takes `mode` at once."""
    import fcntl

    with open(process_table.LOCK, "a+b") as handle:
        try:
            fcntl.flock(handle, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(handle, fcntl.LOCK_UN)
        return True


@LINUX_ONLY
def test_a_test_naming_a_host_process_keeps_every_old_release_out(own_lock):
    import fcntl

    with process_table.hold(naming=True):
        assert not _try(fcntl.LOCK_SH)
    assert _try(fcntl.LOCK_EX)


@LINUX_ONLY
def test_old_releases_run_beside_each_other_but_not_beside_a_naming(own_lock):
    import fcntl

    with process_table.hold(naming=False):
        assert _try(fcntl.LOCK_SH)
        assert not _try(fcntl.LOCK_EX)


@LINUX_ONLY
def test_every_session_holds_the_one_lock_file_in_the_temp_dir(monkeypatch):
    """hold() locks process_table.LOCK as the suite sets it, in the system temp dir the
    unit and e2e sessions share. flock only records here, so the suite's lock stays free."""
    import fcntl

    taken = []
    monkeypatch.setattr(fcntl, "flock", lambda handle, mode: taken.append((Path(handle.name), mode)))

    with process_table.hold(naming=True):
        pass

    assert taken == [(process_table.LOCK, fcntl.LOCK_EX)]
    assert process_table.LOCK.parent == Path(tempfile.gettempdir())


def test_elsewhere_holding_the_table_takes_no_lock(monkeypatch):
    monkeypatch.setattr(process_table.sys, "platform", "win32")

    with process_table.hold(naming=True):
        pass


# The spellings that put a name on the host's process table (prctl
# PR_SET_NAME) or run a release out of the tag history (git archive <tag>).
_NAMING = "prctl(15"
_OLD_RELEASE = '"archive"'


def _touches_the_table(text: str) -> bool:
    return _NAMING in text or _OLD_RELEASE in text


# tests/deploy is a session of its own (CRAPKIT_DEPLOY=1, tools/deploy/run.py),
# which on Linux runs in a container with its own process table, and none of
# its tests names a host process. Its act kit exports an old release's Action
# with `git archive` all the same.
DEPLOY = "deploy/"


def _texts() -> dict[str, str]:
    return {path.relative_to(TESTS).as_posix(): path.read_text(encoding="utf-8")
            for path in TESTS.rglob("*.py") if path.name != Path(__file__).name}


def test_no_deploy_test_names_a_host_process():
    assert [name for name, text in _texts().items() if name.startswith(DEPLOY) and _NAMING in text] == []


def test_every_test_that_names_a_host_process_or_runs_an_old_release_holds_the_table():
    holders = {name: "process_table.hold(" in text for name, text in _texts().items()
               if not name.startswith(DEPLOY) and _touches_the_table(text)}

    assert set(holders) >= {"e2e/test_child_env_e2e.py",
                            "e2e/test_verify_reads_stores_older_crapkits_wrote_e2e.py",
                            "e2e/test_absent_state_fixed_by_other_classes_e2e.py"}, holders
    assert all(holders.values()), holders
