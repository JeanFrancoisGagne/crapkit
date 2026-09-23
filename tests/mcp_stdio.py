"""A real MCP client keeps stdin open until each requested reply arrives.

A reply that never comes, and a server that exits before replying, are misses
like any other wait on a child: the server is killed if it still runs, and the
failure carries its stderr in the text tests/hang_guard.py writes.
"""
import functools
import json
import locale
import queue
import subprocess
import tempfile
import threading
import time

import hang_guard

REPLY = 'a reply from the MCP server'


class _Missed(Exception):
    """A wait on the server ended without the state it waited for."""


def _read(stream, replies):
    """Queue each stdout line, then None at EOF. The stream closes here, once
    nothing more can arrive on it."""
    with stream:
        for line in stream:
            replies.put(line)
    replies.put(None)


def _expects_reply(line):
    try:
        message = json.loads(line)
    except ValueError:
        return False
    return isinstance(message, dict) and 'id' in message


def _left(deadline):
    return max(.01, deadline - time.monotonic())


def _exit_code(process, deadline):
    try:
        return process.wait(timeout=_left(deadline))
    except subprocess.TimeoutExpired:
        raise _Missed('the MCP server exit') from None


def _reply(process, replies, deadline):
    """The next reply. A closed stdout means the server is exiting, and its exit
    is awaited so the miss can say how it ended."""
    try:
        response = replies.get(timeout=_left(deadline))
    except queue.Empty:
        raise _Missed(REPLY) from None
    if response is None:
        _exit_code(process, deadline)
        raise _Missed(REPLY)
    return response


def _exchange(process, frames, replies, deadline):
    output = []
    for line in frames.splitlines():
        process.stdin.write(line + '\n')
        process.stdin.flush()
        if _expects_reply(line):
            output.append(_reply(process, replies, deadline))
    return ''.join(output)


def _printed(stream, encoding, errors):
    stream.seek(0)
    return stream.read().decode(encoding or locale.getencoding(), errors or 'strict')


def _close(process):
    """Kill a server the exchange left running. A killed server always exits."""
    if process.poll() is None:
        process.kill()
    process.wait()
    process.stdin.close()


def run(argv, *, cwd, frames, env, timeout=None, encoding=None, errors=None):
    """Feed `frames` to the server one line at a time, waiting for each reply,
    all under the hang bound unless `timeout` names another."""
    seconds = hang_guard.HANG_SECONDS if timeout is None else timeout
    deadline = time.monotonic() + seconds
    with tempfile.TemporaryFile() as error_stream:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=error_stream,
                                   text=True, encoding=encoding, errors=errors)
        replies = queue.Queue()
        threading.Thread(target=_read, args=(process.stdout, replies), daemon=True).start()
        try:
            output = _exchange(process, frames, replies, deadline)
            process.stdin.close()
            code = _exit_code(process, deadline)
            return subprocess.CompletedProcess(argv, code, output,
                                               _printed(error_stream, encoding, errors))
        except _Missed as missed:
            stderr = functools.partial(_printed, error_stream, encoding, 'replace')
            raise AssertionError(hang_guard.report(str(missed), process, seconds, stderr)) from None
        finally:
            _close(process)
