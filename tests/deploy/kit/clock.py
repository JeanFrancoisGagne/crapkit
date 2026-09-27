"""The moved clock of a `run.py --faketime` run (lin-clock).

run.py mounts /etc/ld.so.preload naming libfaketime and /etc/faketimerc
holding the offset. The dynamic loader reads /etc/ld.so.preload, so every
dynamically linked process in the container starts under libfaketime and
reads the moved clock, whatever environment the kit or a harness hands it and
whatever language built it: Goose and prek are Rust binaries linked against
glibc, and both read the moved clock. A program that names no dynamic loader
keeps the real clock. REAL_CLOCK names each such program the images hold
under /opt, /usr/bin and /usr/local/bin: uv and uvx; Crush and act, Go
binaries; the Codex CLI's musl build and the code-mode host, rg and bwrap it
ships; the rg, cursorsandbox and crepectl the Cursor agent ships; and in the
gui image, the rg, tgrep and apply-seccomp VS Code ships. test_kit_isolation
walks those directories in each image a run uses and fails when a program's
headers and REAL_CLOCK disagree. A musl build that names musl's loader never
starts at all: the Debian images hold no such loader, and the harness
launchers pick the glibc build. A cell that asserts a time reads it through
date, git, node or python. Two more things follow for a cell:

  FAKETIME_SHARED  libfaketime writes it into the environment of each process
                   it loads into (the shared memory that keeps a process tree
                   on one clock), so a child's environment holds it although
                   no allowlist passed it. added_env() names it under a moved
                   clock, and every environment allowlist check takes it.
  CANNOT_START     harness releases that never start under libfaketime.
                   Sandbox.resolve and Sandbox.script skip the cell at the
                   first start of one and say why; every step before it ran
                   on the moved clock. test_kit_isolation probes each entry
                   under the clock and fails once a release starts again.
"""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path

import pytest

PRELOAD = Path("/etc/ld.so.preload")
ADDED_ENV = frozenset({"FAKETIME_SHARED"})
# The file names of the programs under /opt, /usr/bin and /usr/local/bin in the
# images that name no dynamic loader, so /etc/ld.so.preload never reaches them
# and they keep the real clock. The codex here is the musl binary the Codex
# CLI's node launcher starts, current and floor releases alike; act is in the
# ci image, tgrep and apply-seccomp in the gui image. test_kit_isolation walks
# those directories in each image a run uses and holds each listed program
# loader-free and every other one dynamically linked.
REAL_CLOCK = frozenset({
    "uv", "uvx",                                     # the toolchain
    "codex", "codex-code-mode-host", "rg", "bwrap",  # the Codex CLI's musl build
    "cursorsandbox", "crepectl",                     # the Cursor agent (its rg too)
    "crush", "act",                                  # Go binaries
    "tgrep", "apply-seccomp",                        # VS Code (its rg too)
})
# npm package -> why its pinned release never starts under libfaketime.
# Measured with `claude --version` in the core image: exit 124 under `timeout`
# at +0, +1d, +30d and +400d, and with FAKETIME_DONT_FAKE_MONOTONIC=1,
# FAKETIME_DISABLE_SHM=1 or FAKETIME_SKIP_CMDS=claude.exe; 2.1.138 and 2.1.139
# start, as do Bun, OpenCode and Amp.
CANNOT_START = {
    "@anthropic-ai/claude-code": (
        "Claude Code 2.1.281 deadlocks before main: libfaketime's constructor looks up libc symbols, "
        "the lookup calls the binary's own malloc, and that malloc reads the clock through libfaketime, "
        "which waits on its own unfinished start"),
}
# The first word of each command in a pasted script: at a line start or after ; & |.
COMMAND_WORD = re.compile(r"(?:^|[;&|])[ \t]*([^\s;&|()]+)", re.M)


@functools.lru_cache(maxsize=None)
def _names_libfaketime(preload: str) -> bool:
    try:
        return "libfaketime" in Path(preload).read_text(encoding="utf-8")
    except OSError:
        return False


def moved() -> bool:
    """Whether this container runs under `run.py --faketime`."""
    return _names_libfaketime(str(PRELOAD))


def added_env() -> frozenset[str]:
    """The environment names libfaketime adds to every process it loads into."""
    return ADDED_ENV if moved() else frozenset()


def blocker(path: str) -> str | None:
    """Why the program at `path` cannot start under this container's moved clock, or None."""
    if not moved():
        return None
    real = Path(os.path.realpath(path)).as_posix()
    return next((why for package, why in CANNOT_START.items() if f"/node_modules/{package}/" in real), None)


def skip_unstartable(path: str) -> None:
    """Skip the running cell at a program the moved clock cannot start."""
    why = blocker(path)
    if why:
        pytest.skip(f"lin-clock: {path} cannot start under libfaketime, so the cell stops here: {why}. "
                    "Every step before this one ran on the moved clock; kit/clock.py CANNOT_START lists "
                    "the release")


def skip_unstartable_in(text: str, which) -> None:
    """Skip the running cell at a pasted script whose commands start such a program."""
    if not moved():
        return
    for word in COMMAND_WORD.findall(text):
        found = which(word)
        if found:
            skip_unstartable(found)
