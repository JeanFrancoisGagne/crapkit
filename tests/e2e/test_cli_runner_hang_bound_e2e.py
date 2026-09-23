"""A CLI child with no bound of its own waits the hang bound, and a miss shows
what the child printed."""
import pytest

import hang_guard
from conftest import run_cli


def test_a_cli_run_without_its_own_bound_waits_the_hang_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(hang_guard, "HANG_SECONDS", 0)

    with pytest.raises(AssertionError, match="so the child was killed"):
        run_cli(tmp_path, "--version")


def test_a_cli_run_inside_the_bound_returns_its_output(tmp_path):
    done = run_cli(tmp_path, "--version")

    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.startswith("crapkit ")
