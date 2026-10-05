"""A pytest session a test starts keeps its temp dirs under this session's own.

Every session that makes a basetemp in the user-wide pytest-of-<user> root
deletes, at its sessionfinish, each unlocked numbered dir three or more behind
the newest. A nested session there inherited a finished full suite's basetemp,
81,679 files on one Windows runner, and run.py's e2e session spent past the
test's 120 s bound deleting it: test_runner_process_lifetime.py hung that way
in 11 Windows runs.
"""
from pathlib import Path
import sys

from hang_guard import run

CHILD = "def test_child(tmp_path):\n    print('BASETEMP', tmp_path)\n"


def test_a_pytest_a_test_starts_makes_its_basetemp_under_this_sessions(tmp_path, tmp_path_factory):
    (tmp_path / "test_child.py").write_text(CHILD, encoding="utf-8")

    done = run([sys.executable, "-m", "pytest", "test_child.py", "-s", "-q", "-p", "no:randomly",
                "-p", "no:cacheprovider"], cwd=tmp_path, text=True, encoding="utf-8")

    assert done.returncode == 0, done.stdout + done.stderr
    printed = [line.split(" ", 1)[1] for line in done.stdout.splitlines()
               if line.startswith("BASETEMP ")]
    assert len(printed) == 1, done.stdout
    assert Path(printed[0]).is_relative_to(tmp_path_factory.getbasetemp())
