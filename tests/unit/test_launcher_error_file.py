"""The POSIX launcher reports a failed exec through a private error file.

The caller raises that OSError after cleanup, and anything else the launcher
wrote there as a ToolError. Popen is faked, so the launcher start runs on every
platform.
"""
import io
import json
import os
import subprocess
import sys

import pytest

from crapkit import procs
from crapkit.errors import ToolError

MISSING = [2, "No such file or directory", "missing-runner", None, None]


class _GatedLauncher:
    """A launcher that passed its gate, wrote its error file and exited."""

    def __init__(self, code):
        self.pid = 4343
        self.args = ["launcher"]
        self.stdin = io.BytesIO()
        self.code = code

    def wait(self, timeout=None):
        return self.code


class _Owner:
    def __init__(self):
        self.stopped = []

    def prepare(self, popen_kwargs):
        return None, popen_kwargs

    def register_then(self, pid, release, registration=None):
        release()

    def stop(self, pid):
        self.stopped.append(pid)

    def check_cancelled(self):
        pass


def _launcher_writes(monkeypatch, payload, code=1):
    """Fake the launcher: it writes `payload` where the caller told it to."""
    calls = []
    launcher = _GatedLauncher(code)

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        _write_error(argv, kwargs, payload)
        return launcher

    monkeypatch.setattr(procs, "_START", procs._launched)
    monkeypatch.setattr(procs.subprocess, "Popen", popen)
    monkeypatch.setattr(procs, "_wait_command", lambda process, timeout: process.wait(timeout))
    return launcher, calls


def _write_error(argv, kwargs, payload):
    _, descriptor, merge = json.loads(argv[-1])
    if merge:
        kwargs["stderr"].write(payload)  # the launcher's own stderr is the error file
    else:
        os.write(descriptor, payload)


def test_a_failed_exec_is_the_callers_os_error_after_cleanup(monkeypatch):
    launcher, calls = _launcher_writes(monkeypatch, json.dumps(MISSING).encode())
    owner = _Owner()
    with pytest.raises(FileNotFoundError) as caught:
        procs.run_bounded("missing-runner", 10, owner=owner)
    assert (caught.value.errno, caught.value.filename) == (2, "missing-runner")
    assert owner.stopped == [launcher.pid]
    assert launcher.stdin.closed
    (argv, kwargs), = calls
    assert argv[:5] == [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-c",
                        procs._OWNED_LAUNCH]
    assert json.loads(argv[5]) == ["missing-runner", None, True]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == subprocess.PIPE
    assert "pass_fds" not in kwargs


def test_separate_streams_pass_the_error_file_by_descriptor(monkeypatch):
    _, calls = _launcher_writes(monkeypatch, json.dumps(MISSING).encode())
    with pytest.raises(FileNotFoundError):
        procs.run_owned(["missing-runner", "--flag"], owner=_Owner(), capture_output=True)
    (argv, kwargs), = calls
    command, descriptor, merge = json.loads(argv[5])
    assert (command, merge) == (["missing-runner", "--flag"], False)
    assert kwargs["pass_fds"] == (descriptor,)
    assert kwargs["stderr"] is not None, "the command keeps its own stderr capture"


def test_anything_else_in_the_error_file_is_a_launcher_failure(monkeypatch):
    _launcher_writes(monkeypatch, b"Traceback: launcher startup failed")
    with pytest.raises(ToolError, match="command launcher failed: Traceback"):
        procs.run_owned(["runner"], owner=_Owner())


def test_an_empty_error_file_returns_the_commands_code(monkeypatch):
    _launcher_writes(monkeypatch, b"", code=5)
    assert procs.run_owned(["runner"], owner=_Owner()).returncode == 5
