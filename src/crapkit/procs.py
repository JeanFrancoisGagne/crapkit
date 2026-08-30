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

_OWN_GROUP = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
              if os.name == "nt" else {"start_new_session": True})


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


def run_bounded(command: str, timeout: float, **popen_kwargs) -> int | None:
    """The exit code, or None when the deadline expired and the tree was killed.

    Output goes to DEVNULL, never a pipe: nobody here reads it, and a pipe
    outlives the timeout — the drain has no deadline of its own, so the call
    would return when the grandchild holding the handles exits.
    """
    proc = subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            **_OWN_GROUP, **popen_kwargs)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return None
