"""Submission and broken gate failures cannot strand later worker admissions."""
from contextlib import contextmanager, nullcontext
import queue
import threading
from types import SimpleNamespace

import pytest

from crapkit import _analysis_pool as pools
from crapkit.errors import ToolError


class Gate:
    def __init__(self, *, broken=False):
        self.broken = broken
        self.released = threading.Event()

    def send(self, message):
        if self.broken:
            raise BrokenPipeError("worker exited")
        self.released.set()

    def close(self):
        pass


class Owner:
    held = True

    def __init__(self):
        self.cancelled = False

    def register_then(self, pid, release):
        if self.cancelled:
            raise ToolError("owner cancelled")
        release()

    def cancel(self):
        self.cancelled = True


def test_interrupted_submission_drains_gated_workers_before_shutdown(monkeypatch):
    monkeypatch.setattr(pools, "os", SimpleNamespace(name="posix"))
    gates, pending = [Gate()], queue.Queue()
    owner = Owner()
    @contextmanager
    def own(*args, **kwargs):
        yield owner
    class Executor:
        def __init__(self, **kwargs):
            pass
        def map(self, *args, **kwargs):
            pending.put((123, gates[0]))
            raise KeyboardInterrupt
        def shutdown(self, **kwargs):
            assert gates[0].released.wait(1), "submitted worker kept waiting at its gate"
    context = SimpleNamespace(Queue=lambda: pending, get_start_method=lambda: "fork")
    monkeypatch.setattr(pools.multiprocessing, "get_context", lambda: context)
    monkeypatch.setattr(pending, "close", lambda: None, raising=False)
    monkeypatch.setattr(pending, "join_thread", lambda: None, raising=False)
    monkeypatch.setattr(pools, "available_slots", lambda status: ["0", "1"])
    monkeypatch.setattr(pools, "own_processes", own)
    monkeypatch.setattr(pools, "ProcessPoolExecutor", Executor)
    with pytest.raises(KeyboardInterrupt):
        with pools.analysis_pool(workers=2) as pool:
            list(pool.map(abs, [-1]))
    assert owner.cancelled


def test_spawn_workers_can_start_while_later_jobs_are_still_submitting(monkeypatch):
    monkeypatch.setattr(pools, "os", SimpleNamespace(name="posix"))
    gate, pending, owner = Gate(), queue.Queue(), Owner()
    @contextmanager
    def own(*args, **kwargs):
        yield owner
    class Executor:
        def __init__(self, **kwargs):
            pass
        def map(self, *args, **kwargs):
            pending.put((123, gate))
            assert gate.released.wait(1), "spawn work was held until every job was submitted"
            return iter([1])
        def shutdown(self, **kwargs):
            pass
    context = SimpleNamespace(Queue=lambda: pending, get_start_method=lambda: "spawn")
    monkeypatch.setattr(pools.multiprocessing, "get_context", lambda: context)
    monkeypatch.setattr(pending, "close", lambda: None, raising=False)
    monkeypatch.setattr(pending, "join_thread", lambda: None, raising=False)
    monkeypatch.setattr(pools, "available_slots", lambda status: ["0", "1"])
    monkeypatch.setattr(pools, "own_processes", own)
    monkeypatch.setattr(pools, "ProcessPoolExecutor", Executor)
    with pools.analysis_pool(workers=2) as pool:
        assert list(pool.map(abs, [-1])) == [1]


def test_a_closed_refusal_gate_does_not_strand_the_next_gate():
    owner, pending, errors = Owner(), queue.Queue(), []
    owner.cancel()
    later = Gate()
    pending.put((123, Gate(broken=True)))
    pending.put((456, later))
    pending.put(None)
    pools._register_workers(owner, pending, errors)
    assert later.released.is_set()
    assert errors == ["owner cancelled", "owner cancelled"]


@pytest.mark.parametrize("system,ready,message,exits", [
    ("posix", "parent", "go", True), ("nt", "gate", "refused", True),
    ("posix", "gate", "go", False),
])
def test_worker_gate_requires_live_parent_and_registered_release(monkeypatch, system, ready, message, exits):
    from pathlib import Path
    pending, groups, closed, retained = queue.Queue(), [], [], []
    receive = SimpleNamespace(recv=lambda: message, close=lambda: closed.append("receive"))
    send = SimpleNamespace(close=lambda: closed.append("send"))
    def exit_process(code):
        raise SystemExit(code)
    operating = SimpleNamespace(name=system, setsid=lambda: groups.append("own group"),
                                getpid=lambda: 123, _exit=exit_process)
    context = SimpleNamespace(parent_process=lambda: SimpleNamespace(sentinel="parent"),
                              Pipe=lambda **kwargs: (receive, send))
    monkeypatch.setattr(pools, "os", operating)
    monkeypatch.setattr(pools, "multiprocessing", context)
    monkeypatch.setattr(pools, "wait", lambda handles: ["parent"] if ready == "parent" else [receive])
    monkeypatch.setattr(pools, "_retain_worker_slot", retained.extend)
    monkeypatch.setattr(pools, "_stop_with_parent", lambda parent: None)
    if exits:
        with pytest.raises(SystemExit, match="1"):
            pools._worker_gate(pending, ("slots", ("2.lock", "7.lock")))
    else:
        pools._worker_gate(pending, ("slots", ("2.lock", "7.lock")))
    assert retained == [Path("slots/2.lock"), Path("slots/7.lock")]
    assert pending.get_nowait() == (123, send)
    assert closed == ["receive", "send"]
    assert groups == (["own group"] if system == "posix" else [])

@pytest.mark.parametrize("claims,expected", [([False, True], ["first", "second"]),
                                            ([False, False], ["first", "second"])])
def test_worker_slot_selection_skips_busy_descriptors(monkeypatch, claims, expected):
    seen = []
    remaining = iter(claims)
    def claim(path):
        seen.append(path)
        return next(remaining)
    monkeypatch.setattr(pools, "_claim_worker_slot", claim)
    if any(claims):
        pools._retain_worker_slot(["first", "second"])
    else:
        with pytest.raises(ToolError, match="no analysis worker slot"):
            pools._retain_worker_slot(["first", "second"])
    assert seen == expected


@pytest.mark.parametrize("refused", [False, True])
def test_worker_descriptor_is_closed_only_when_admission_fails(monkeypatch, tmp_path, refused):
    closed = []
    def lock(descriptor):
        assert descriptor == 47
        if refused:
            raise OSError("busy")
    operating = SimpleNamespace(open=lambda *args: 47, close=closed.append, O_CREAT=1, O_RDWR=2)
    monkeypatch.setattr(pools, "os", operating)
    monkeypatch.setattr(pools, "_lock_worker_descriptor", lock)
    assert pools._claim_worker_slot(tmp_path / "0.lock") is not refused
    assert closed == ([47] if refused else [])


def test_pool_startup_transports_the_slot_directory_once(monkeypatch, tmp_path):
    import pickle
    captured = {}
    directory = tmp_path.joinpath(*[f"part{index}" for index in range(16)])
    paths = [directory / f"{index * 2}.lock" for index in range(24)]
    context = SimpleNamespace(Queue=queue.Queue, get_start_method=lambda: "spawn")
    monkeypatch.setattr(pools.multiprocessing, "get_context", lambda: context)
    monkeypatch.setattr(pools, "ProcessPoolExecutor", lambda **options: captured.update(options))
    pools._OwnedPool(Owner(), paths)
    wire = pickle.dumps(captured["initargs"][1])
    useful_bytes = len(str(directory).encode()) + sum(len(path.name.encode()) for path in paths)
    assert len(wire) <= useful_bytes + 128, "worker bootstrap repeats the directory structure"


@pytest.mark.parametrize("interrupt", [False, True])
def test_windows_primary_slots_outlive_local_job_cleanup(monkeypatch, tmp_path, interrupt):
    events, owner = [], Owner()
    @contextmanager
    def lock(path, **kwargs):
        events.append("lock " + path.name)
        try:
            yield
        finally:
            events.append("unlock " + path.name)
    @contextmanager
    def own(paths, **kwargs):
        assert tuple(paths) == (), "analysis retained an extra Windows guardian process"
        events.append("jobs open")
        try:
            yield owner
        finally:
            events.append("jobs complete")
    class Pool:
        def __init__(self, owner, paths):
            events.append("pool open")
        def close(self):
            events.append("pool closed")
    monkeypatch.setattr(pools, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(pools, "exclusive_lock", lock, raising=False)
    monkeypatch.setattr(pools, "own_processes", own)
    monkeypatch.setattr(pools, "_OwnedPool", Pool)
    with pytest.raises(KeyboardInterrupt) if interrupt else nullcontext():
        with pools._held_pool([tmp_path / "0.lock", tmp_path / "1.lock"]):
            if interrupt:
                raise KeyboardInterrupt
    assert owner.cancelled is interrupt
    assert events == ["lock 0.lock", "lock 1.lock", "jobs open", "pool open",
                      "pool closed", "jobs complete", "unlock 1.lock", "unlock 0.lock"]


@pytest.mark.parametrize("error", [ToolError("busy"), OSError("unusable")])
def test_partial_windows_slot_claim_releases_without_starting_jobs(monkeypatch, tmp_path, error):
    events = []
    @contextmanager
    def lock(path, **kwargs):
        if path.name == "1.lock":
            raise error
        try:
            yield
        finally:
            events.append("released")
    def unexpected(*args, **kwargs):
        pytest.fail("partly claimed slots started a process owner")
    monkeypatch.setattr(pools, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(pools, "exclusive_lock", lock, raising=False)
    monkeypatch.setattr(pools, "own_processes", unexpected)
    with pools._held_pool([tmp_path / "0.lock", tmp_path / "1.lock"]) as pool:
        assert pool is None
    assert events == ["released"]
