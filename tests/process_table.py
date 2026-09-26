"""One lock over the host's process table, for tests that change or read it.

A crapkit older than 0.8.1 reads every /proc/<pid>/stat on a Linux host in the
locale when a command's process group outlives its leader, so one process
named in bytes that are not UTF-8 anywhere on the host stops it with
`measurement owner stopped before confirming ownership`. A test that keeps
such a process alive holds the lock exclusively, and a test that runs an old
release holds it shared, so the two never overlap under xdist. The lock file
sits in the system temp directory, where the unit and e2e sessions both see it.
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
from pathlib import Path

LOCK = Path(tempfile.gettempdir()) / "crapkit-tests-process-table.lock"


@contextlib.contextmanager
def hold(*, naming: bool):
    """Held while a test keeps a process with a name that is not UTF-8 on the
    host (naming=True), or runs a release older than 0.8.1 (naming=False).
    Only Linux has a /proc such an owner scans, so elsewhere nothing is held."""
    if not sys.platform.startswith("linux"):
        yield
        return
    import fcntl

    with open(LOCK, "a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX if naming else fcntl.LOCK_SH)
        yield
