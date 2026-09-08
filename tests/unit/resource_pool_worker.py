"""Private workload for the shared analysis pool's public lifetime contract."""
import json
import os
from pathlib import Path
import sys
import time
import threading
from contextlib import contextmanager
from functools import partial

from crapkit._analysis_pool import analysis_pool
from crapkit import _analysis_pool
from crapkit.locks import exclusive_lock

OWN = _analysis_pool.own_processes


@contextmanager
def observed_owner(root, mode, *args, **kwargs):
    with OWN(*args, **kwargs) as owner:
        pid = owner.process.pid if owner.process is not None else None
        (root / "guardian.json").write_text(json.dumps({"pid": pid}), encoding="utf-8")
        if mode == "registering":
            owner.register_then = partial(delayed_registration, root, owner.register_then)
        yield owner


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
    with analysis_pool(workers=2, worker_budget=2) as pool:
        if pool is None:
            raise RuntimeError("private budget unexpectedly occupied")
        list(pool.map(hold, [directory, directory], chunksize=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
