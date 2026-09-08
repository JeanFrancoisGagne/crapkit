"""Keep measurement locks until its registered command trees have stopped.

The caller keeps stdin open. EOF means normal release or a dead caller; both
stop any commands still registered before releasing the OS locks.
"""
from contextlib import ExitStack, contextmanager, nullcontext
import json
import os
from pathlib import Path
import sys
import subprocess
import time

from .errors import ToolError
from .locks import exclusive_lock
from .procs import _kill_pid
from ._process_family import Family, ancestor_leases


def _reply(value: dict) -> None:
    print(json.dumps(value), flush=True)


class _ProcessGroup:
    def __init__(self, pid: int, family=None):
        self.pid = pid
        self.family = None if family is None else Family(family)

    def stop(self) -> None:
        completion = nullcontext() if self.family is None else self.family.stopping()
        with completion:
            self._stop_group()

    def _stop_group(self):
        _kill_pid(self.pid)
        while _group_exists(self.pid) and _group_active(self.pid):
            time.sleep(.01)


def _group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _proc_group_member(path: Path, pid: str) -> bool:
    try:
        fields = path.read_text().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return False
    return fields[2] == pid and fields[0] not in ("Z", "X")


def _group_active(pid: int) -> bool:
    """Wait for descriptor closure; zombies cannot keep writing or own locks."""
    if sys.platform.startswith("linux"):
        return any(_proc_group_member(path, str(pid))
                   for path in Path("/proc").glob("[0-9]*/stat"))
    result = subprocess.run(["ps", "-A", "-o", "pgid=", "-o", "stat="],
                            capture_output=True, text=True, check=True)
    return any(group == str(pid) and not state.startswith("Z")
               for group, state in (line.split() for line in result.stdout.splitlines()))


def _command_tree(entry):
    pid, family = (entry['pid'], entry['family']) if isinstance(entry, dict) else (entry, None)
    if os.name == "nt":
        from ._windows_job import Job
        return Job(pid)
    return _ProcessGroup(pid, family)


def _stop_children(children: dict) -> None:
    for child in children.values():
        child.stop()
    children.clear()


def _operation(children, operation, pid):
    if operation == "add":
        key = pid['pid'] if isinstance(pid, dict) else pid
        children[key] = _command_tree(pid)
    elif operation == "cancel":
        _stop_children(children)
    else:
        child = children.pop(pid, None)
        if child is not None:
            child.stop()


def _serve() -> None:
    children = {}
    try:
        for line in sys.stdin:
            operation, pid = json.loads(line)
            _operation(children, operation, pid)
            _reply({"ok": True})
    finally:
        _stop_children(children)


@contextmanager
def _resources(paths, label, optional):
    with ExitStack() as stack:
        held = True
        try:
            for path in paths:
                stack.enter_context(exclusive_lock(Path(path), label=label))
        except ToolError:
            if not optional:
                raise
            stack.close()
            held = False
        yield held


def main(options: dict) -> None:
    try:
        with ancestor_leases(), _resources(**options) as held:
            _reply({"ok": True, "held": held})
            _serve()
    except (OSError, ToolError) as error:
        _reply({"error": str(error)})


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
