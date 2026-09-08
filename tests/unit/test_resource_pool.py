"""Independent analysis pools share slots held through real worker cleanup."""
import os
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from crapkit.resources import resource_status


@pytest.fixture(autouse=True)
def isolated_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAPKIT_RESOURCE_DIR", str(tmp_path / "slots"))
    monkeypatch.delenv("CRAPKIT_ANALYSIS_WORKERS", raising=False)
    monkeypatch.delenv("CRAPKIT_ANALYSIS_MEMORY_MB", raising=False)


def test_overlapping_pools_share_workers_and_release_after_shutdown():
    from crapkit._analysis_pool import analysis_pool
    with analysis_pool(workers=2, worker_budget=2) as first:
        assert first is not None
        assert list(first.map(abs, [-1, -2], chunksize=1)) == [1, 2]
        with analysis_pool(workers=2, worker_budget=2) as second:
            assert second is None
    with analysis_pool(workers=2, worker_budget=2) as replacement:
        assert list(replacement.map(abs, [-3], chunksize=1)) == [3]


def test_serial_request_does_not_create_budget_files():
    from crapkit._analysis_pool import analysis_pool
    from pathlib import Path
    with analysis_pool(workers=1, worker_budget=2) as pool:
        assert pool is None
    assert not Path(resource_status()["budget_directory"]).exists()


def test_a_smaller_overlapping_request_uses_remaining_slots():
    from crapkit._analysis_pool import analysis_pool
    with analysis_pool(workers=2, worker_budget=4) as first:
        assert first is not None
        with analysis_pool(workers=2, worker_budget=4) as second:
            assert second is not None
            assert list(second.map(abs, [-7], chunksize=1)) == [7]


def test_unusable_coordination_directory_uses_serial_caller(tmp_path, monkeypatch):
    from crapkit._analysis_pool import analysis_pool
    destination = tmp_path / "file"
    destination.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("CRAPKIT_RESOURCE_DIR", str(destination))
    with analysis_pool(workers=2) as pool:
        assert pool is None


def _wait_for(condition):
    deadline = time.monotonic() + 15
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("private worker readiness or cleanup did not complete")
        time.sleep(.01)


@pytest.fixture
def running_pool(tmp_path):
    process = _start_fixture(tmp_path)
    try:
        _wait_for(lambda: len(list(tmp_path.glob("worker-*.json"))) == 2)
        yield tmp_path, process
    finally:
        (tmp_path / "release").touch()
        _drain_fixture(process, tmp_path)


def _start_fixture(root, mode="running"):
    script = Path(__file__).with_name("resource_pool_worker.py")
    return subprocess.Popen([sys.executable, str(script), str(root), mode],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


@pytest.fixture
def registering_pool(tmp_path):
    process = _start_fixture(tmp_path, "registering")
    try:
        _wait_for(lambda: (tmp_path / "registration.json").exists())
        yield tmp_path, process
    finally:
        (tmp_path / "register-release").touch()
        (tmp_path / "release").touch()
        _drain_fixture(process, tmp_path)


def test_delayed_registration_can_release_real_worker_code(registering_pool):
    directory, process = registering_pool
    assert not list(directory.glob("worker-*.json"))
    (directory / "register-release").touch()
    _wait_for(lambda: len(list(directory.glob("worker-*.json"))) == 2)
    (directory / "release").touch()
    output, error = process.communicate(timeout=20)
    assert process.returncode == 0, output + error
    assert len(list(directory.glob("late-*"))) == 2


def test_caller_death_before_registration_cannot_start_worker_code(registering_pool):
    from crapkit._analysis_pool import analysis_pool
    directory, _ = registering_pool
    caller = json.loads((directory / "caller.json").read_text(encoding="utf-8"))["pid"]
    os.kill(caller, signal.SIGTERM)
    _wait_for(lambda: len(_free_slots()) == 2)
    with analysis_pool(workers=2, worker_budget=2) as replacement:
        assert list(replacement.map(abs, [-9], chunksize=1)) == [9]
    assert not list(directory.glob("worker-*.json"))
    assert not list(directory.glob("late-*"))


def _drain_fixture(process, root):
    try:
        process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        (root / "abort-fixture").touch()
        process.communicate(timeout=5)
        raise


def test_a_live_pool_can_write_after_explicit_release(running_pool):
    directory, process = running_pool
    (directory / "release").touch()
    output, error = process.communicate(timeout=20)
    assert process.returncode == 0, output + error
    assert error == "", "normal pool cleanup wrote an interpreter-shutdown error"
    assert len(list(directory.glob("late-*"))) == 2


def test_caller_death_stops_writers_before_shared_slots_are_reused(running_pool):
    from crapkit._analysis_pool import analysis_pool
    from crapkit.locks import exclusive_lock
    directory, _ = running_pool
    caller = json.loads((directory / "caller.json").read_text(encoding="utf-8"))["pid"]
    os.kill(caller, signal.SIGTERM)
    _wait_for(lambda: len(_free_slots()) == 2)
    with analysis_pool(workers=2, worker_budget=2) as replacement:
        assert replacement is not None
        for path in directory.glob("writer-*.lock"):
            with exclusive_lock(path, label="old writer"):
                pass
        assert list(replacement.map(abs, [-9], chunksize=1)) == [9]
    assert not list(directory.glob("late-*"))


def _free_slots():
    from crapkit.resources import available_slots
    return available_slots(resource_status(analysis_workers=2, worker_budget=2))


def test_registration_refusal_cannot_release_work_or_keep_slots(monkeypatch):
    from crapkit._analysis_pool import analysis_pool
    from crapkit.procs import _ProcessOwner
    from crapkit.errors import ToolError
    def refuse(self, pid, release):
        raise ToolError("fixture registration refusal")
    monkeypatch.setattr(_ProcessOwner, "register_then", refuse)
    with pytest.raises(ToolError, match="fixture registration refusal"):
        with analysis_pool(workers=2, worker_budget=2) as pool:
            list(pool.map(abs, [-1], chunksize=1))
    assert len(_free_slots()) == 2


def _stopped_linux(pid):
    path = Path(f"/proc/{pid}/stat")
    if not path.exists():
        return True
    return path.read_text().rsplit(")", 1)[1].split()[0] in {"Z", "X"}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux process-group owner death")
def test_guardian_death_keeps_live_worker_slots_until_caller_death(running_pool):
    directory, _ = running_pool
    guardian = json.loads((directory / "guardian.json").read_text(encoding="utf-8"))["pid"]
    caller = json.loads((directory / "caller.json").read_text(encoding="utf-8"))["pid"]
    os.kill(guardian, signal.SIGKILL)
    _wait_for(lambda: _stopped_linux(guardian))
    try:
        assert _free_slots() == [], "live workers lost their budget when only the guardian died"
    finally:
        os.kill(caller, signal.SIGTERM)
    _wait_for(lambda: len(_free_slots()) == 2)
    assert not list(directory.glob("late-*"))


def test_a_slot_claimed_after_the_probe_falls_back_before_pool_creation(monkeypatch):
    from crapkit import _analysis_pool
    from crapkit.resources import available_slots
    paths = available_slots(resource_status(analysis_workers=2, worker_budget=2))
    with _analysis_pool.analysis_pool(workers=2, worker_budget=2) as occupied:
        assert occupied is not None
        monkeypatch.setattr(_analysis_pool, "available_slots", lambda status: paths)
        with _analysis_pool.analysis_pool(workers=2, worker_budget=2) as refused:
            assert refused is None


def exit_worker(value):
    os._exit(value)


def test_unexpected_worker_exit_refuses_and_releases_the_budget():
    from crapkit._analysis_pool import analysis_pool
    from concurrent.futures.process import BrokenProcessPool
    with pytest.raises(BrokenProcessPool):
        with analysis_pool(workers=2, worker_budget=2) as pool:
            list(pool.map(exit_worker, [1], chunksize=1))
    assert len(_free_slots()) == 2
