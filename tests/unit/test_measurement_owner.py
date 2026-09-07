"""Owned command failures release outputs without granting unregistered work."""
import os
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
    with own_processes([path]) as owner:
        assert run_bounded(f'"{sys.executable}" -c "raise SystemExit(7)"', 5, owner=owner) == 7
    with own_processes([path]) as owner:
        assert run_bounded(f'"{sys.executable}" -c "pass"', 5, owner=owner) == 0


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
