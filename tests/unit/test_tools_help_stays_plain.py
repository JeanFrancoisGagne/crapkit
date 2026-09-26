"""The development and release scripts print plain help and usage in a pipe.

Python 3.14's argparse colours help and usage when FORCE_COLOR or
PYTHON_COLORS=1 is set, and a pipe does not stop it. The scripts under tools/
are read through pipes: CI job logs, the suite's usage-error tests, an agent
reading --help. They are stdlib-only and cannot import crapkit's parser, so each
builds its own parser with color=False from 3.14 on. Before 3.14 argparse has no
colour to switch off.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hang_guard import HANG_SECONDS

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = [
    "tools/release/release.py",
    "tools/testing/ci.py",
    "tools/testing/run.py",
    "tools/docs/generate.py",
    "tools/demo/generate.py",
    "tools/evidence/publication.py",
    "tools/notes/pytest_cov7_repro.py",
]
FORCED = {
    "FORCE_COLOR=1": {"FORCE_COLOR": "1"},
    "PYTHON_COLORS=1": {"PYTHON_COLORS": "1"},
    "TERM=dumb FORCE_COLOR=1": {"TERM": "dumb", "FORCE_COLOR": "1"},
}
pytestmark = pytest.mark.skipif(sys.version_info < (3, 14),
                                reason="argparse colours help and usage from Python 3.14 on")


def _run(script: str, argument: str, forced: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, script, argument], cwd=ROOT,
                          env={**os.environ, **FORCED[forced]}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=HANG_SECONDS)


@pytest.mark.parametrize("forced", sorted(FORCED))
@pytest.mark.parametrize("script", SCRIPTS)
def test_help_in_a_pipe_is_plain_when_colour_is_forced(script, forced):
    done = _run(script, "--help", forced)

    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("usage: "), done.stdout
    assert "\x1b" not in done.stdout + done.stderr, done.stdout


@pytest.mark.parametrize("script", SCRIPTS)
def test_a_usage_error_in_a_pipe_is_plain_when_colour_is_forced(script):
    done = _run(script, "--no-such-flag", "FORCE_COLOR=1")

    assert done.returncode == 2, done.stderr
    assert done.stderr.startswith("usage: "), done.stderr
    assert "\x1b" not in done.stderr, done.stderr
