"""git run for an oracle: bytes out, and a failure raised with what git printed.

Bytes, because a path may hold a carriage return, a tab or a non-ASCII byte,
and a text-mode pipe would translate the first and guess at the last. No crapkit.
"""
from __future__ import annotations

import os
from pathlib import Path

import hang_guard
from accuracy.kit import tiers


def environment(now: int | None = None) -> dict:
    """The parent environment, with git's clock frozen at `now` when given."""
    env = dict(os.environ)
    if now is not None:
        env["GIT_TEST_DATE_NOW"] = str(now)
    return env


def git(cwd: Path, *args: str, now: int | None = None) -> bytes:
    """One git command's stdout; a non-zero exit fails the test with git's stderr."""
    tiers.require_process("git")
    done = hang_guard.run(["git", *args], cwd=cwd, env=environment(now))
    if done.returncode != 0:
        stderr = done.stderr.decode("utf-8", "replace")
        raise AssertionError(f"git {' '.join(args)} exited {done.returncode}: {stderr}")
    return done.stdout


def text(cwd: Path, *args: str, now: int | None = None) -> str:
    """One git command's stdout as UTF-8 text with the final newline cut."""
    return git(cwd, *args, now=now).decode("utf-8").removesuffix("\n")


def top_and_prefix(root: Path) -> tuple[Path, str]:
    """The git top above `root`, and `root` relative to it ('' at the top)."""
    top = Path(text(root, "rev-parse", "--show-toplevel")).resolve()
    prefix = Path(root).resolve().relative_to(top).as_posix()
    return top, "" if prefix == "." else prefix


def under(path: str, prefix: str) -> str | None:
    """`path` cut to root-relative, or None when it lies outside the root."""
    if not prefix:
        return path
    head = prefix + "/"
    return path[len(head):] if path.startswith(head) else None
