"""Own command trees until they stop, and keep measurement locks until then.

`own_processes` yields an owner client. Windows without leases registers Jobs
in this process. Every other owner is a guardian: this module run as a script,
holding the locks and the registered trees. The caller keeps its stdin open.
EOF means normal release or a dead caller; both stop any commands still
registered before releasing the OS locks.

A caller asks `prepare` for a command's registration before spawning it and
hands that registration back to `register_then` unread. Requests to the
guardian are built and parsed here and nowhere else.
"""
from contextlib import ExitStack, contextmanager, nullcontext, suppress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .errors import ToolError
from .locks import exclusive_lock

_OWN_GROUP = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
              if os.name == "nt" else {"start_new_session": True})

_OWNER_INPUTS = set()
_OWNER_INPUTS_LOCK = threading.RLock()


def _close_forked_inputs():
    # Raw close cannot flush a parent's pending protocol bytes into its owner.
    for stream in _OWNER_INPUTS:
        stream.close()
    _OWNER_INPUTS.clear()
    _OWNER_INPUTS_LOCK.release()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(before=_OWNER_INPUTS_LOCK.acquire,
                        after_in_parent=_OWNER_INPUTS_LOCK.release,
                        after_in_child=_close_forked_inputs)


def kill_process_tree(pid: int) -> None:
    """Kill a process and its descendants: taskkill /T on Windows, the group on POSIX."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def command_registration(popen_kwargs):
    """A command's registration and the Popen kwargs that start it.

    POSIX names the command's family before spawn, so nested guardians can hold
    leases in it; registration creates it. Windows Jobs need no family.
    """
    if os.name == "nt":
        return None, popen_kwargs
    from ._process_family import command_environment
    family, environment = command_environment(popen_kwargs.get("env"))
    return family, {**popen_kwargs, "env": environment}


class CommandCancelled(Exception):
    """The command owner stopped this request and refuses further starts."""


class ProcessOwner:
    """Registers command trees and stops them; `cancel` stops all and refuses more.

    A subclass says where the trees live: `_send` delivers one request.
    `process` is the guardian, or None when the trees live in this process.
    """

    held = True
    process = None

    def __init__(self):
        self._requests = threading.Lock()
        self.cancelled = False

    def prepare(self, popen_kwargs):
        """The opaque registration for a command, and the kwargs that start it."""
        return command_registration(popen_kwargs)

    def register_then(self, pid, release, registration=None):
        """Register a gated process, then release it before cancellation can enter."""
        with self._requests:
            self.check_cancelled()
            self._send({"op": "add", "pid": pid, "family": registration})
            release()

    def stop(self, pid):
        """Stop the tree registered for pid, wait for it, and forget it."""
        with self._requests:
            self._send({"op": "remove", "pid": pid})

    def check_cancelled(self):
        if self.cancelled:
            raise CommandCancelled("command owner was cancelled")

    def cancel(self):
        """Stop registered trees and refuse starts, keeping resource leases held."""
        with self._requests:
            if not self.cancelled:
                self.cancelled = True
                self._send({"op": "cancel"})

    def check(self):
        """Raise when ownership ended before the caller could publish."""


class _LocalOwner(ProcessOwner):
    """Without leases, caller-owned Jobs already survive caller failure safely.

    The kernel owns live Jobs through this process's handles, so `check` has
    nothing to confirm.
    """

    def __init__(self):
        super().__init__()
        self.children = {}

    def _send(self, request):
        _operation(self.children, request)


class _GuardianOwner(ProcessOwner):
    """The client of a guardian process that holds the locks and the trees."""

    def __init__(self, process, log=None):
        super().__init__()
        self.process = process
        self.log = log

    def receive(self) -> dict:
        line = self.process.stdout.readline()
        if not line:
            raise self._stopped("before confirming ownership")
        result = json.loads(line)
        if not result.get("ok"):
            raise ToolError(result.get("error", "measurement owner refused the operation"))
        return result

    def _send(self, request):
        try:
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()
            self.receive()
        except OSError as error:
            raise self._stopped("during command registration") from error

    def check(self) -> None:
        if self.process.poll() is not None:
            raise self._stopped("before publication")

    def _stopped(self, when: str) -> ToolError:
        """The exit-5 line for every way the guardian can vanish, naming the
        log its stderr went to and whether it wrote there."""
        said = "" if self.log is None else self.log.said()
        return ToolError(f"measurement owner stopped {when}{said}")


class _OwnerLog:
    """Where a guardian's stderr goes: appended to `_log_path`, and nowhere
    when that file cannot be opened, since an owner that cannot log still
    guards. Opened in append mode because owners of other commands on the
    same checkout write to it too."""

    def __init__(self, paths):
        try:
            self.path = _log_path(paths)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = open(self.path, "ab")
        except (OSError, RuntimeError):
            self.stream = None
        self.start = self.stream.tell() if self.stream else 0

    def target(self):
        """What Popen takes as the guardian's stderr."""
        return self.stream or subprocess.DEVNULL

    def close(self) -> None:
        """The guardian holds its own descriptor once it has started."""
        if self.stream:
            self.stream.close()

    def said(self) -> str:
        """The clause that ends the exit-5 line. A killed guardian writes
        nothing, and an older owner's traceback above this one's start is not
        its cause."""
        if self.stream is None:
            return ""
        if _size(self.path) > self.start:
            return f"; its error is at the end of {self.path}"
        return f"; it wrote nothing to {self.path}"


def _log_path(paths) -> Path:
    """`owner.log` in the `.crapkit` directory nearest one of the owner's
    locks, which is the checkout it guards. An owner holding no lock there (a
    scoped test run, the MCP server, a probe) logs beside the measurement
    locks in the per-user cache."""
    states = (parent for path in paths for parent in Path(path).parents if parent.name == ".crapkit")
    state = next(states, None)
    return (state or Path.home() / ".cache" / "crapkit") / "owner.log"


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


@contextmanager
def _local_owner():
    owner = _LocalOwner()
    try:
        yield owner
    finally:
        owner.cancel()


@contextmanager
def own_processes(paths, *, optional: bool = False, label: str = "measurement"):
    """Keep leases until tree cleanup; lease-free Windows Jobs need no guardian."""
    paths = tuple(paths)
    context = (_local_owner() if os.name == "nt" and not paths else
               _external_owner(paths, optional=optional, label=label))
    with context as owner:
        yield owner


@contextmanager
def _external_owner(paths, *, optional: bool = False, label: str = "measurement"):
    """Own measurement outputs across caller crashes and command cleanup."""
    options = {"paths": list(map(str, paths)), "optional": optional, "label": label}
    bootstrap = ("import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); "
                 "runpy.run_module('crapkit._process_owner', run_name='__main__')")
    package_root = str(Path(__file__).resolve().parent.parent)
    log = _OwnerLog(paths)
    with _OWNER_INPUTS_LOCK:
        try:
            process = subprocess.Popen([sys.executable, "-c", bootstrap, package_root, json.dumps(options)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=log.target(), text=True, encoding="utf-8", **_OWN_GROUP)
        finally:
            log.close()
        raw_input = process.stdin.buffer.raw
        _OWNER_INPUTS.add(raw_input)
    owner = _GuardianOwner(process, log)
    try:
        owner.held = owner.receive()["held"]
        yield owner
        owner.check()
    finally:
        with _OWNER_INPUTS_LOCK:
            _OWNER_INPUTS.discard(raw_input)
            close_input(process)
        process.wait()
        process.stdout.close()


def close_input(process) -> None:
    """Close a child's input pipe when it has one. A child that already exited
    took its end of the pipe with it, and that close error is not ours."""
    if process.stdin is not None:
        with suppress(OSError):
            process.stdin.close()


def _reply(value: dict) -> None:
    print(json.dumps(value), flush=True)


class _ProcessGroup:
    def __init__(self, pid: int, family=None):
        self.pid = pid
        self.family = None if family is None else _family(family)

    def stop(self) -> None:
        completion = nullcontext() if self.family is None else self.family.stopping()
        with completion:
            self._stop_group()

    def _stop_group(self):
        kill_process_tree(self.pid)
        while _group_exists(self.pid) and _group_active(self.pid):
            time.sleep(.01)


def _family(path):
    from ._process_family import Family
    return Family(path)


def _group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _proc_group_member(path: Path, pid: str) -> bool:
    """Read as bytes: the name in parentheses is whatever the process set,
    cut at 15 bytes, and only the ASCII fields after it are read."""
    try:
        fields = path.read_bytes().rsplit(b")", 1)[1].split()
    except (OSError, IndexError):
        return False
    return fields[2] == pid.encode() and fields[0] not in (b"Z", b"X")


def _group_active(pid: int) -> bool:
    """Wait for descriptor closure; zombies cannot keep writing or own locks."""
    if sys.platform.startswith("linux"):
        return any(_proc_group_member(path, str(pid))
                   for path in Path("/proc").glob("[0-9]*/stat"))
    result = subprocess.run(["ps", "-A", "-o", "pgid=", "-o", "stat="],
                            capture_output=True, text=True, check=True)
    return any(group == str(pid) and not state.startswith("Z")
               for group, state in (line.split() for line in result.stdout.splitlines()))


def _command_tree(pid, family):
    if os.name == "nt":
        from ._windows_job import Job
        return Job(pid)
    return _ProcessGroup(pid, family)


def _stop_children(children: dict) -> None:
    for child in children.values():
        child.stop()
    children.clear()


def _operation(children, request):
    operation = request["op"]
    if operation == "add":
        children[request["pid"]] = _command_tree(request["pid"], request["family"])
    elif operation == "cancel":
        _stop_children(children)
    else:
        _stop_child(children.pop(request["pid"], None))


def _stop_child(child):
    if child is not None:
        child.stop()


def _serve() -> None:
    children = {}
    try:
        for line in sys.stdin:
            _operation(children, json.loads(line))
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
    from ._process_family import ancestor_leases
    try:
        with ancestor_leases(), _resources(**options) as held:
            _reply({"ok": True, "held": held})
            _serve()
    except (OSError, ToolError) as error:
        _reply({"error": str(error)})


def _logged_main(argv: list[str]) -> None:
    """Run the guardian. An error it did not expect goes to its stderr, which
    the caller points at owner.log, under a dated line naming this process:
    owners of several commands append to one log."""
    try:
        main(json.loads(argv[1]))
    except Exception:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"{stamp} measurement owner {os.getpid()} stopped:", file=sys.stderr, flush=True)
        raise


if __name__ == "__main__":
    _logged_main(sys.argv)
