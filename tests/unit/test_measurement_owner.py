"""Owned command failures release outputs without granting unregistered work."""
import os
import shutil
import signal
import sys

import pytest

from crapkit.errors import ToolError
from crapkit.procs import own_processes, run_bounded


def test_a_dead_owner_cannot_start_a_command(tmp_path):
    marker = tmp_path / "should-not-run"
    command = f'"{sys.executable}" -c "from pathlib import Path; Path(r\'{marker}\').touch()"'
    with pytest.raises(ToolError, match="measurement owner stopped"):
        with own_processes([tmp_path / "owner.lock"]) as owner:
            owner.process.kill()
            owner.process.wait(timeout=10)
            run_bounded(command, 5, owner=owner)
    assert not marker.exists()


def test_a_dead_owner_cannot_confirm_publication(tmp_path):
    with pytest.raises(ToolError, match="before publication"):
        with own_processes([tmp_path / "owner.lock"]) as owner:
            owner.process.kill()
            owner.process.wait(timeout=10)


def test_a_failed_command_releases_the_same_output_for_another_run(tmp_path):
    path = tmp_path / "owner.lock"
    # This checks exit codes and lock release. test_procs owns deadline checks.
    with own_processes([path]) as owner:
        assert run_bounded(f'"{sys.executable}" -c "raise SystemExit(7)"', None, owner=owner) == 7
    with own_processes([path]) as owner:
        assert run_bounded(f'"{sys.executable}" -c "pass"', None, owner=owner) == 0


def test_an_operation_exception_releases_ownership(tmp_path):
    path = tmp_path / "owner.lock"
    with pytest.raises(ValueError, match="parse failed"):
        with own_processes([path]):
            raise ValueError("parse failed")
    with own_processes([path]):
        assert path.is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal return codes")
def test_owned_commands_keep_the_shell_signal_return_code(tmp_path):
    with own_processes([tmp_path / "owner.lock"]) as owner:
        assert run_bounded("kill -TERM $$", 5, owner=owner) == -signal.SIGTERM


def test_output_locks_stay_outside_the_directories_the_runner_clears(tmp_path):
    """A coverage runner owns its reports directory and wipes it on startup.

    crapkit used to keep its lock inside that directory, so vitest's clean step
    met a file this process holds an OS lock on. POSIX unlinks an open file
    happily; Windows answers EBUSY, the lane dies before writing an artifact,
    and with no artifact there is no baseline, no ratchet seed and no commit.
    """
    from crapkit.config import Lane
    from crapkit.lanes import measurement_owner

    reports = tmp_path / ".crapkit/cov/unit-fast"
    reports.mkdir(parents=True)
    lane = Lane(name="unit-fast", command="unused",
                artifact=".crapkit/cov/unit-fast/coverage-final.json",
                parser="istanbul", scopes=(),
                results_artifact=".crapkit/junit/unit-fast.xml")

    with measurement_owner(tmp_path, [lane]):
        held = [path for path in reports.rglob("*") if path.suffix == ".lock"]
        assert held == [], f"lock inside the runner's reports directory: {held}"
        shutil.rmtree(reports)  # what vitest --coverage.clean does on startup
    reports.mkdir(parents=True, exist_ok=True)


def test_two_lane_outputs_sharing_a_filename_get_distinct_locks(tmp_path):
    """Lanes all write `coverage-final.json`; only the directory differs."""
    from crapkit.config import Lane
    from crapkit.lanes import _output_lock

    def lock_for(lane_name):
        return _output_lock(tmp_path / f".crapkit/cov/{lane_name}/coverage-final.json")

    assert lock_for("unit-fast") != lock_for("gateway-core")
