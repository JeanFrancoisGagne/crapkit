"""Process ownership for short operations on shared local files."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import BinaryIO

from .errors import ToolError


@contextmanager
def exclusive_lock(path: Path, *, label: str):
    """Refuse peers without waiting; keep the lock file stable after release."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as owner:
        owner.seek(0, os.SEEK_END)
        if owner.tell() == 0:
            owner.write(b"\0")
            owner.flush()
        try:
            _lock(owner, True)
        except OSError as exc:
            raise ToolError(f"{label} already in use; wait for its owner to finish") from exc
        try:
            yield
        finally:
            _lock(owner, False)


def _lock(owner: BinaryIO, take: bool) -> None:
    owner.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(owner, (fcntl.LOCK_EX | fcntl.LOCK_NB) if take else fcntl.LOCK_UN)
