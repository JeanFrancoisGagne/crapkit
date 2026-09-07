"""Shell commands whose completion includes cleanup of their descendants.

A separate owner registers each gated launcher before the command starts.
Windows Jobs and POSIX process groups retain ownership after the shell exits.
Completion, timeout and caller death stop the owned group before its resources
are released. POSIX commands must keep their inherited group; an explicit
setsid daemon is outside that ownership. Untimed commands have no deadline.
"""
from __future__ import annotations

import os
from contextlib import contextmanager, nullcontext
import json
import re
import signal
import shlex
import subprocess
import sys
import threading
import time
from typing import IO

_OWN_GROUP = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
              if os.name == "nt" else {"start_new_session": True})

# How often the progress watch looks at the stream. Small enough that the kill
# lands close to the deadline, large enough that watching a two-hour suite costs
# nothing measurable.
_TICK = 0.5


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


class _ProcessOwner:
    def __init__(self, process):
        self.process = process
        self._requests = threading.Lock()

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
        from .errors import ToolError
        try:
            with self._requests:
                self.process.stdin.write(json.dumps((operation, pid)) + "\n")
                self.process.stdin.flush()
                self.receive()
        except OSError as error:
            raise ToolError("measurement owner stopped during command registration") from error

    def check(self) -> None:
        from .errors import ToolError
        if self.process.poll() is not None:
            raise ToolError("measurement owner stopped before publication")


@contextmanager
def own_processes(paths, *, optional: bool = False, label: str = "measurement"):
    """Own measurement outputs across caller crashes and command cleanup."""
    options = {"paths": list(map(str, paths)), "optional": optional, "label": label}
    process = subprocess.Popen([sys.executable, "-m", "crapkit._process_owner", json.dumps(options)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    owner = _ProcessOwner(process)
    try:
        owner.held = owner.receive()["held"]
        yield owner
        owner.check()
    finally:
        _close_input(process)
        process.wait()
        process.stdout.close()


def _close_input(process) -> None:
    try:
        process.stdin.close()
    except OSError:
        pass


_OWNED_LAUNCH = """import os, sys
if sys.stdin.buffer.readline() != b'go\\n':
    raise SystemExit(1)
if os.name != 'nt':
    with open(os.devnull, 'rb') as source:
        os.dup2(source.fileno(), 0)
    os.execl('/bin/sh', '/bin/sh', '-c', sys.argv[1])
import subprocess
raise SystemExit(subprocess.call(sys.argv[1], shell=True, stdin=subprocess.DEVNULL))
"""


def _spawn(command: str, out, owner, kwargs) -> subprocess.Popen:
    # A Windows venv executable redirects into another process before Python
    # reaches the start gate. Use the base interpreter with startup hooks off,
    # so Job assignment precedes every child the launcher can create.
    launcher = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen([launcher, "-I", "-S", "-c", _OWNED_LAUNCH, command],
                               stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT,
                               **_OWN_GROUP, **kwargs)
    try:
        owner.request("add", process.pid)
        process.stdin.write(b"go\n")
        process.stdin.flush()
        return process
    except BaseException:
        _kill_tree(process)
        _close_input(process)
        raise


def _stream_size(stream: IO | None) -> int:
    """Bytes the command has written so far, or -1 when nothing can be measured.
    The child writes to the file behind this handle, so its size is the only
    progress signal available without a pipe to read."""
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

    `stream` takes stdout and stderr both, and must be a real file (the lane log
    is one). A file needs no reader, so the kill lands on the deadline the same
    way; a pipe would reintroduce the drain and is the one thing not to pass.

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
    proc = _spawn(command, out, owner, popen_kwargs)
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
