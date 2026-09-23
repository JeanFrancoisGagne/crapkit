"""Shell commands whose completion includes cleanup of their descendants.

An owner registers each command before it runs any code. Windows starts the
command suspended and resumes it once its Job holds it; POSIX holds a launcher
at a start gate that execs the command after registration. Windows Jobs and
POSIX process groups retain ownership after the shell exits.
Completion, timeout and caller death stop the owned group before its resources
are released. POSIX commands must keep their inherited group; an explicit
setsid daemon is outside that ownership. Untimed commands have no deadline.
"""
from __future__ import annotations

import os
from contextlib import ExitStack, contextmanager, nullcontext, suppress
import json
import re
import shlex
import subprocess
import sys
import tempfile
import time
from typing import IO

from ._process_owner import CommandCancelled, kill_process_tree, own_processes
from .errors import ToolError

__all__ = ["CommandCancelled", "NoProgress", "own_processes", "prepare_template",
           "run_bounded", "run_owned"]

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
    kill_process_tree(proc.pid)
    proc.wait()


def _close_input(process) -> None:
    if process.stdin is not None:
        with suppress(OSError):
            process.stdin.close()


# How long a launcher whose input pipe is already dead gets to settle its exit
# code before the message has to say it is still running.
_LAUNCHER_SETTLE = 2.0

# The code a Windows process exits with when a DLL's initialisation fails,
# 0xC0000142. Seen at spawn on a loaded machine, before the process ran any
# code of its own.
_DLL_INIT_FAILED = 3221225794

# CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED, spelled out so the Windows start
# can be driven on any platform.
_SUSPENDED_GROUP = 0x200 | 0x4


class _StartFailed(ToolError, OSError):
    """A start failed, so the exit code is no answer to read: a launcher died
    at its start gate, an owner refused the registration, or the command exited
    0xC0000142 because a process in it failed to start. A ToolError to the lane
    layer, which fails and retries that one lane. Still an OSError to the doctor
    and init probes, which read an OSError from run_bounded as a question that
    could not be put and answer with a finding."""


def _run(command, streams, owner, kwargs, watch):
    """Start one owned command, wait for it and its cleanup, and return its code.

    `streams` are the command's stdout and stderr. `watch` is the deadline, the
    progress stream and the no-progress limit. None means the deadline passed.
    """
    with _START(command, *streams, owner, kwargs) as process:
        code = _complete_command(process, *watch, owner)
    _refuse_failed_start(code)
    return code


@contextmanager
def _suspended(command, stdout, stderr, owner, kwargs):
    """Windows: the command starts suspended and runs no code, so it creates no
    child, until its owner holds it in a Job and resumes it."""
    registration, kwargs = owner.prepare(kwargs)
    process = subprocess.Popen(command, shell=isinstance(command, str), stdin=subprocess.DEVNULL,
                               stdout=stdout, stderr=stderr, creationflags=_SUSPENDED_GROUP,
                               **kwargs)
    _hand_over(process, owner, registration, (_resume, _unowned, _terminate))
    yield process


def _terminate(process):
    """TerminateProcess through the handle Popen holds, with no taskkill to spawn
    on a box that is already failing process starts. A command that never
    resumed has no children; one that did was registered, and the owner's stop
    ended its Job first."""
    process.kill()


def _resume(process):
    import ctypes
    _check_resumed(ctypes.WinDLL("ntdll").NtResumeProcess(ctypes.c_void_p(int(process._handle))))


def _check_resumed(status):
    if status:
        raise OSError(f"NtResumeProcess refused the command: status 0x{status & 0xFFFFFFFF:08X}")


def _unowned(process, error: OSError):
    return _StartFailed(f"command could not start under its owner ({error}), so it never ran")


def _refuse_failed_start(code):
    """A process whose DLL initialisation failed exits 0xC0000142 before its own
    code runs. A shell exits with its last process's code, so work before that
    process may have run. The lane layer retries the command instead of reading
    a result."""
    if code == _DLL_INIT_FAILED:
        raise _StartFailed(f"command exited with code {code} (0xC0000142, "
                           "STATUS_DLL_INIT_FAILED): a process in it failed to start, "
                           "so the code is not the command's answer")


@contextmanager
def _launched(command, stdout, stderr, owner, kwargs):
    """POSIX: a launcher waits at its start gate until the owner registers it,
    then execs the command. A private file carries an exec OSError back here,
    so no exit code needs a sentinel and no unread pipe can block completion."""
    with tempfile.TemporaryFile() as errors:
        launcher_stderr, descriptor, merge, passed = _launch_options(errors, stderr)
        registration, kwargs = owner.prepare({**kwargs, **passed})
        # The base interpreter with startup hooks off starts nothing before its gate.
        process = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-I", "-S",
                                    "-c", _OWNED_LAUNCH, json.dumps([command, descriptor, merge])],
                                   stdin=subprocess.PIPE, stdout=stdout, stderr=launcher_stderr,
                                   start_new_session=True, **kwargs)
        _hand_over(process, owner, registration, (_release_launcher, _dead_launcher, _kill_group))
        yield process
        _raise_launch_error(errors)


def _kill_group(process):
    """A launcher's whole process group: it may have exec'd the command already."""
    kill_process_tree(process.pid)


def _launch_options(errors, stderr):
    """The launcher's stderr, the payload's error descriptor and merge flag, and
    the descriptor to pass. A merged command's launcher reports through its own
    stderr, the error file, and joins the command's stderr to stdout itself."""
    if stderr == subprocess.STDOUT:
        return errors, None, True, {}
    return stderr, errors.fileno(), False, {"pass_fds": (errors.fileno(),)}


_START = _suspended if os.name == "nt" else _launched


def _hand_over(process, owner, registration, start):
    """Register the gated process, then let it run; a failed hand-over kills it.

    `start` is how this start gate releases the process, the error a refused
    registration raises, and how the process dies when the hand-over fails.
    """
    release, refusal, kill = start
    try:
        _register(process, owner, registration, release, refusal)
    except BaseException:
        _abandon(process, owner, kill)
        raise


def _abandon(process, owner, kill):
    """Forget any registration the owner took, then kill and reap the process.
    The add may have succeeded before the release failed; an owner that cannot
    answer any more must not hide the start failure."""
    with suppress(ToolError, OSError):
        owner.stop(process.pid)
    kill(process)
    process.wait()
    _close_input(process)


def _register(process, owner, registration, release, refusal):
    try:
        owner.register_then(process.pid, lambda: release(process), registration)
    except OSError as error:
        raise refusal(process, error) from error


def _release_launcher(process):
    process.stdin.write(b"go\n")
    process.stdin.flush()


def _dead_launcher(process, error: OSError):
    """The launcher exited before its start gate: the start line hit EPIPE, or
    registration failed at the OS level. Either is one lane's failure, which
    the lane layer retries, not a crash of the whole run.
    """
    try:
        code = process.wait(timeout=_LAUNCHER_SETTLE)
    except subprocess.TimeoutExpired:
        code = None
    return _StartFailed(f"command launcher {_launcher_fate(code, error)}, so the command never ran")


def _launcher_fate(code, error: OSError) -> str:
    if code is None:
        return (f"was still running {_LAUNCHER_SETTLE:g}s after its start gate failed "
                f"({error})")
    return f"exited with code {code} before its start gate"


_OWNED_LAUNCH = """import json, os, sys
if sys.stdin.buffer.readline() != b'go\\n':
    raise SystemExit(1)
command, error_descriptor, merge = json.loads(sys.argv[1])
if error_descriptor is None:
    error_fd = os.dup(2)
else:
    error_fd = error_descriptor
os.set_inheritable(error_fd, False)
with os.fdopen(error_fd, 'w', encoding='utf-8') as errors:
    if merge:
        os.dup2(1, 2)
    try:
        with open(os.devnull, 'rb') as source:
            os.dup2(source.fileno(), 0)
        if isinstance(command, str):
            os.execl('/bin/sh', '/bin/sh', '-c', command)
        os.execvp(command[0], command)
    except OSError as error:
        json.dump([error.errno, error.strerror, error.filename, None, error.filename2], errors)
raise SystemExit(1)
"""


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
    out = subprocess.DEVNULL if stream is None else stream
    with ownership as held:
        return _run(command, (out, subprocess.STDOUT), held, popen_kwargs,
                    (timeout, stream, no_progress))


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
        code = _run(command, output, held, {"cwd": cwd, "env": env}, (timeout, None, None))
        if code is None:
            raise subprocess.TimeoutExpired(command, timeout)
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


def _raise_launch_error(errors):
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
            owner.stop(proc.pid)
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
