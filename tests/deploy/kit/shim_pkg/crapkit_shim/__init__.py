"""A `crapkit` that records how it was started, then runs the real one.

The record is one JSON line per event, appended to the log path baked into
_config.py: `start` (argv, cwd, the whole environment, pid, ppid), `first_line`
(the first line the client wrote to stdin, up to 64 KiB) and `exit` (the real
launcher's exit code). Stdin is forwarded line by line; stdout and stderr are
the real launcher's own, inherited, so the client talks to crapkit directly.

`ppid` is the process that started `crapkit`: the harness. On Windows a
console-script crapkit.exe is a launcher that starts the venv's python.exe,
which starts this interpreter, so there the harness is the launcher's parent
and the start record names the launcher as `launcher_pid`.
"""
import importlib
import json
import ntpath
import os
import subprocess
import sys
import threading

TH32CS_SNAPPROCESS = 2
LAUNCHER_DEPTH = 3

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


def _entry_type():
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]
    return Entry


def processes() -> dict:
    """pid -> (parent pid, executable name) for every process (Windows)."""
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    entry = _entry_type()()
    entry.dwSize = ctypes.sizeof(entry)
    snapshot = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    table, more = {}, kernel.Process32FirstW(snapshot, ctypes.byref(entry))
    while more:
        table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
        more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    kernel.CloseHandle(snapshot)
    return table


def parents(table: dict, ppid: int, launcher: str) -> dict:
    """{"ppid": the process that ran `launcher`, "launcher_pid": the launcher}
    when the launcher is an ancestor within LAUNCHER_DEPTH; else our parent.
    On Windows the chain is harness -> crapkit.exe -> the venv's python.exe
    redirector -> this interpreter."""
    pid = ppid
    for _ in range(LAUNCHER_DEPTH):
        entry = table.get(pid)
        if entry is None:
            break
        if _stem(entry[1]) == _stem(launcher):
            return {"ppid": entry[0], "launcher_pid": pid}
        pid = entry[0]
    return {"ppid": ppid}


def _stem(path: str) -> str:
    """A Windows executable's name without its extension, in either slash."""
    return ntpath.splitext(ntpath.basename(path))[0].lower()


def starter() -> dict:
    if os.name != "nt":
        return {"ppid": os.getppid()}
    return parents(processes(), os.getppid(), sys.argv[0])


def main() -> None:
    config = _load_config()
    record("start", argv=sys.argv, cwd=os.getcwd(), env=dict(os.environ), real=config.REAL, **starter())
    child = subprocess.Popen([config.REAL, *sys.argv[1:]], stdin=subprocess.PIPE)
    source = sys.stdin.buffer if sys.stdin is not None else open(os.devnull, "rb")
    threading.Thread(target=forward, args=(source, child.stdin), daemon=True).start()
    code = child.wait()
    record("exit", code=code)
    # os._exit, not sys.exit: when the client still holds stdin open, the
    # forwarding thread sits in a read on sys.stdin, and interpreter shutdown
    # then dies with "Fatal Python error: _enter_buffered_busy" (exit 139, or
    # 0xC0000005 on Windows) in place of crapkit's own code. Nothing is left to
    # flush: the child wrote its output to the inherited stdout itself.
    os._exit(code)
