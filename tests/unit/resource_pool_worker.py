"""Private workload for the shared analysis pool's public lifetime contract."""
import json
import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
import sys
import time
import threading
from contextlib import contextmanager
from functools import partial

from crapkit._analysis_pool import analysis_pool
from crapkit import _analysis_pool
from crapkit.errors import ToolError
from crapkit.locks import exclusive_lock

OWN = _analysis_pool.own_processes


@contextmanager
def observed_owner(root, mode, *args, **kwargs):
    with OWN(*args, **kwargs) as owner:
        pid = owner.process.pid if owner.process is not None else None
        (root / "guardian.json").write_text(json.dumps({"pid": pid}), encoding="utf-8")
        if mode == "registering":
            owner.register_then = partial(delayed_registration, root, owner.register_then)
        if mode == "refused":
            owner.register_then = refuse
        yield owner


def refuse(pid, release):
    raise ToolError("fixture registration refusal")


def lock_held_after_send(send):
    """A queue's feeder thread holds the queue's write lock until send_bytes
    returns. In a worker, this keeps it for 30 s after the bytes are out, so a
    worker refused in that time exits while it still holds the lock."""
    def held(connection, buf):
        send(connection, buf)
        if multiprocessing.parent_process() and threading.current_thread().name == "QueueFeederThread":
            time.sleep(30)
    return held


def refused_worker_keeps_the_queue_lock():
    """Fork, so the workers inherit the stretched send; then have the owner
    refuse every worker and print the error the caller sees."""
    multiprocessing.set_start_method("fork", force=True)
    Connection._send_bytes = lock_held_after_send(Connection._send_bytes)
    try:
        with analysis_pool(workers=2, worker_budget=2) as pool:
            list(pool.map(abs, [-1], chunksize=1))
    except ToolError as error:
        print(error)


def delayed_registration(root, register, pid, release):
    (root / "registration.json").write_text(json.dumps({"pid": pid}), encoding="utf-8")
    while not (root / "register-release").exists():
        time.sleep(.01)
    register(pid, release)


def abort_fixture(root):
    while not (root / "abort-fixture").exists():
        time.sleep(.05)
    os._exit(97)

def hold(directory):
    root = Path(directory)
    threading.Thread(target=abort_fixture, args=(root,), daemon=True).start()
    pid = os.getpid()
    with exclusive_lock(root / f"writer-{pid}.lock", label="test writer"):
        (root / f"worker-{pid}.json").write_text(json.dumps({"pid": pid}), encoding="utf-8")
        while not (root / "release").exists():
            time.sleep(.01)
        (root / f"late-{pid}").touch()
    return pid


def main(directory, mode="running"):
    root = Path(directory)
    _analysis_pool.own_processes = partial(observed_owner, root, mode)
    (root / "caller.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    if mode == "refused":
        return refused_worker_keeps_the_queue_lock()
    with analysis_pool(workers=2, worker_budget=2) as pool:
        if pool is None:
            raise RuntimeError("private budget unexpectedly occupied")
        list(pool.map(hold, [directory, directory], chunksize=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
