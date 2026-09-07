"""Cancellation stops live command trees before returning their output locks."""
from contextlib import nullcontext
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from crapkit.locks import exclusive_lock
from crapkit.procs import own_processes, run_bounded


TREE = """from pathlib import Path
import json, os, subprocess, sys, time
from crapkit.locks import exclusive_lock
root = Path(sys.argv[1])
if len(sys.argv) > 2:
    with exclusive_lock(root / 'child.lock', label='child'):
        print(os.getpid(), flush=True)
        time.sleep(60)
else:
    with exclusive_lock(root / 'parent.lock', label='parent'):
        child = subprocess.Popen([sys.executable, __file__, str(root), 'child'], stdout=subprocess.PIPE, text=True)
        child_pid = int(child.stdout.readline())
        (root / 'ready.json').write_text(json.dumps([os.getpid(), child_pid]), encoding='utf-8')
        child.wait()
"""


def _wait_until_ready(path):
    deadline = time.monotonic() + 30
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), "both fixture processes must acquire locks before interruption"


def _interrupt_at_wait(monkeypatch, ready, script):
    original = subprocess.Popen.wait
    interrupted = []

    def wait(process, *args, **kwargs):
        if not interrupted and script.name in str(process.args):
            _wait_until_ready(ready)
            assert process.poll() is None
            interrupted.append(process.pid)
            raise KeyboardInterrupt
        return original(process, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "wait", wait)
    return interrupted


def _stop_survivors(path):
    if not path.exists():
        return
    for pid in json.loads(path.read_text(encoding="utf-8")):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


@pytest.mark.parametrize("owned", [False, True], ids=["plain", "owned"])
def test_interrupt_stops_the_parent_and_grandchild_before_returning(tmp_path, monkeypatch, owned):
    script = tmp_path / "tree.py"
    script.write_text(TREE, encoding="utf-8")
    ready = tmp_path / "ready.json"
    # Inject at the OS wait boundary. Windows defers interrupt_main during its wait.
    interrupted = _interrupt_at_wait(monkeypatch, ready, script)
    ownership = own_processes([tmp_path / "output.lock"]) if owned else nullcontext(None)
    released = False
    try:
        with ownership as owner, (tmp_path / "command.log").open("w+b") as log:
            with pytest.raises(KeyboardInterrupt):
                run_bounded(f'"{sys.executable}" "{script}" "{tmp_path}"', None,
                            stream=log, owner=owner)
            assert ready.exists(), "the cancellation must follow both processes acquiring their locks"
            with exclusive_lock(tmp_path / "parent.lock", label="parent"):
                with exclusive_lock(tmp_path / "child.lock", label="child"):
                    released = True
            assert interrupted
        with own_processes([tmp_path / "output.lock"]):
            pass
    finally:
        if not released:
            _stop_survivors(ready)
