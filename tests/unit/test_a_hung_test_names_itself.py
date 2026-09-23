"""A test stuck past every hang bound prints every thread's stack while the
session waits on it.

pytest -q prints a dot per finished test and nothing for one that never
finishes, so a stuck test held a CI measurement until the job was cancelled
with 98% of the unit session printed and no name to start from. pytest's
faulthandler_timeout writes each thread's stack once a test runs past it, and
under xdist the dump reaches the controller's output, so the job log names the
test and the line it waits on. The timeout sits past HOLD_SECONDS, the longest
a correct test can hold a child, so a slow machine never dumps a healthy test.
"""
from pathlib import Path
import tomllib

from hang_guard import HOLD_SECONDS

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_a_test_past_the_longest_hold_dumps_every_threads_stack():
    ini = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]

    assert ini.get("faulthandler_timeout", 0) > HOLD_SECONDS
