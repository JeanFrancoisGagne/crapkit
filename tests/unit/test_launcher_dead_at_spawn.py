"""A launcher that dies before its start gate is that lane's failure, not a crash.

Seen 2026-09-14 in a weekly coverage run on Windows: nine launchers exited
with 3221225794 (0xC0000142, STATUS_DLL_INIT_FAILED) before reading stdin.
The "go" line then hit a dead pipe, the OSError climbed out of the coverage
command as a traceback, and one dead launcher killed a run whose other lanes
were fine. The lane layer already retries a ToolError from run_bounded; an
OSError it does not know.

Windows now starts the command itself suspended, and that exit code is its
retry trigger (test_suspended_start.py). The start-gate launcher is POSIX's;
these tests fake Popen and drive it on every platform.
"""
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

from crapkit import procs
from crapkit.errors import ToolError
from crapkit.lane_command import LaunchSpec

KILLED = -9


@pytest.fixture(autouse=True)
def launcher_start(monkeypatch):
    monkeypatch.setattr(procs, "_START", procs._launched)


class _DeadPipe:
    """The launcher's stdin after the launcher has gone: the write or the flush
    reaches nobody and reports EPIPE."""

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


class _RecordingOwner:
    """The owner methods procs calls, with no Job or guardian process behind them."""

    def __init__(self, registration=None):
        self.registration = registration
        self.registered = []

    def prepare(self, popen_kwargs):
        return self.registration, popen_kwargs

    def register_then(self, pid, release, registration=None):
        self.registered.append((pid, registration))
        release()

    def stop(self, pid):
        pass

    def check_cancelled(self):
        pass


# The registration is opaque to procs: none, or a POSIX process family.
_REGISTRATIONS = pytest.mark.parametrize("family", [None, "crapkit-family-4242"],
                                         ids=["no-family", "posix-family"])


def _dead_at_spawn(monkeypatch, code, fails_on="flush", error=None):
    error = BrokenPipeError(32, "Broken pipe") if error is None else error
    launcher = _DeadLauncher(code, _DeadPipe(fails_on, error))
    monkeypatch.setattr(procs.subprocess, "Popen", lambda *args, **kwargs: launcher)
    # The tree kill would otherwise taskkill/killpg a pid that is not ours.
    monkeypatch.setattr(procs, "kill_process_tree", launcher.killed.append)
    return launcher


@pytest.mark.parametrize("fails_on", ["flush", "write"])
def test_run_bounded_names_the_dead_launcher_as_a_tool_error(monkeypatch, fails_on):
    _dead_at_spawn(monkeypatch, KILLED, fails_on)
    with pytest.raises(ToolError) as caught:
        procs.run_bounded("unused", 10, owner=_RecordingOwner())
    assert isinstance(caught.value, OSError)
    message = str(caught.value)
    assert "exited with code -9 before its start gate" in message
    assert "never ran" in message
    assert "0x" not in message


def test_run_owned_names_the_dead_launcher_as_a_tool_error(monkeypatch):
    _dead_at_spawn(monkeypatch, KILLED)
    with pytest.raises(ToolError) as caught:
        procs.run_owned(["unused"], 10, owner=_RecordingOwner(), capture_output=True)
    message = str(caught.value)
    assert "exited with code -9 before its start gate" in message
    assert "never ran" in message


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


@_REGISTRATIONS
def test_the_spawn_cleanup_still_kills_the_tree_and_closes_the_pipe(monkeypatch, family):
    launcher = _dead_at_spawn(monkeypatch, KILLED)
    owner = _RecordingOwner(family)
    with pytest.raises(ToolError):
        procs.run_bounded("unused", 10, owner=owner)
    assert owner.registered == [(launcher.pid, family)]
    assert launcher.killed == [launcher.pid]
    assert launcher.stdin.closed is True
    # A bounded settle wait first, then the tree kill's reap.
    assert launcher.waits[0] is not None
    assert launcher.waits[-1] is None


class _RefusingOwner(_RecordingOwner):
    """Registration fails at the OS level one step before the start line."""

    def register_then(self, pid, release, registration=None):
        self.registered.append((pid, registration))
        raise PermissionError(13, "Access is denied")


@_REGISTRATIONS
def test_a_launcher_gone_before_registration_is_the_same_tool_error(monkeypatch, family):
    launcher = _dead_at_spawn(monkeypatch, KILLED)
    owner = _RefusingOwner(family)
    with pytest.raises(ToolError) as caught:
        procs.run_bounded("unused", 10, owner=owner)
    message = str(caught.value)
    assert "exited with code -9 before its start gate" in message
    assert "never ran" in message
    assert owner.registered == [(launcher.pid, family)]
    assert launcher.killed == [launcher.pid]
    assert launcher.stdin.closed is True


# doctor and init put machine questions through run_bounded and read an OSError
# as "the question could not be put". A dead launcher was an OSError before this
# fix, so those probes answered with a finding; it must stay one for them while
# the lane layer reads the same failure as a ToolError.

def _probes_meet_a_dead_launcher(monkeypatch):
    _dead_at_spawn(monkeypatch, KILLED)
    # The probes pass no owner, so run_bounded builds one; hand it the recording one.
    monkeypatch.setattr(procs, "own_processes", lambda paths: nullcontext(_RecordingOwner()))


def test_the_start_probe_cannot_ask_a_dead_launcher(monkeypatch):
    from crapkit.cli import admin
    _probes_meet_a_dead_launcher(monkeypatch)
    admin._start_probe.cache_clear()
    try:
        assert admin._start_probe("dead-launcher-word", LaunchSpec(Path.cwd())) is None
    finally:
        admin._start_probe.cache_clear()


def test_the_runner_report_cannot_ask_a_dead_launcher(monkeypatch):
    from crapkit.cli import admin
    _probes_meet_a_dead_launcher(monkeypatch)
    admin._runner_report.cache_clear()
    try:
        assert admin._runner_report("dead-launcher-word", LaunchSpec(Path.cwd())) is None
    finally:
        admin._runner_report.cache_clear()


def test_the_pytest_import_probe_answers_no_for_a_dead_launcher(monkeypatch):
    from crapkit.cli import admin
    _probes_meet_a_dead_launcher(monkeypatch)
    assert admin._imports_pytest(Path(sys.executable)) is False


def test_the_pytest_cov_probe_stays_quiet_for_a_dead_launcher(monkeypatch):
    from crapkit.cli import admin
    _probes_meet_a_dead_launcher(monkeypatch)
    assert admin._pytest_cov_probe(LaunchSpec(Path.cwd()), f'"{sys.executable}" -m pytest') is True
