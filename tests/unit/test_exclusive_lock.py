"""OS ownership excludes peers and ends when its process exits."""
import os
import signal
import subprocess
import sys

import pytest

from crapkit.errors import ToolError
from crapkit.locks import exclusive_lock


OWNER = """from pathlib import Path
import os, sys
from crapkit.locks import exclusive_lock
with exclusive_lock(Path(sys.argv[1]), label='fixture'):
    print(os.getpid(), flush=True)
    sys.stdin.readline()
"""


@pytest.fixture
def held(tmp_path):
    path = tmp_path / "owner.lock"
    process = subprocess.Popen([sys.executable, "-c", OWNER, str(path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        holder = int(process.stdout.readline())
        assert holder > 0
        yield path, process, holder
    finally:
        process.communicate(input="\n" if process.poll() is None else None, timeout=10)


def test_a_second_process_cannot_take_an_owned_file(held):
    path, process, _ = held
    with pytest.raises(ToolError, match="fixture already in use"):
        with exclusive_lock(path, label="fixture"):
            pytest.fail("a peer entered the owned operation")
    process.communicate(input="\n", timeout=10)
    with exclusive_lock(path, label="fixture"):
        assert path.is_file()


def test_a_crashed_owner_releases_the_same_stable_lock_file(held):
    path, process, holder = held
    # A Windows venv launcher has a different PID from the Python lock holder.
    os.kill(holder, signal.SIGTERM)
    process.wait(timeout=10)
    with exclusive_lock(path, label="fixture"):
        assert path.is_file()


def test_independent_files_remain_independent(held, tmp_path):
    with exclusive_lock(tmp_path / "other.lock", label="other fixture"):
        assert held[1].poll() is None


def test_an_exception_releases_ownership(tmp_path):
    path = tmp_path / "owner.lock"
    with pytest.raises(ValueError, match="operation failed"):
        with exclusive_lock(path, label="fixture"):
            raise ValueError("operation failed")
    with exclusive_lock(path, label="fixture"):
        assert path.is_file()
