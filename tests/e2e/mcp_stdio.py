"""A real MCP client keeps stdin open until each requested reply arrives."""
import json
import locale
import queue
import subprocess
import tempfile
import threading
import time


def _read(stream, replies):
    for line in stream:
        replies.put(line)
    replies.put(None)


def _expects_reply(line):
    try:
        message = json.loads(line)
    except ValueError:
        return False
    return isinstance(message, dict) and 'id' in message


def _exchange(process, frames, replies, deadline):
    output = []
    for line in frames.splitlines():
        process.stdin.write(line + '\n')
        process.stdin.flush()
        if _expects_reply(line):
            response = replies.get(timeout=max(.01, deadline - time.monotonic()))
            assert response is not None, 'MCP closed stdout before replying'
            output.append(response)
    return ''.join(output)


def run(argv, *, cwd, frames, env, timeout, encoding, errors):
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as error_stream:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=error_stream,
                                   text=True, encoding=encoding, errors=errors)
        replies = queue.Queue()
        reader = threading.Thread(target=_read, args=(process.stdout, replies), daemon=True)
        reader.start()
        try:
            output = _exchange(process, frames, replies, deadline)
            process.stdin.close()
            code = process.wait(timeout=max(.01, deadline - time.monotonic()))
            reader.join(2)
            error_stream.seek(0)
            error = error_stream.read().decode(encoding or locale.getencoding(), errors or 'strict')
            return subprocess.CompletedProcess(argv, code, output, error)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            process.stdin.close()
            reader.join(2)
            process.stdout.close()
