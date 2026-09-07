"""Keep measurement locks until its registered command trees have stopped.

The caller keeps stdin open. EOF means normal release or a dead caller; both
stop any commands still registered before releasing the OS locks.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys

from .errors import ToolError
from .locks import exclusive_lock
from .procs import _kill_pid


def _reply(value: dict) -> None:
    print(json.dumps(value), flush=True)


def _process_handle(pid: int):
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), "cannot retain command identity")
    return handle


def _close_handle(handle) -> None:
    if handle is not None:
        import ctypes
        from ctypes import wintypes
        ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(handle))


def _still_alive(handle) -> bool:
    if handle is None:
        return True
    import ctypes
    from ctypes import wintypes
    return ctypes.windll.kernel32.WaitForSingleObject(wintypes.HANDLE(handle), 0) == 258


def _stop_children(children: dict) -> None:
    for pid, handle in children.items():
        try:
            if _still_alive(handle):
                _kill_pid(pid)
        finally:
            _close_handle(handle)


def _serve() -> None:
    children = {}
    try:
        for line in sys.stdin:
            operation, pid = json.loads(line)
            if operation == "add":
                children[pid] = _process_handle(pid)
            else:
                _close_handle(children.pop(pid, None))
            _reply({"ok": True})
    finally:
        _stop_children(children)


def main(paths: list[str]) -> None:
    try:
        with ExitStack() as stack:
            for path in paths:
                stack.enter_context(exclusive_lock(Path(path), label="measurement"))
            _reply({"ok": True})
            _serve()
    except (OSError, ToolError) as error:
        _reply({"error": str(error)})


if __name__ == "__main__":
    main(sys.argv[1:])
