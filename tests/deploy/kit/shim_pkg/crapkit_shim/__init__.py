"""A `crapkit` that records how it was started, then runs the real one.

The record is one JSON line per event, appended to the log path baked into
_config.py: `start` (argv, cwd, the whole environment, pid, ppid), `first_line`
(the first line the client wrote to stdin, up to 64 KiB) and `exit` (the real
launcher's exit code). Stdin is forwarded line by line; stdout and stderr are
the real launcher's own, inherited, so the client talks to crapkit directly.
"""
import importlib
import json
import os
import subprocess
import sys
import threading

LIMIT = 1 << 16


def _load_config():
    """The paths shim.py wrote into crapkit_shim/_config.py at build time."""
    return importlib.import_module("crapkit_shim._config")


def record(event: str, **fields) -> None:
    line = json.dumps({"event": event, "pid": os.getpid(), **fields}, default=str) + "\n"
    with open(_load_config().LOG, "a", encoding="utf-8") as log:
        log.write(line)


def _pump(source, sink) -> None:
    first = True
    for line in iter(source.readline, b""):
        if first:
            record("first_line", line=line[:LIMIT].decode("utf-8", "replace"))
            first = False
        sink.write(line)
        sink.flush()


def forward(source, sink) -> None:
    """Copy the client's stdin to the real launcher, recording its first line.
    A launcher that already exited closes the pipe; that ends the copy."""
    try:
        _pump(source, sink)
        sink.close()
    except OSError:
        pass


def main() -> None:
    config = _load_config()
    record("start", argv=sys.argv, cwd=os.getcwd(), env=dict(os.environ), ppid=os.getppid(),
           real=config.REAL)
    child = subprocess.Popen([config.REAL, *sys.argv[1:]], stdin=subprocess.PIPE)
    source = sys.stdin.buffer if sys.stdin is not None else open(os.devnull, "rb")
    threading.Thread(target=forward, args=(source, child.stdin), daemon=True).start()
    code = child.wait()
    record("exit", code=code)
    sys.exit(code)
