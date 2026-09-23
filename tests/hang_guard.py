"""Test-side waits on a child process: one bound against a hang, and a report
when a wait misses.

Every wait here returns the moment its state appears, so the bound costs a
passing test nothing. It only decides how long a hung child takes to fail. That
is why the suite has one generous number instead of a guess per call site: under
a saturated machine a child needed 9 s where a test allowed 5 s, and 14 s against
a 10 s wait, and each of those guesses failed a verify run on a correct tree.

A miss has two causes: the child exited before the state appeared, or the bound
ran out. Either way the child is killed if it still runs, and the failure carries
what it printed, from its pipes or from the log file it writes to.

Child scripts written from a template cannot import this module. Importing it
puts the bound in the environment under HANG_ENV, and a template spells
CHILD_WAIT where it waits for a state. A child that holds a lock until the test
releases it spells CHILD_HOLD, twice the bound: a hold that ran out first would
release the lock and pass a test whose product never stopped the child.

Product deadlines under test keep their own numbers: the 0 to 3 s timeouts a
test expects to expire, a mutation or lane timeout the test is about, a
measurement window and a latency budget. They measure the product, not a hang.
"""
import os
import queue
import subprocess
import threading
import time

HANG_SECONDS = 120
HOLD_SECONDS = 2 * HANG_SECONDS
HANG_ENV = "CRAPKIT_TEST_HANG_SECONDS"
os.environ[HANG_ENV] = str(HANG_SECONDS)

# What a child script spells for the bound and for a hold. The child imports os.
CHILD_WAIT = f'float(os.environ["{HANG_ENV}"])'
CHILD_HOLD = f"2 * {CHILD_WAIT}"

# Fixed at import, so a test that forces a miss by zeroing HANG_SECONDS still
# reads the killed child's output.
_REPORT_SECONDS = HANG_SECONDS


def wait_until(condition, process=None, *, log=None, what="the awaited state"):
    """Return once condition() holds. A child that exits first, or the bound
    running out, is a miss."""
    until = time.monotonic() + HANG_SECONDS
    while _counting(process, until):
        if condition():
            return
        time.sleep(.01)
    if not condition():
        raise AssertionError(_miss(what, process, log, HANG_SECONDS))


def wait_for(path, process=None, *, log=None):
    """Return once `path` exists."""
    wait_until(path.exists, process, log=log, what=str(path))


def exited(process, *, log=None):
    """The child's exit code, waited for under the bound."""
    try:
        return process.wait(timeout=HANG_SECONDS)
    except subprocess.TimeoutExpired:
        raise AssertionError(_miss("the child exit", process, log, HANG_SECONDS)) from None


def communicate(process, input=None):
    """`process.communicate` under the bound."""
    return _drain(process, input, HANG_SECONDS, "the child exit")


def run(argv, *, input=None, timeout=None, **popen):
    """`subprocess.run` with both streams captured, under the bound unless the
    caller names its own."""
    seconds = HANG_SECONDS if timeout is None else timeout
    stdin = None if input is None else subprocess.PIPE
    process = subprocess.Popen(argv, stdin=stdin, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, **popen)
    out, err = _drain(process, input, seconds, f"{argv!r} finish")
    return subprocess.CompletedProcess(argv, process.returncode, out, err)


def next_line(process, *, log=None):
    """The child's next stdout line, read under the bound."""
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        return lines.get(timeout=HANG_SECONDS)
    except queue.Empty:
        raise AssertionError(_miss("a line on the child's stdout", process, log,
                                   HANG_SECONDS)) from None


def _counting(process, until):
    return (process is None or process.poll() is None) and time.monotonic() < until


def _drain(process, input, seconds, what):
    try:
        return process.communicate(input, timeout=seconds)
    except subprocess.TimeoutExpired:
        raise AssertionError(_miss(what, process, None, seconds)) from None


def _miss(what, process, log, seconds):
    ending = _stop(process, seconds)
    printed = _printed(process, log) or "(nothing)"
    return f"never saw {what} {ending}\n--- the child printed ---\n{printed}"


def _stop(process, seconds):
    """Kill a child that still runs, and say how the wait ended."""
    if process is None:
        return f"within {seconds} s"
    code = process.poll()
    if code is not None:
        return f"before the child exited with code {code}"
    process.kill()
    return f"within {seconds} s, so the child was killed"


def _printed(process, log):
    if log is not None:
        return log.read_text(encoding="utf-8", errors="replace")
    if process is None or not _captured(process):
        return "(nothing captured)"
    return _collected(process)


def _captured(process):
    return process.stdout is not None or process.stderr is not None


def _collected(process):
    try:
        out, err = process.communicate(timeout=_REPORT_SECONDS)
    except subprocess.TimeoutExpired:
        return "(unreadable: a descendant still holds the child's pipes)"
    return _text(out) + _text(err)


def _text(stream):
    if isinstance(stream, bytes):
        return stream.decode("utf-8", "replace")
    return stream or ""
