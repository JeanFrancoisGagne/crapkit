"""A launcher that dies before its start gate is that lane's failure, not a crash.

Seen 2026-09-14 in a weekly coverage run on Windows: nine launchers exited
with 3221225794 (0xC0000142, STATUS_DLL_INIT_FAILED) before reading stdin.
The "go" line then hit a dead pipe, the OSError climbed out of the coverage
command as a traceback, and one dead launcher killed a run whose other lanes
were fine. The lane layer already retries a ToolError from run_bounded; an
OSError it does not know.
"""
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

from crapkit import procs
from crapkit.errors import ToolError

DLL_INIT_FAILED = 3221225794


class _DeadPipe:
    """The launcher's stdin after the launcher has gone: the write or the flush
    reaches nobody. Windows reports EINVAL there; POSIX reports EPIPE."""

    def __init__(self, fails_on: str, error: OSError):
        self.fails_on = fails_on
        self.error = error
        self.closed = False

    def write(self, data):
        if self.fails_on == "write":
            raise self.error
        return len(data)

    def flush(self):
        if self.fails_on == "flush":
            raise self.error

    def close(self):
        self.closed = True


class _DeadLauncher:
    """What subprocess.Popen hands back when the launcher exits at spawn."""

    def __init__(self, code, stdin):
        self.pid = 4242
        self.args = ["launcher"]
        self.returncode = code
        self.stdin = stdin
        self.waits = []
        self.killed = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return self.returncode


class _RecordingOwner(procs._ProcessOwner):
    """Accepts add/remove requests without a Job or a guardian process."""

    def __init__(self):
        super().__init__(None)
        self.requests = []

    def _request(self, operation, pid):
        self.requests.append((operation, pid))


def _dead_at_spawn(monkeypatch, code, fails_on="flush", error=None):
    error = OSError(22, "Invalid argument") if error is None else error
    launcher = _DeadLauncher(code, _DeadPipe(fails_on, error))
    monkeypatch.setattr(procs.subprocess, "Popen", lambda *args, **kwargs: launcher)
    # The tree kill would otherwise taskkill/killpg a pid that is not ours.
    monkeypatch.setattr(procs, "_kill_pid", launcher.killed.append)
    return launcher


@pytest.mark.parametrize("fails_on, error", [
    ("flush", OSError(22, "Invalid argument")),
    ("write", BrokenPipeError(32, "Broken pipe")),
], ids=["windows-einval-on-flush", "posix-epipe-on-write"])
def test_run_bounded_names_the_dll_init_failure_as_a_tool_error(monkeypatch, fails_on, error):
    _dead_at_spawn(monkeypatch, DLL_INIT_FAILED, fails_on, error)
    with pytest.raises(ToolError) as caught:
        procs.run_bounded("unused", 10, owner=_RecordingOwner())
    message = str(caught.value)
    assert "3221225794" in message
    assert "0xC0000142" in message
    assert "STATUS_DLL_INIT_FAILED" in message
    assert "before its start gate" in message
    assert "never ran" in message


def test_run_owned_names_the_dll_init_failure_as_a_tool_error(monkeypatch):
    _dead_at_spawn(monkeypatch, DLL_INIT_FAILED)
    with pytest.raises(ToolError) as caught:
        procs.run_owned(["unused"], 10, owner=_RecordingOwner(), capture_output=True)
    message = str(caught.value)
    assert "3221225794" in message
    assert "0xC0000142" in message
    assert "STATUS_DLL_INIT_FAILED" in message
    assert "never ran" in message


def test_another_exit_code_is_named_in_decimal_without_the_windows_status(monkeypatch):
    _dead_at_spawn(monkeypatch, 7)
    with pytest.raises(ToolError) as caught:
        procs.run_bounded("unused", 10, owner=_RecordingOwner())
    message = str(caught.value)
    assert "exited with code 7 " in message
    assert "0x" not in message
    assert "STATUS_DLL_INIT_FAILED" not in message
    assert "before its start gate" in message


def test_a_launcher_still_running_behind_a_dead_pipe_is_reported_as_running(monkeypatch):
    launcher = _dead_at_spawn(monkeypatch, None)

    def never_settles(timeout=None):
        launcher.waits.append(timeout)
        if timeout is not None:
            raise procs.subprocess.TimeoutExpired(launcher.args, timeout)
        return 0

    monkeypatch.setattr(launcher, "wait", never_settles)
    with pytest.raises(ToolError) as caught:
        procs.run_bounded("unused", 10, owner=_RecordingOwner())
    message = str(caught.value)
    assert "still running" in message
    assert "exited with code" not in message
    assert "never ran" in message


def test_the_spawn_cleanup_still_kills_the_tree_and_closes_the_pipe(monkeypatch):
    launcher = _dead_at_spawn(monkeypatch, DLL_INIT_FAILED)
    owner = _RecordingOwner()
    with pytest.raises(ToolError):
        procs.run_bounded("unused", 10, owner=owner)
    assert owner.requests == [("add", launcher.pid)]
    assert launcher.killed == [launcher.pid]
    assert launcher.stdin.closed is True
    # A bounded settle wait first, then the tree kill's reap.
    assert launcher.waits[0] is not None
    assert launcher.waits[-1] is None


class _RefusingOwner(_RecordingOwner):
    """A Windows Job refuses an already-exited launcher with access denied,
    one step before the start line is written."""

    def _request(self, operation, pid):
        super()._request(operation, pid)
        if operation == "add":
            raise PermissionError(13, "Access is denied")


def test_a_launcher_gone_before_registration_is_the_same_tool_error(monkeypatch):
    launcher = _dead_at_spawn(monkeypatch, DLL_INIT_FAILED)
    owner = _RefusingOwner()
    with pytest.raises(ToolError) as caught:
        procs.run_bounded("unused", 10, owner=owner)
    message = str(caught.value)
    assert "3221225794" in message
    assert "STATUS_DLL_INIT_FAILED" in message
    assert "never ran" in message
    assert owner.requests == [("add", launcher.pid)]
    assert launcher.killed == [launcher.pid]
    assert launcher.stdin.closed is True


# doctor and init put machine questions through run_bounded and read an OSError
# as "the question could not be put". A dead launcher was an OSError before this
# fix, so those probes answered with a finding; it must stay one for them while
# the lane layer reads the same failure as a ToolError.

def _probes_meet_a_dead_launcher(monkeypatch):
    _dead_at_spawn(monkeypatch, DLL_INIT_FAILED)
    # The probes pass no owner, so run_bounded builds one; hand it the recording one.
    monkeypatch.setattr(procs, "own_processes", lambda paths: nullcontext(_RecordingOwner()))


def test_the_start_probe_cannot_ask_a_dead_launcher(monkeypatch):
    from crapkit.cli import admin
    _probes_meet_a_dead_launcher(monkeypatch)
    admin._start_probe.cache_clear()
    try:
        assert admin._start_probe("dead-launcher-word") is None
    finally:
        admin._start_probe.cache_clear()


def test_the_runner_report_cannot_ask_a_dead_launcher(monkeypatch):
    from crapkit.cli import admin
    _probes_meet_a_dead_launcher(monkeypatch)
    admin._runner_report.cache_clear()
    try:
        assert admin._runner_report("dead-launcher-word") is None
    finally:
        admin._runner_report.cache_clear()


def test_the_pytest_import_probe_answers_no_for_a_dead_launcher(monkeypatch):
    from crapkit.cli import admin
    _probes_meet_a_dead_launcher(monkeypatch)
    assert admin._imports_pytest(Path(sys.executable)) is False


def test_the_pytest_cov_probe_stays_quiet_for_a_dead_launcher(monkeypatch):
    from crapkit.cli import admin
    _probes_meet_a_dead_launcher(monkeypatch)
    assert admin._pytest_cov_probe(f'"{sys.executable}" -m pytest') is True
