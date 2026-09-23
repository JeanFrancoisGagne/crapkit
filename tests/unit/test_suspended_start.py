"""Windows starts an owned command suspended, with no Python launcher in between.

A suspended process runs no code, so it creates no child before its owner puts
it in a Job; the owner resumes it only after that. A start that failed in DLL
initialisation now shows as the command's own exit code 0xC0000142, and that
code is the lane layer's retry trigger.
"""
import ctypes
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from crapkit import procs
from crapkit.errors import ToolError

WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows process start")
BASE = getattr(sys, "_base_executable", sys.executable)
DLL_INIT_FAILED = 3221225794
EXIT_DLL_INIT_FAILED = "import os; os._exit(-1073741502)"  # 0xC0000142 as a C int


def test_the_owned_command_is_the_callers_own_child():
    # POSIX: the launcher execs the command. Windows: nothing sits in between.
    result = procs.run_owned([BASE, "-c", "import os; print(os.getppid())"],
                             capture_output=True)
    assert int(result.stdout) == os.getpid()


class _Threads(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("usage", ctypes.c_ulong), ("thread", ctypes.c_ulong),
                ("process", ctypes.c_ulong), ("base_priority", ctypes.c_long),
                ("delta_priority", ctypes.c_long), ("flags", ctypes.c_ulong)]


def _suspend_counts(pid):
    """Each thread of pid's suspend count, read by suspending it once more."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel.OpenThread.restype = ctypes.c_void_p
    snapshot = ctypes.c_void_p(kernel.CreateToolhelp32Snapshot(0x4, 0))  # TH32CS_SNAPTHREAD
    entry = _Threads(size=ctypes.sizeof(_Threads))
    counts = []
    try:
        more = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.process == pid:
                thread = ctypes.c_void_p(kernel.OpenThread(0x2, False, entry.thread))
                counts.append(kernel.SuspendThread(thread))
                kernel.ResumeThread(thread)
                kernel.CloseHandle(thread)
            more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return counts


@WINDOWS
def test_windows_registers_the_command_before_it_runs_any_code():
    seen = []
    with procs.own_processes(()) as owner:
        register = owner.register_then

        def observed(pid, release, registration=None):
            seen.append(_suspend_counts(pid))
            register(pid, release, registration)

        owner.register_then = observed
        result = procs.run_owned([BASE, "-c", "print('ran')"], owner=owner, capture_output=True)
    assert result.stdout == "ran\n"
    assert seen == [[1]], "registration must meet the command's one thread still suspended"


@WINDOWS
@pytest.mark.parametrize("shell", [False, True], ids=["argv", "shell"])
def test_a_dll_init_failure_exit_is_the_retry_trigger(shell):
    command = ([BASE, "-c", EXIT_DLL_INIT_FAILED] if not shell
               else f'"{BASE}" -c "{EXIT_DLL_INIT_FAILED}"')
    with pytest.raises(ToolError) as caught:
        procs.run_owned(command, capture_output=True)
    assert isinstance(caught.value, OSError), "doctor and init probes read an OSError"
    message = str(caught.value)
    assert "3221225794 (0xC0000142, STATUS_DLL_INIT_FAILED)" in message
    assert "never ran" in message


@WINDOWS
def test_run_bounded_raises_the_same_retry_trigger():
    with pytest.raises(ToolError, match="STATUS_DLL_INIT_FAILED"):
        procs.run_bounded(f'"{BASE}" -c "{EXIT_DLL_INIT_FAILED}"', 30)


@WINDOWS
def test_a_resume_the_kernel_refuses_is_an_os_error():
    with pytest.raises(OSError, match="NtResumeProcess"):
        procs._resume(SimpleNamespace(_handle=0))


class _RefusingOwner:
    """An owner that cannot take the command: Job assignment was refused."""

    def prepare(self, popen_kwargs):
        return None, popen_kwargs

    def register_then(self, pid, release, registration=None):
        raise PermissionError(13, "Access is denied")

    def stop(self, pid):
        raise AssertionError("a command that was never registered is never stopped")

    def check_cancelled(self):
        pass


def test_a_refused_registration_stops_the_command_before_it_runs(tmp_path, monkeypatch):
    # POSIX waits this long for a launcher behind a failed start line to exit.
    monkeypatch.setattr(procs, "_LAUNCHER_SETTLE", .1)
    marker = tmp_path / "ran"
    with pytest.raises(ToolError) as caught:
        procs.run_owned([BASE, "-c", f"open({str(marker)!r}, 'w').close()"],
                        owner=_RefusingOwner())
    assert isinstance(caught.value, OSError)
    assert "never ran" in str(caught.value)
    assert not marker.exists()


class _FakeCommand:
    """What Popen hands back for a suspended command, on any platform."""

    def __init__(self, code, events):
        self.pid = 4242
        self.args = "command"
        self.stdin = None
        self.code = code
        self.events = events

    def wait(self, timeout=None):
        self.events.append("wait")
        return self.code

    def poll(self):
        return self.code


class _RecordingOwner:
    def __init__(self, events):
        self.events = events

    def prepare(self, popen_kwargs):
        self.events.append("prepare")
        return "registration", popen_kwargs

    def register_then(self, pid, release, registration=None):
        self.events.append(("register", pid, registration))
        release()

    def stop(self, pid):
        self.events.append(("stop", pid))

    def check_cancelled(self):
        pass


def _suspended_start(monkeypatch, code):
    """Drive the Windows start on any platform, with Popen and resume faked."""
    events, calls = [], []
    command = _FakeCommand(code, events)

    def popen(*args, **kwargs):
        calls.append((args, kwargs))
        events.append("popen")
        return command

    monkeypatch.setattr(procs, "_START", procs._suspended)
    monkeypatch.setattr(procs.subprocess, "Popen", popen)
    monkeypatch.setattr(procs, "_resume", lambda process: events.append("resume"))
    monkeypatch.setattr(procs, "_wait_command", lambda process, timeout: process.wait(timeout))
    return events, calls


def test_the_suspended_start_registers_before_it_resumes(monkeypatch):
    events, calls = _suspended_start(monkeypatch, 0)
    owner = _RecordingOwner(events)
    assert procs.run_bounded("runner --flag", 10, owner=owner, cwd="here") == 0
    assert events == ["prepare", "popen", ("register", 4242, "registration"), "resume",
                      "wait", ("stop", 4242), "wait"]
    (args, kwargs), = calls
    assert args == ("runner --flag",)
    assert kwargs["shell"] is True
    assert kwargs["creationflags"] == 0x204  # CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["cwd"] == "here"


def test_an_argv_command_starts_without_a_shell(monkeypatch):
    events, calls = _suspended_start(monkeypatch, 0)
    procs.run_owned(["runner", "--flag"], owner=_RecordingOwner(events))
    (args, kwargs), = calls
    assert args == (["runner", "--flag"],)
    assert kwargs["shell"] is False


def test_the_dll_init_failure_code_is_refused_after_cleanup(monkeypatch):
    events, _ = _suspended_start(monkeypatch, DLL_INIT_FAILED)
    with pytest.raises(ToolError, match="0xC0000142"):
        procs.run_bounded("runner", 10, owner=_RecordingOwner(events))
    assert ("stop", 4242) in events
