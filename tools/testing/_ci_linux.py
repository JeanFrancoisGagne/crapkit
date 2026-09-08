"""Own CI descendants even when an unchanged historical runner creates sessions.

The separate guardian is their actual ancestor and Linux subreaper. It signals
only its own children, without reaping between the list and the signal batch.
The kernel's ECHILD result ends cleanup; an empty /proc list does not.
"""
from contextlib import ExitStack, contextmanager
import ctypes
import importlib
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time


class _CallerGone(Exception):
    pass


class _Owner:
    def __init__(self, process):
        self.process = process
        self.lock = threading.Lock()

    def receive(self):
        line = self.process.stdout.readline()
        if not line:
            raise OSError("CI guardian stopped before confirming command cleanup")
        response = json.loads(line)
        if "error" in response:
            raise OSError(*response["error"])
        return response

    def run(self, command, capture_output, cwd, env):
        request = {"argv": command, "capture": capture_output,
                   "cwd": str(cwd) if cwd is not None else os.getcwd(),
                   "env": dict(os.environ) if env is None else env}
        with self.lock:
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()
            response = self.receive()
        return subprocess.CompletedProcess(command, response["code"],
                                           response["stdout"], response["stderr"])


@contextmanager
def own_processes(paths):
    if paths:
        raise ValueError("CI guardian does not acquire artifact leases")
    with ExitStack() as descriptors:
        output = os.dup(1)
        descriptors.callback(os.close, output)
        errors = os.dup(2)
        descriptors.callback(os.close, errors)
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", str(Path(__file__).resolve()), str(output), str(errors)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
            pass_fds=(output, errors), start_new_session=True)
    owner = _Owner(process)
    try:
        owner.receive()
        yield owner
    finally:
        _close_input(process)
        process.wait()
        process.stdout.close()


def _close_input(process):
    try:
        process.stdin.close()
    except OSError:
        pass


def run_owned(command, *, owner=None, capture_output=False, cwd=None, env=None):
    if owner is not None:
        return owner.run(command, capture_output, cwd, env)
    with own_processes(()) as held:
        return held.run(command, capture_output, cwd, env)


def _ancestor_leases():
    package = Path(__file__).resolve().parents[2] / "src/crapkit"
    spec = importlib.util.spec_from_file_location(
        "_crapkit_ci_guardian", package / "__init__.py", submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    family = importlib.import_module(spec.name + "._process_family")
    return family.ancestor_leases()


def _subreaper():
    library = ctypes.CDLL(None, use_errno=True)
    library.prctl.argtypes = [ctypes.c_int, *([ctypes.c_ulong] * 4)]
    if library.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    _direct_children()  # Refuse an unavailable procfs before starting commands.


def _notice(number, frame):
    pass  # set_wakeup_fd writes the notification without reaping any child.


@contextmanager
def _child_signals():
    reader, writer = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        previous = signal.signal(signal.SIGCHLD, _notice)
        wakeup = signal.set_wakeup_fd(writer, warn_on_full_buffer=False)
        try:
            yield reader
        finally:
            signal.set_wakeup_fd(wakeup)
            signal.signal(signal.SIGCHLD, previous)
    finally:
        os.close(reader)
        os.close(writer)


def _direct_children():
    path = Path(f"/proc/self/task/{os.getpid()}/children")
    return [int(word) for word in path.read_text().split()]


def _signal_children():
    # Exited children remain unreaped, so these child IDs cannot be reused
    # between collection and signaling. Only this main thread calls waitpid.
    children = _direct_children()
    for pid in children:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _reap(block=False):
    reaped = {}
    while True:
        try:
            pid, status = os.waitpid(-1, 0 if block else os.WNOHANG)
        except ChildProcessError:
            return reaped, False
        if not pid:
            return reaped, True
        reaped[pid] = os.waitstatus_to_exitcode(status)


def _stop_children():
    ended = {}
    while True:
        try:
            _signal_children()
        except OSError:
            _reap(block=True)  # Keep ancestor leases until every child exits.
            raise
        reaped, children = _reap()
        ended.update(reaped)
        if not children:
            return ended
        time.sleep(.01)  # Retry only active cleanup, including omitted children.


def _wait_event(wakeup):
    ready, _, _ = select.select((sys.stdin.fileno(), wakeup), (), ())
    if sys.stdin.fileno() in ready:
        if not os.read(sys.stdin.fileno(), 1):
            raise _CallerGone
        raise OSError("CI guardian received overlapping commands")
    os.read(wakeup, 65536)


def _wait_root(process, wakeup):
    while True:
        ended, children = _reap()
        if process.pid in ended:
            process.returncode = ended[process.pid]
            return process.returncode
        if not children:
            raise OSError("CI guardian lost its command's exit status")
        _wait_event(wakeup)


def _streams(stack, capture, output, errors):
    if capture:
        return tuple(stack.enter_context(tempfile.TemporaryFile()) for _ in range(2))
    return output, errors


def _text(stream, capture):
    if not capture:
        return None
    stream.seek(0)
    return stream.read().decode("utf-8").replace("\r\n", "\n")


def _execute(request, wakeup, output, errors):
    with ExitStack() as stack:
        streams = _streams(stack, request["capture"], output, errors)
        process = subprocess.Popen(request["argv"], cwd=request["cwd"], env=request["env"],
                                   stdin=subprocess.DEVNULL, stdout=streams[0], stderr=streams[1],
                                   start_new_session=True)
        try:
            code = _wait_root(process, wakeup)
        finally:
            ended = _stop_children()
            if process.returncode is None:
                process.returncode = ended.get(process.pid, -signal.SIGKILL)
        return {"code": code, "stdout": _text(streams[0], request["capture"]),
                "stderr": _text(streams[1], request["capture"])}


def _reply(value):
    data = memoryview((json.dumps(value) + "\n").encode("utf-8"))
    while data:
        data = data[os.write(sys.stdout.fileno(), data):]


def _answer(line, wakeup, output, errors):
    try:
        return _execute(json.loads(line), wakeup, output, errors)
    except OSError as error:
        return {"error": [error.errno, error.strerror or str(error), error.filename]}


def _serve(output, errors):
    os.set_inheritable(output, False)
    os.set_inheritable(errors, False)
    _subreaper()
    with _ancestor_leases(), _child_signals() as wakeup:
        try:
            _reply({"ready": True})
            for line in sys.stdin:
                _reply(_answer(line, wakeup, output, errors))
        finally:
            _stop_children()


def main():
    try:
        _serve(*map(int, sys.argv[1:]))
    except (_CallerGone, BrokenPipeError):
        return 0
    except OSError as error:
        _reply({"error": [error.errno, error.strerror or str(error), error.filename]})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
