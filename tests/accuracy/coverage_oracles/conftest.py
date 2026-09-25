"""The coverage-oracles fixtures: the probes scored once per session."""
import os

import pytest

from accuracy.coverage_oracles import probe_repo

LIVE = "coveragepy-live"


def _shared_base(tmp_path_factory):
    """The session's temp root every xdist worker shares."""
    base = tmp_path_factory.getbasetemp()
    return base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base


@pytest.fixture(scope="session")
def probe_run(tmp_path_factory):
    """Every recording plus this interpreter's own coverage.py run over the
    Python probe, scored by one `crapkit coverage` (see probe_repo.py)."""
    base = _shared_base(tmp_path_factory)
    live = {(LIVE, scenario): probe_repo.live_coveragepy(base, scenario)
            for scenario in probe_repo.SCENARIOS}
    run = probe_repo.measure(base, live)
    assert run.code == 0, run.stderr
    return run
