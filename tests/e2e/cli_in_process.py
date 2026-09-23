"""One CLI call run inside the pytest worker, reported as a child would report it.

A spawned `python -m crapkit` pays for an interpreter start, crapkit's imports
and coverage's startup hook before the command runs: measured at 54 ms for
`--version` on a quiet machine, 148 ms under the py lane's coverage patch, and
the suite makes about 1,750 such calls. `crapkit.cli.main(argv)` is the entry
point `python -m crapkit` reaches, so the worker can call it directly, provided
it rebuilds what the child had and puts the worker back afterwards:

- cwd and os.environ are the child's for the call, and the worker's after it.
- File descriptors 1 and 2 point at a temp file each for the call, so output
  from crapkit and from any command it starts with inherited stdio (a
  test-scoped runner, a lane) lands where the child's pipes would have caught
  it. sys.stdout and sys.stderr are new text streams on those descriptors,
  opened the way Python opens a child's.
- sys.stdin holds the bytes the child would have read, sys.argv what
  `python -m crapkit` sets (crapkit spells itself in messages from argv[0]),
  and warnings print to stderr under a new interpreter's filters.
- crapkit's functools caches are empty on entry and on exit: a probe cached for
  one PATH must not answer the next test, which may have another.
- The call's garbage is collected on the way out, which closes the store the
  way a process exit does.
- crapkit's analysis pool refuses with a RuntimeError. On Linux the pool forks
  the worker, and a repo past 32 cold files asks for it there; Windows asks
  only for a bigger repo, so the refusal holds on every OS.
- The status is main's return or SystemExit's code. An uncaught exception is
  exit 1 with its traceback on stderr, which is how the interpreter reports one.
- The bytes are decoded as subprocess.run decodes a child's: the caller's
  encoding or the locale's, the caller's errors or strict, universal newlines.

What it cannot rebuild is the process boundary itself: the console entry point,
the child's own stdio encoding, exit codes as the OS reports them, a killed
process. Files that test those keep `spawn=True` (AGENTS.md says which).

Two calls cannot run at once, since each one owns the process for its
duration; a second call while one is running raises instead of corrupting both.

A call past its bound fails its own test, as the spawn path's kill-on-timeout
does. A watch thread raises _PastBound in the call's thread, which arrives at
its next Python instruction; a BaseException, so crapkit's `except Exception`
handlers let it through. Every context above puts the worker back on the way
out, and the test fails with an AssertionError naming argv, the bound and what
the call printed, and the session goes on. A thread stuck in C code never
reaches another Python instruction, so GRACE_SECONDS later faulthandler writes
every thread's stack to the file `log_hangs_to` named, and the worker exits.
The suite names a file under the worker's basetemp, because pytest's capture
owns descriptor 2 during a test and a dump sent there dies with the worker.
"""
from __future__ import annotations

import ctypes
import faulthandler
import gc
import io
import locale
import os
import subprocess
import sys
import tempfile
import threading
import traceback
import warnings
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

_DEFAULT_FILTERS = (("ignore", DeprecationWarning), ("ignore", PendingDeprecationWarning),
                    ("ignore", ImportWarning), ("ignore", ResourceWarning))

_ONE_CALL = threading.Lock()

GRACE_SECONDS = 30
_HANG_LOG = sys.__stderr__


def log_hangs_to(file) -> None:
    """Write the stack of a call stuck past its bound to `file`, an open text
    file with a descriptor, which the caller keeps open while calls run."""
    global _HANG_LOG
    _HANG_LOG = file


class _PastBound(BaseException):
    """What the call's thread raises once its bound runs out."""


def fits(args, spawn: bool) -> bool:
    """Whether a call can run here: not when its file asked for a child, and
    not `mcp`, whose server reads a real stdin descriptor."""
    return not spawn and tuple(args[:1]) != ("mcp",)


def run(repo: Path, args, *, env: dict, stdin: str | None = None,
        encoding: str | None = None, errors: str | None = None,
        timeout: float | None = None) -> subprocess.CompletedProcess:
    """`python -m crapkit <args>` in `repo` under `env`, run in this process."""
    argv = [sys.executable, "-m", "crapkit", *args]
    with _only_call(), tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            with ExitStack() as stack:
                _enter_child(stack, repo, env, (out, err))
                stack.enter_context(_child_stdin(stdin, encoding, errors))
                stack.enter_context(_swapped(sys, "argv", [_main_path(), *args]))
                code = _watched(argv, timeout)
        except _PastBound:
            raise AssertionError(_miss(argv, timeout, (out, err), encoding)) from None
        return subprocess.CompletedProcess(argv, code, _decoded(out, encoding, errors),
                                           _decoded(err, encoding, errors))


def _miss(argv: list[str], timeout: float, files, encoding: str | None) -> str:
    """hang_guard's report of a missed wait, for a call stopped in place."""
    printed = "".join(_decoded(file, encoding, "replace") for file in files)
    return (f"never saw {argv!r} finish within {timeout} s, so the call was stopped\n"
            f"--- the call printed ---\n{printed or '(nothing)'}")


@contextmanager
def _only_call() -> Iterator[None]:
    """cwd, os.environ and descriptors 1 and 2 belong to the whole process, so
    two calls at once would each run inside the other's. The second refuses."""
    if not _ONE_CALL.acquire(blocking=False):
        raise RuntimeError("in-process CLI calls cannot overlap: a file that runs "
                           "them at once binds cli_runner(spawn=True)")
    try:
        yield
    finally:
        _ONE_CALL.release()


def _enter_child(stack: ExitStack, repo: Path, env: dict, files) -> None:
    stack.enter_context(_collected())
    stack.enter_context(_cold_crapkit_caches())
    stack.enter_context(_working_directory(repo))
    stack.enter_context(_environment(env))
    stack.enter_context(_swapped(_analysis_pool(), "analysis_pool", _refuse_pool))
    for fd, file in zip((1, 2), files):
        stack.enter_context(_descriptor(fd, file))
    stack.enter_context(_child_streams())
    stack.enter_context(_child_warnings())


@contextmanager
def _collected() -> Iterator[None]:
    """Exit releases every handle a process holds; here the call's garbage
    cycles are collected instead. One of them holds a file open: sqlite3's
    statement cache wraps its own connection, so reference counting never
    closes crapkit's store, and Windows refuses to rename or delete a repo whose
    store is still open. Freezing what the worker held before the call keeps
    that collection to what the call made: a worker holds about 100,000
    objects, and scanning them all took most of an in-process call's cost."""
    gc.freeze()
    try:
        yield
    finally:
        gc.collect()
        gc.unfreeze()


def _analysis_pool():
    import crapkit._analysis_pool
    return crapkit._analysis_pool


def _refuse_pool(**_):
    """The analysis pool forks its caller under Linux's start method, which
    here would fork the pytest worker. Windows asks for the pool only for a
    bigger repo, so the same call would pass there and fork the worker on
    Linux CI; refusing on every OS fails it on the machine it was written on."""
    raise RuntimeError("this call reached the analysis pool; its file binds "
                       "cli_runner(spawn=True)")


def _main_path() -> str:
    import crapkit
    return str(Path(crapkit.__file__).with_name("__main__.py"))


def _exit_status(args: list[str]) -> int:
    from crapkit.cli import main
    try:
        return _status(main(args))
    except SystemExit as exc:
        return _status(exc.code)
    except Exception:  # noqa: BLE001 - the interpreter's own report of an uncaught error
        traceback.print_exc()
        return 1


def _status(code) -> int:
    """sys.exit's reading of `code`: None is 0, an int is itself, anything else
    is printed to stderr and exits 1."""
    if code is None or isinstance(code, int):
        return code or 0
    print(code, file=sys.stderr)
    return 1


def _decoded(file, encoding: str | None, errors: str | None) -> str:
    file.seek(0)
    text = file.read().decode(encoding or locale.getpreferredencoding(False), errors or "strict")
    return text.replace("\r\n", "\n").replace("\r", "\n")


@contextmanager
def _swapped(owner, name: str, value) -> Iterator[None]:
    saved = getattr(owner, name)
    setattr(owner, name, value)
    try:
        yield
    finally:
        setattr(owner, name, saved)


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    saved = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(saved)


def _replace_environ(target: dict) -> None:
    for key in set(os.environ) - set(target):
        del os.environ[key]
    os.environ.update(target)


@contextmanager
def _environment(env: dict) -> Iterator[None]:
    saved = dict(os.environ)
    _replace_environ(env)
    try:
        yield
    finally:
        _replace_environ(saved)


@contextmanager
def _descriptor(fd: int, file) -> Iterator[None]:
    """Point `fd` at `file` for the call. On Windows the C runtime moves the
    process's standard handle with it, which is what a child started with
    inherited stdio receives."""
    saved = os.dup(fd)
    os.dup2(file.fileno(), fd)
    try:
        yield
    finally:
        os.dup2(saved, fd)
        os.close(saved)


@contextmanager
def _child_streams() -> Iterator[None]:
    """sys.stdout and sys.stderr as Python opens them for a child on a pipe:
    locale encoding, stdout block-buffered, stderr line-buffered with
    backslashreplace. crapkit reconfigures both to UTF-8 as it starts."""
    streams = (open(1, "w", closefd=False),
               open(2, "w", buffering=1, closefd=False, errors="backslashreplace"))
    with _swapped(sys, "stdout", streams[0]), _swapped(sys, "stderr", streams[1]):
        try:
            yield
        finally:
            for stream in streams:
                stream.close()


def _stdin_bytes(stdin: str | None, encoding: str | None, errors: str | None) -> bytes:
    """What subprocess.run writes to a child's stdin: newlines as the platform
    writes them, in the caller's encoding or the locale's."""
    if stdin is None:
        return b""
    text = stdin.replace("\n", os.linesep)
    return text.encode(encoding or locale.getpreferredencoding(False), errors or "strict")


@contextmanager
def _child_stdin(stdin: str | None, encoding: str | None, errors: str | None) -> Iterator[None]:
    stream = io.TextIOWrapper(io.BytesIO(_stdin_bytes(stdin, encoding, errors)))
    with _swapped(sys, "stdin", stream):
        yield


def _show_on_stderr(message, category, filename, lineno, file=None, line=None) -> None:
    sys.stderr.write(warnings.formatwarning(message, category, filename, lineno, line))


@contextmanager
def _child_warnings() -> Iterator[None]:
    """A new interpreter's warning filters, printing to the call's stderr in
    place of pytest's recorder."""
    with warnings.catch_warnings():
        warnings.resetwarnings()
        for action, category in _DEFAULT_FILTERS:
            warnings.simplefilter(action, category)
        warnings.showwarning = _show_on_stderr
        yield


def _crapkit_modules() -> list:
    return [module for name, module in list(sys.modules.items())
            if name.partition(".")[0] == "crapkit"]


def _crapkit_caches() -> Iterator:
    for module in _crapkit_modules():
        yield from (value for value in vars(module).values() if hasattr(value, "cache_clear"))


def _clear_crapkit_caches() -> None:
    for cached in _crapkit_caches():
        cached.cache_clear()


@contextmanager
def _cold_crapkit_caches() -> Iterator[None]:
    _clear_crapkit_caches()
    try:
        yield
    finally:
        _clear_crapkit_caches()


def _watched(argv: list[str], timeout: float | None) -> int:
    """The call's status, under its bound when it has one.

    _PastBound can arrive at any instruction until the watch stops, the first
    call to stop() included. It arrives once, so a second stop() completes."""
    if timeout is None:
        return _exit_status(argv[3:])
    watch = _Watch(argv, timeout)
    try:
        watch.start()
        return _exit_status(argv[3:])
    finally:
        try:
            watch.stop()
        except _PastBound:
            watch.stop()
            raise


def _set_async(thread_id: int, exception) -> None:
    """Make `thread_id` raise `exception` at its next Python instruction, or
    with None, drop one it has not raised yet."""
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), exception)


class _Watch:
    """One call's bound, kept by a thread of its own. The lock orders firing
    against stopping: once stop() holds it, nothing new can fire, and an
    exception fired but not yet raised is dropped, since the call returned."""

    def __init__(self, argv: list[str], timeout: float):
        self._call = threading.get_ident()
        self._argv, self._timeout = argv, timeout
        self._test = os.environ.get("PYTEST_CURRENT_TEST", "")
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._fired = False
        self._done = False
        self._thread = threading.Thread(target=self._keep, name="in-process CLI bound",
                                        daemon=True)

    def start(self) -> None:
        faulthandler.dump_traceback_later(self._timeout + GRACE_SECONDS, exit=True,
                                          file=_HANG_LOG)
        self._thread.start()

    def _keep(self) -> None:
        if not self._stopped.wait(self._timeout):
            self._fire()

    def _fire(self) -> None:
        with self._lock:
            if self._done:
                return
            self._fired = True
            _set_async(self._call, ctypes.py_object(_PastBound))
        print(f"{self._test}: {self._argv!r} past its {self._timeout} s bound. Stopping it; "
              f"if it has not returned {GRACE_SECONDS} s from now, every thread's stack "
              f"follows and the worker exits.", file=_HANG_LOG, flush=True)

    def stop(self) -> None:
        with self._lock:
            self._done = True
            if self._fired:
                _set_async(self._call, None)
        faulthandler.cancel_dump_traceback_later()
        self._stopped.set()
        self._thread.join()
