"""An in-process CLI call past its bound fails its own test, and the session goes on.

The call runs in the worker's thread, so no child can be killed when it hangs.
Ending the worker instead lost the report: pytest's capture owned descriptor 2,
the stack dump went into the capture file and died with the worker, and xdist
said only that the worker crashed. Serial, the whole session ended with no
failure report and no summary. These tests run a real pytest session in a
child, serial and under one xdist worker, because only a session shows whether
the next test still runs.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

E2E = Path(__file__).resolve().parent

SPINS = '''import time

import crapkit.cli
from conftest import run_cli


def test_hangs(tmp_path, monkeypatch):
    def spins(argv):
        print("started the command", flush=True)
        while True:
            time.sleep(0.01)

    monkeypatch.setattr(crapkit.cli, "main", spins)
    run_cli(tmp_path, "worklist", "--json", timeout=1)


def test_the_next_test_runs(tmp_path):
    done = run_cli(tmp_path, "--version")

    assert done.returncode == 0 and done.stdout.startswith("crapkit ")
'''

STUCK_IN_C = '''import time

import cli_in_process
import crapkit.cli
from conftest import run_cli


def test_stuck(tmp_path, monkeypatch):
    def sleeps_in_c(argv):
        time.sleep(600)

    monkeypatch.setattr(cli_in_process, "GRACE_SECONDS", 1)
    monkeypatch.setattr(crapkit.cli, "main", sleeps_in_c)
    run_cli(tmp_path, "worklist", timeout=1)
'''


def _session(tmp_path: Path, name: str, source: str, *options: str) -> subprocess.CompletedProcess:
    """A pytest session over one file that loads tests/e2e/conftest.py as a
    plugin, so its tests call the suite's own run_cli."""
    (tmp_path / name).write_text(source, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(E2E), str(E2E.parent),
                                                      os.environ.get("PYTHONPATH"))))
    return subprocess.run([sys.executable, "-m", "pytest", name, "-p", "conftest",
                           "-p", "no:randomly", "-p", "no:cacheprovider", "--tb=short",
                           "--basetemp", str(tmp_path / "basetemp"), *options],
                          cwd=tmp_path, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)


@pytest.mark.parametrize("workers", ["0", "1"])
def test_a_call_past_its_bound_fails_its_test_and_the_next_test_runs(tmp_path, workers):
    done = _session(tmp_path, "test_spins.py", SPINS, "-n", workers)
    report = done.stdout + done.stderr

    assert done.returncode == 1, report
    assert "1 failed, 1 passed" in report
    assert "FAILED test_spins.py::test_hangs" in report
    assert "'-m', 'crapkit', 'worklist', '--json'] finish within 1 s" in report
    assert "started the command" in report


def test_a_call_stuck_in_c_code_ends_the_worker_and_leaves_its_stack_in_basetemp(tmp_path):
    """Nothing reaches a thread that never returns to Python, so after a grace
    period the worker ends. The stack goes to a file under the worker's
    basetemp, where no capture can swallow it."""
    done = _session(tmp_path, "test_stuck.py", STUCK_IN_C, "-n", "0")

    log = (tmp_path / "basetemp" / "in-process-hangs.log").read_text(encoding="utf-8")
    assert done.returncode != 0, done.stdout + done.stderr
    assert "test_stuck.py::test_stuck" in log
    assert "'-m', 'crapkit', 'worklist'] past its 1 s bound" in log
    assert "in sleeps_in_c" in log
