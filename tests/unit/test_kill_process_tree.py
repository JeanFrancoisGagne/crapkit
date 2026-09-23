"""kill_process_tree ends a real process tree on the running OS.

Every other test replaces it with a fake, so without this file the branch for
the OS a release is verified on never runs: taskkill /T on Windows, the
process group's SIGKILL on POSIX.
"""
import subprocess
import sys

from hang_guard import HANG_SECONDS

from crapkit._process_owner import _OWN_GROUP, kill_process_tree

SLEEPER = [sys.executable, "-c", "import time; time.sleep(600)"]


def test_a_live_tree_is_killed():
    child = subprocess.Popen(SLEEPER, stdin=subprocess.DEVNULL, **_OWN_GROUP)

    kill_process_tree(child.pid)

    assert child.wait(timeout=HANG_SECONDS) != 0


def test_a_tree_that_already_exited_is_left_alone():
    child = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL, **_OWN_GROUP)
    child.wait(timeout=HANG_SECONDS)

    kill_process_tree(child.pid)

    assert child.returncode == 0
