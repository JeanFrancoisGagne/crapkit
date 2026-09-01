"""Shell commands run under a deadline the caller can actually enforce.

`shell=True` makes the shell the child and the real program a grandchild.
subprocess.run's timeout kills the shell alone, so the program keeps running
with nothing waiting on it: `mutate` scored a mutant killed at 2 s and left the
suite it was supposed to kill running to the end, one per mutant, all at once
on the single-worker path. The probe leaked one interpreter per timeout.

So the shell starts in its own process group (its own session on POSIX) and the
deadline kills the group, not the shell: `taskkill /T` walks the child tree on
Windows, killpg reaches it on POSIX. The shell is reaped before this returns,
because the caller's next move is deleting the directory the tree ran in.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import IO

_OWN_GROUP = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
              if os.name == "nt" else {"start_new_session": True})

# How often the progress watch looks at the stream. Small enough that the kill
# lands close to the deadline, large enough that watching a two-hour suite costs
# nothing measurable.
_TICK = 0.5


class NoProgress(Exception):
    """The command was alive and silent for `seconds`, and its tree was killed.

    Only a caller that passed `no_progress` can see this. A total deadline still
    returns None, because the two say different things: one command ran too
    long, the other stopped doing anything at all.
    """

    def __init__(self, seconds: float) -> None:
        super().__init__(f"no output for {seconds:g}s")
        self.seconds = seconds


def _kill_group(proc: subprocess.Popen) -> None:
    """POSIX: start_new_session made the shell its own group leader, so its pid
    is the group id. A group that already died raises, and that is a win."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _kill_tree(proc: subprocess.Popen) -> None:
    """The shell and everything under it, then reap the shell."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    else:
        _kill_group(proc)
    proc.wait()


def _stream_size(stream: IO | None) -> int:
    """Bytes the command has written so far, or -1 when nothing can be measured.
    The child writes to the file behind this handle, so its size is the only
    progress signal available without a pipe to read."""
    try:
        return os.fstat(stream.fileno()).st_size
    except (AttributeError, OSError, ValueError):
        return -1


def _progress(stream: IO | None, size: int, since: float) -> tuple[int, float]:
    """The stream's size and the moment it last changed."""
    grown = _stream_size(stream)
    return (grown, time.monotonic()) if grown != size else (size, since)


def _expired(limit: float | None) -> bool:
    return limit is not None and time.monotonic() >= limit


def _wait_watching(proc: subprocess.Popen, timeout: float | None, no_progress: float,
                   stream: IO) -> int | None:
    """Wait in ticks so a command that stops writing can be caught between them.

    A suite that hangs at 0% CPU never trips a total deadline the user did not
    set, and `timeout_seconds` defaults to none at all, so the run sat on it
    forever with nothing watching the log. The log IS the signal: it grows while
    the runner reports and stops when the runner does.
    """
    limit = None if timeout is None else time.monotonic() + timeout
    size, since = _stream_size(stream), time.monotonic()
    while True:
        try:
            return proc.wait(timeout=_TICK)
        except subprocess.TimeoutExpired:
            size, since = _progress(stream, size, since)
        if time.monotonic() - since >= no_progress:
            _kill_tree(proc)
            raise NoProgress(no_progress)
        if _expired(limit):
            _kill_tree(proc)
            return None


def run_bounded(command: str, timeout: float | None, *, stream: IO | None = None,
                no_progress: float | None = None, **popen_kwargs) -> int | None:
    """The exit code, or None when the deadline expired and the tree was killed.
    A timeout of None is no deadline at all: the caller waits for the command.

    `no_progress` adds a second deadline on top of the first: the tree is killed
    and NoProgress raised when `stream` has not grown for that many seconds. It
    needs a stream to watch, so it is ignored when output goes to DEVNULL, where
    there is nothing to measure and every command would read as stalled.

    Output goes to DEVNULL by default, never a pipe: nobody here reads it, and a
    pipe outlives the timeout - the drain has no deadline of its own, so the
    call would return when the grandchild holding the handles exits.

    `stream` takes stdout and stderr both, and must be a real file (the lane log
    is one). A file needs no reader, so the kill lands on the deadline the same
    way; a pipe would reintroduce the drain and is the one thing not to pass.
    """
    out = subprocess.DEVNULL if stream is None else stream
    proc = subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL,
                            stdout=out, stderr=subprocess.STDOUT,
                            **_OWN_GROUP, **popen_kwargs)
    if no_progress and stream is not None:
        return _wait_watching(proc, timeout, no_progress, stream)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return None
