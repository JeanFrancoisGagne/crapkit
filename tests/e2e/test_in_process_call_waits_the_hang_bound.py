"""An in-process CLI call that names no bound runs under the hang bound.

run_cli promises the hang bound to a call that names no timeout. The spawn path
kept that promise and the in-process path handed None down, which the in-process
runner reads as no bound at all, so a hung call held its worker forever.
"""
import cli_in_process
import hang_guard
from conftest import run_cli


def _bounds(monkeypatch) -> list:
    """The bound each in-process call ran under."""
    seen = []
    real = cli_in_process._watched

    def watched(argv, timeout):
        seen.append(timeout)
        return real(argv, timeout)

    monkeypatch.setattr(cli_in_process, "_watched", watched)
    return seen


def test_a_call_that_names_no_bound_runs_under_the_hang_bound(tmp_path, monkeypatch):
    seen = _bounds(monkeypatch)

    done = run_cli(tmp_path, "--version")

    assert done.returncode == 0, done.stdout + done.stderr
    assert seen == [hang_guard.HANG_SECONDS]


def test_a_call_that_names_its_bound_runs_under_it(tmp_path, monkeypatch):
    seen = _bounds(monkeypatch)

    longer = hang_guard.HANG_SECONDS + 1

    assert run_cli(tmp_path, "--version", timeout=longer).returncode == 0
    assert seen == [longer]
