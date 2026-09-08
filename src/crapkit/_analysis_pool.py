"""Keep shared worker slots until every gated analysis worker has stopped."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import ExitStack, contextmanager
import multiprocessing
from multiprocessing.connection import wait
import os
from pathlib import Path
import threading

from .errors import ToolError
from .locks import exclusive_lock
from .procs import own_processes
from .resources import available_slots, resource_status, worker_lock


def _lock_worker_descriptor(descriptor):
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _claim_worker_slot(path):
    descriptor = os.open(worker_lock(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _lock_worker_descriptor(descriptor)
    except OSError:
        os.close(descriptor)
        return False
    # No Python file object or finalizer may release this before process exit.
    return True


def _retain_worker_slot(paths):
    for path in paths:
        if _claim_worker_slot(path):
            return
    raise ToolError("no analysis worker slot remains available")


def _stop_with_parent(parent):
    wait([parent.sentinel])
    os._exit(1)


def _worker_gate(registrations, paths) -> None:
    if os.name != "nt":
        os.setsid()
    parent = multiprocessing.parent_process()
    directory, names = paths
    _retain_worker_slot(Path(directory) / name for name in names)
    receive, send = multiprocessing.Pipe(duplex=False)
    registrations.put((os.getpid(), send))
    try:
        ready = wait([receive, parent.sentinel])
        if parent.sentinel in ready or receive.recv() != "go":
            os._exit(1)
    finally:
        receive.close()
        send.close()
    threading.Thread(target=_stop_with_parent, args=(parent,), daemon=True).start()


def _release_worker(owner, pid: int, gate, errors: list) -> None:
    try:
        owner.register_then(pid, lambda: gate.send("go"))
    except Exception as error:
        errors.append(str(error))
        _refuse_gate(gate)
    finally:
        gate.close()


def _refuse_gate(gate):
    try:
        gate.send("refused")
    except OSError:
        pass  # The worker can already have exited after its caller died.


def _register_workers(owner, registrations, errors: list) -> None:
    for entry in iter(registrations.get, None):
        _release_worker(owner, *entry, errors)


class _OwnedPool:
    def __init__(self, owner, paths):
        context = multiprocessing.get_context()
        self.start_before_submit = context.get_start_method() != "fork"
        self.registrations = context.Queue()
        # Windows spawn writes this descriptor through a bounded bootstrap pipe.
        packed = (str(Path(paths[0]).parent), tuple(Path(path).name for path in paths))
        self.executor = ProcessPoolExecutor(max_workers=len(paths), mp_context=context,
                                           initializer=_worker_gate,
                                           initargs=(self.registrations, packed))
        self.errors = []
        self.registrar = threading.Thread(target=_register_workers,
                                          args=(owner, self.registrations, self.errors), daemon=True)
        self.started = False

    def map(self, function, jobs, *, chunksize=1):
        # Submit starts all fork workers before adding our registration thread.
        if self.start_before_submit:
            self._start_registrar()
        try:
            results = self.executor.map(function, jobs, chunksize=chunksize)
        finally:
            self._start_registrar()
        try:
            yield from results
        except BrokenProcessPool as error:
            if self.errors:
                raise ToolError(self.errors[0]) from error
            raise

    def _start_registrar(self):
        if not self.started:
            self.registrar.start()
            self.started = True

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
        if self.started:
            self.registrations.put(None)
            self.registrar.join()
        self.registrations.close()
        self.registrations.join_thread()


@contextmanager
def _pool_owner(paths):
    with ExitStack() as stack:
        owned = paths
        if os.name == "nt":
            if not _claim_primary_slots(stack, paths):
                yield None
                return
            owned = ()
        owner = stack.enter_context(own_processes(owned, optional=True, label="analysis workers"))
        yield owner if owner.held else None


def _claim_primary_slots(stack, paths):
    try:
        for path in paths:
            stack.enter_context(exclusive_lock(path, label="analysis workers"))
    except (OSError, ToolError):
        return False
    return True


@contextmanager
def _held_pool(paths):
    with _pool_owner(paths) as owner:
        if owner is None:
            yield None
            return
        pool = _OwnedPool(owner, paths)
        try:
            yield pool
        except BaseException:
            owner.cancel()
            raise
        finally:
            pool.close()


@contextmanager
def analysis_pool(*, workers: int | None = None, worker_budget: int = 0):
    """Yield an owned pool, or None for cheap serial work or busy shared slots.

    Probing does not reserve capacity. The caller on Windows, or a guardian on
    POSIX, claims primary slots before workers start. Actual workers retain
    companion slots through process exit. A competing claim falls back.
    """
    status = resource_status(analysis_workers=workers or 0, worker_budget=worker_budget)
    if status["pool_worker_limit"] == 1:
        yield None
        return
    paths = available_slots(status)
    if len(paths) < 2:
        yield None
        return
    with _held_pool(paths) as pool:
        yield pool
