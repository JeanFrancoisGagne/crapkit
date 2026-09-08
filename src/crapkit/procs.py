"""Shell commands whose completion includes cleanup of their descendants.

A separate owner registers each gated launcher before the command starts.
Windows Jobs and POSIX process groups retain ownership after the shell exits.
Completion, timeout and caller death stop the owned group before its resources
are released. POSIX commands must keep their inherited group; an explicit
setsid daemon is outside that ownership. Untimed commands have no deadline.
"""
from __future__ import annotations

import os
from pathlib import Path
from contextlib import ExitStack, contextmanager, nullcontext
import json
import re
import signal
import shlex
import subprocess
import sys
import threading
import tempfile
import time
from typing import IO

_OWN_GROUP = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
              if os.name == "nt" else {"start_new_session": True})

# How often the progress watch looks at the stream. Small enough that the kill
# lands close to the deadline, large enough that watching a two-hour suite costs
# nothing measurable.
_TICK = 0.5

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


def prepare_template(template: str, values: dict[str, list[str]]) -> tuple[str, dict[str, str]]:
    """Render whole-argument placeholders and return required environment values.

    Windows percent substitutions carry caret-escaped CRT arguments without
    changing the command's expansion mode. Multiline arguments need delayed
    expansion, with static bangs protected from that extra pass. Quoting a
    placeholder in the template is optional; quoting belongs to this function.
    """
    values = {name: arguments for name, arguments in values.items() if '{' + name + '}' in template}
    delayed = _multiline_mode(template, values)
    environment, replacements = {}, {}
    for index, (name, arguments) in enumerate(values.items()):
        variable = f"CRAPKIT_LITERAL_{index}"
        replacements[name] = _literal_arguments(arguments, variable, environment, delayed)
    if delayed:
        environment["CRAPKIT_LITERAL_BANG"] = "!"
        template = template.replace("!", "!CRAPKIT_LITERAL_BANG!")
    template = _replace_placeholders(template, replacements)
    if delayed:
        template = f'cmd /D /V:ON /S /C "{template}"'
    return template, environment


def _has_newlines(values: dict[str, list[str]]) -> bool:
    return any("\n" in argument or "\r" in argument
               for arguments in values.values() for argument in arguments)


def _multiline_mode(template: str, values: dict[str, list[str]]) -> bool:
    if os.name != "nt" or not _has_newlines(values):
        return False
    if re.search(r"%[^%\r\n]+%", template):
        from .errors import ToolError
        raise ToolError("Windows templates cannot combine multiline arguments with percent "
                        "environment expansion; use a literal command path for this template")
    return True


def _literal_arguments(arguments: list[str], variable: str, environment: dict, delayed: bool) -> str:
    if not arguments:
        return ""
    if os.name == "nt":
        return _windows_arguments(arguments, variable, environment, delayed)
    return " ".join(shlex.quote(arg) for arg in arguments)


def _windows_arguments(arguments: list[str], variable: str, environment: dict, delayed: bool) -> str:
    raw = subprocess.list2cmdline(arguments)
    environment[variable] = raw if delayed else "".join("^" + char for char in raw)
    marker = "!" if delayed else "%"
    return f"{marker}{variable}{marker}"


def _replace_placeholders(template: str, replacements: dict[str, str]) -> str:
    if not replacements:
        return template
    names = "|".join(re.escape(name) for name in replacements)
    pattern = re.compile(r"(?P<quote>['\"]?)\{(?P<name>" + names + r")\}(?P=quote)")
    return pattern.sub(lambda match: replacements[match["name"]], template)


class NoProgress(Exception):
    """The command was alive and silent for `seconds`, and its tree was killed.

    Only a caller that passed `no_progress` can see this. A total deadline still
    returns None, because the two say different things: one command ran too
    long, the other stopped doing anything at all.
    """

    def __init__(self, seconds: float) -> None:
        super().__init__(f"no output for {seconds:g}s")
        self.seconds = seconds


def _kill_tree(proc: subprocess.Popen) -> None:
    """The shell and everything under it, then reap the shell."""
    _kill_pid(proc.pid)
    proc.wait()


def _kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


class CommandCancelled(Exception):
    """The command owner stopped this request and refuses further starts."""


class _ProcessOwner:
    def __init__(self, process):
        self.process = process
        self._requests = threading.Lock()
        self.cancelled = False

    def receive(self) -> dict:
        from .errors import ToolError
        line = self.process.stdout.readline()
        if not line:
            raise ToolError("measurement owner stopped before confirming ownership")
        result = json.loads(line)
        if not result.get("ok"):
            raise ToolError(result.get("error", "measurement owner refused the operation"))
        return result

    def request(self, operation: str, pid: int) -> None:
        with self._requests:
            if operation == "add":
                self.check_cancelled()
            self._request(operation, pid)

    def _request(self, operation: str, pid: int) -> None:
        from .errors import ToolError
        try:
            self.process.stdin.write(json.dumps((operation, pid)) + "\n")
            self.process.stdin.flush()
            self.receive()
        except OSError as error:
            raise ToolError("measurement owner stopped during command registration") from error

    def start(self, process, family=None):
        """Register and release one launcher atomically against cancellation."""
        self.register_then(process.pid, lambda: _release_launcher(process), family=family)

    def register_then(self, pid, release, *, family=None):
        """Register a gated process, then release it before cancellation can enter."""
        with self._requests:
            self.check_cancelled()
            self._request("add", pid if family is None else {"pid": pid, "family": family})
            release()

    def check_cancelled(self):
        if self.cancelled:
            raise CommandCancelled("command owner was cancelled")

    def cancel(self):
        """Stop registered trees and refuse starts, keeping resource leases held."""
        with self._requests:
            if not self.cancelled:
                self.cancelled = True
                self._request("cancel", 0)

    def check(self) -> None:
        from .errors import ToolError
        if self.process.poll() is not None:
            raise ToolError("measurement owner stopped before publication")


class _WindowsOwner(_ProcessOwner):
    """Without leases, caller-owned Jobs already survive caller failure safely."""

    def __init__(self):
        super().__init__(None)
        self.children = {}
        self.held = True

    def _request(self, operation, pid):
        from ._process_owner import _operation
        _operation(self.children, operation, pid)

    def check(self):
        pass  # The kernel owns live Jobs through this process's handles.


@contextmanager
def _local_owner():
    owner = _WindowsOwner()
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
    with _OWNER_INPUTS_LOCK:
        process = subprocess.Popen([sys.executable, "-c", bootstrap, package_root, json.dumps(options)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8", **_OWN_GROUP)
        raw_input = process.stdin.buffer.raw
        _OWNER_INPUTS.add(raw_input)
    owner = _ProcessOwner(process)
    try:
        owner.held = owner.receive()["held"]
        yield owner
        owner.check()
    finally:
        with _OWNER_INPUTS_LOCK:
            _OWNER_INPUTS.discard(raw_input)
            _close_input(process)
        process.wait()
        process.stdout.close()


def _close_input(process) -> None:
    try:
        process.stdin.close()
    except OSError:
        pass


def _release_launcher(process):
    process.stdin.write(b"go\n")
    process.stdin.flush()


_OWNED_LAUNCH = """import json, os, sys
if sys.stdin.buffer.readline() != b'go\\n':
    raise SystemExit(1)
command, error_descriptor, merge = json.loads(sys.argv[1])
if error_descriptor is None:
    error_fd = os.dup(2)
elif os.name == 'nt':
    import msvcrt
    error_fd = msvcrt.open_osfhandle(error_descriptor, os.O_WRONLY)
else:
    error_fd = error_descriptor
os.set_inheritable(error_fd, False)
with os.fdopen(error_fd, 'w', encoding='utf-8') as errors:
    if merge:
        os.dup2(1, 2)
    try:
        if os.name != 'nt':
            with open(os.devnull, 'rb') as source:
                os.dup2(source.fileno(), 0)
            if isinstance(command, str):
                os.execl('/bin/sh', '/bin/sh', '-c', command)
            os.execvp(command[0], command)
        import subprocess
        code = subprocess.call(command, shell=isinstance(command, str), stdin=subprocess.DEVNULL)
    except OSError as error:
        json.dump([error.errno, error.strerror, error.filename,
                   getattr(error, 'winerror', None), error.filename2], errors)
        code = 1
raise SystemExit(code)
"""


def _spawn(command, out, errors, owner, kwargs, *, error_descriptor=None, merge=True) -> subprocess.Popen:
    # A Windows venv executable redirects into another process before Python
    # reaches the start gate. Use the base interpreter with startup hooks off,
    # so Job assignment precedes every child the launcher can create.
    launcher = getattr(sys, "_base_executable", sys.executable)
    payload = json.dumps([command, error_descriptor, merge])
    family, kwargs = _command_family(kwargs)
    process = subprocess.Popen([launcher, "-I", "-S", "-c", _OWNED_LAUNCH, payload],
                               stdin=subprocess.PIPE, stdout=out, stderr=errors,
                               **_OWN_GROUP, **kwargs)
    try:
        owner.start(process, family)
        return process
    except BaseException:
        _kill_tree(process)
        _close_input(process)
        raise


def _command_family(kwargs):
    if os.name == "nt":
        return None, kwargs
    from ._process_family import command_environment
    family, environment = command_environment(kwargs.get("env"))
    return family, {**kwargs, "env": environment}


def _stream_size(stream: IO | None) -> int:
    """Read the rotating log's monotonic byte count or an ordinary file's size."""
    if hasattr(stream, "progress_bytes"):
        return stream.progress_bytes
    try:
        return os.fstat(stream.fileno()).st_size
    except (AttributeError, OSError, ValueError):
        return -1


def _progress(stream: IO | None, size: int, since: float) -> tuple[int, float]:
    """The stream's size and the moment it last changed."""
    grown = _stream_size(stream)
    return (grown, time.monotonic()) if grown != size else (size, since)


def _expired(limit: float | None) -> bool:
    return limit is not None and time.monotonic() >= limit


def _wait_watching(proc: subprocess.Popen, timeout: float | None, no_progress: float,
                   stream: IO) -> int | None:
    """Wait in ticks so a command that stops writing can be caught between them.

    A suite that hangs at 0% CPU never trips a total deadline the user did not
    set, and `timeout_seconds` defaults to none at all, so the run sat on it
    forever with nothing watching the log. The log IS the signal: it grows while
    the runner reports and stops when the runner does.
    """
    limit = None if timeout is None else time.monotonic() + timeout
    size, since = _stream_size(stream), time.monotonic()
    while True:
        try:
            return _wait_command(proc, _TICK)
        except subprocess.TimeoutExpired:
            size, since = _progress(stream, size, since)
        if time.monotonic() - since >= no_progress:
            raise NoProgress(no_progress)
        if _expired(limit):
            return None


def run_bounded(command: str, timeout: float | None, *, stream: IO | None = None,
                no_progress: float | None = None, owner=None, **popen_kwargs) -> int | None:
    """The exit code, or None when the deadline expired and the tree was killed.
    A timeout of None is no deadline at all: the caller waits for the command.

    `no_progress` adds a second deadline on top of the first: the tree is killed
    and NoProgress raised when `stream` has not grown for that many seconds. It
    needs a stream whose size can be read, so it is ignored for DEVNULL and for
    a handle `os.fstat` refuses: there is nothing to measure in either, and an
    unchanging -1 would read as a stall on every command.

    Output goes to DEVNULL by default, never a pipe: nobody here reads it, and a
    pipe outlives the timeout - the drain has no deadline of its own, so the
    call would return when the grandchild holding the handles exits.

    `stream` takes stdout and stderr both. Use a real file or command_log's
    continuously drained pipe, whose progress counter survives rotation.
    Owned descendants stop before return, allowing the log reader to finish.
    An ordinary pipe without a reader can block the command and is unsupported.

    `owner` registers the command before launch. Its separate process keeps
    measurement locks until registered command trees stop after a caller crash.
    Without an owner, this call creates one for the command's lifetime. A root
    exit stops remaining descendants before returning, even without a deadline.
    """
    ownership = own_processes(()) if owner is None else nullcontext(owner)
    with ownership as held:
        return _run_owned(command, timeout, stream, no_progress, held, popen_kwargs)


def _run_owned(command, timeout, stream, no_progress, owner, popen_kwargs):
    out = subprocess.DEVNULL if stream is None else stream
    # A private file carries launch errors without an exit-code sentinel or a
    # pipe whose unread payload could block command completion.
    with tempfile.TemporaryFile() as errors:
        proc = _spawn(command, out, errors, owner, popen_kwargs)
        code = _complete_command(proc, timeout, stream, no_progress, owner)
        _raise_launch_error(errors)
        return code


def run_owned(command: str | list[str], timeout: float | None = None, *, owner=None,
              capture_output: bool = False, cwd=None, env=None) -> subprocess.CompletedProcess:
    """Run literal argv or a shell string until the command and descendants stop.

    Input is DEVNULL. Output is inherited unless capture_output requests separate
    UTF-8 stdout/stderr strings. Background descendants stop with their command;
    callers wanting a persistent service must launch that service separately.
    TimeoutExpired and CommandCancelled are raised only after tree cleanup.
    """
    ownership = own_processes(()) if owner is None else nullcontext(owner)
    with ownership as held, ExitStack() as stack:
        output = _capture_streams(stack, capture_output)
        errors = stack.enter_context(tempfile.TemporaryFile())
        code = _owned_status(command, timeout, held, output, errors, {"cwd": cwd, "env": env})
        return subprocess.CompletedProcess(command, code, *map(_captured_text, output))


def _capture_streams(stack, capture):
    if not capture:
        return None, None
    return tuple(stack.enter_context(tempfile.TemporaryFile()) for _ in range(2))


def _captured_text(stream):
    if stream is None:
        return None
    stream.seek(0)
    return stream.read().decode("utf-8").replace("\r\n", "\n")


def _owned_status(command, timeout, owner, output, errors, kwargs):
    with _error_transport(errors) as (descriptor, transport):
        proc = _spawn(command, *output, owner, {**kwargs, **transport},
                      error_descriptor=descriptor, merge=False)
    code = _complete_command(proc, timeout, None, None, owner)
    _raise_launch_error(errors)
    if code is None:
        raise subprocess.TimeoutExpired(command, timeout)
    return code


@contextmanager
def _error_transport(stream):
    if os.name != "nt":
        yield stream.fileno(), {"pass_fds": (stream.fileno(),)}
        return
    import msvcrt
    descriptor = os.dup(stream.fileno())
    try:
        os.set_inheritable(descriptor, True)
        handle = msvcrt.get_osfhandle(descriptor)
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [handle]}
        yield handle, {"startupinfo": startup}
    finally:
        os.close(descriptor)


def _raise_launch_error(errors):
    from .errors import ToolError
    errors.seek(0)
    payload = errors.read()
    if not payload:
        return
    try:
        arguments = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as error:
        raise ToolError("command launcher failed: " + payload.decode("utf-8", "replace")) from error
    raise OSError(*arguments)


def _complete_command(proc, timeout, stream, no_progress, owner):
    try:
        return _wait_bounded(proc, timeout, no_progress, stream)
    finally:
        _close_input(proc)
        try:
            owner.request("remove", proc.pid)
        except BaseException:
            _kill_tree(proc)
            raise
        proc.wait()
        owner.check_cancelled()


def _wait_bounded(proc, timeout, no_progress, stream) -> int | None:
    if no_progress and stream is not None and _stream_size(stream) != -1:
        return _wait_watching(proc, timeout, no_progress, stream)
    try:
        return _wait_command(proc, timeout)
    except subprocess.TimeoutExpired:
        return None


def _exit_status(status) -> int:
    return status.si_status if status.si_code == os.CLD_EXITED else -status.si_status


def _wait_command(proc, timeout):
    """Leave a POSIX group leader unreaped until group cleanup confirms exit."""
    if os.name == "nt":
        return proc.wait(timeout=timeout)
    options = os.WEXITED | os.WNOWAIT
    if timeout is None:
        return _exit_status(os.waitid(os.P_PID, proc.pid, options))
    deadline = time.monotonic() + timeout
    while True:
        status = os.waitid(os.P_PID, proc.pid, options | os.WNOHANG)
        if status is not None:
            return _exit_status(status)
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        time.sleep(min(.01, max(0, deadline - time.monotonic())))
