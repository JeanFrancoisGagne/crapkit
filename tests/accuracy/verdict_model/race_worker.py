"""A `ratchet move` child that stops between reading the marks file and writing it.

test_ratchet_file runs two of these at once to force the race two writers of
crapkit-ratchet.tsv can meet: each has read the file before either writes.
The pause sits at crapkit.ratchet.move_marks, the step between the read and
the write, and waits for a go file the test creates.

usage: race_worker.py ROOT NAME OLD NEW
"""
import sys
from pathlib import Path
import time


def _wait(path: Path, seconds: float = 60.0) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            sys.exit(f"race_worker: no {path.name} within {seconds} s")
        time.sleep(0.02)


def main(root: Path, name: str, old: str, new: str) -> int:
    from crapkit import ratchet
    from crapkit.cli import main as crapkit_main

    original = ratchet.move_marks

    def paused(*args, **kwargs):
        moved = original(*args, **kwargs)
        (root / f"{name}-ready").touch()
        _wait(root / f"{name}-go")
        return moved

    ratchet.move_marks = paused
    return crapkit_main(["ratchet", "move", old, new, "--repo", str(root)])


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), *sys.argv[2:5]))
