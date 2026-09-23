"""The measured fixtures of the e2e files build once per worker.

A function-scoped fixture that committed a history and ran `crapkit coverage`
for every test paid for both again each time. Two tests in one worker now get
copies of one build: the same HEAD, and a store written once, so its mtime is
the same in both copies. A fixture that still built per test commits again
under a later clock and writes its store later.
"""
import subprocess
from pathlib import Path

import pytest

import test_brief_packet_e2e
import test_coupling_cache_e2e
import test_report_e2e

STORE = Path(".crapkit") / "crap.sqlite"


def _head(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("fixture", [test_coupling_cache_e2e.coupled_repo,
                                     test_report_e2e.scored_repo,
                                     test_brief_packet_e2e.repo],
                         ids=["coupling_cache", "report", "brief_packet"])
def test_two_tests_in_one_worker_get_copies_of_one_build(tmp_path, fixture):
    """Each call gets its own dir under one private basetemp, the shape pytest
    gives a worker's tests."""
    first, second = tmp_path / "worker" / "one", tmp_path / "worker" / "two"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    a, b = fixture.__wrapped__(first), fixture.__wrapped__(second)

    assert a != b
    assert (a / STORE).stat().st_mtime_ns == (b / STORE).stat().st_mtime_ns
    assert _head(a) == _head(b)
