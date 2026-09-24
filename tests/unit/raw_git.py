"""Commits whose bytes git stores exactly as a test wrote them.

`git commit` re-encodes a message through `i18n.commitEncoding`, and a client
cannot type an author name or a path that is not UTF-8 on every platform.
fast-import writes author, committer, message, path and blob bytes untouched,
plus an `encoding` header when a test asks for one: the shape an import from
another VCS or a client set to a legacy code page leaves in a real history.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

DAY = 86400
SOURCE = b"def f(x):\n    return x\n"


def git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=root, input=stdin, capture_output=True,
                          check=True).stdout


def repository(root: Path) -> Path:
    """An empty repo on `main` that checks files out byte for byte."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "user.name", "raw git test")
    git(root, "config", "user.email", "raw@example.test")
    return root


def has_head(root: Path) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=root,
                          capture_output=True).returncode == 0


def _header(author: bytes, committer: bytes, stamp: bytes, encoding: bytes | None) -> bytes:
    lines = [b"commit refs/heads/main\n",
             b"author " + author + b" <a@example.test> " + stamp + b"\n",
             b"committer " + committer + b" <c@example.test> " + stamp + b"\n"]
    return b"".join(lines) + (b"encoding " + encoding + b"\n" if encoding else b"")


def commit(root: Path, *, author: bytes = b"a", committer: bytes = b"c",
           message: bytes = b"edit", files: dict[bytes, bytes] | None = None,
           deletes: tuple[bytes, ...] = (), encoding: bytes | None = None,
           age_days: int = 0) -> str:
    """One commit on main whose bytes git keeps as given; returns its sha.
    Neither the index nor the working tree moves: `checkout` does that."""
    stamp = b"%d +0000" % (int(time.time()) - age_days * DAY)
    parent = b"from refs/heads/main^0\n" if has_head(root) else b""
    changes = [b"M 100644 inline " + path + b"\ndata %d\n" % len(body) + body + b"\n"
               for path, body in (files if files is not None else {b"a.py": SOURCE}).items()]
    changes += [b"D " + path + b"\n" for path in deletes]
    git(root, "fast-import", "--quiet", stdin=b"".join([
        _header(author, committer, stamp, encoding),
        b"data %d\n" % len(message), message, b"\n", parent, *changes, b"\n"]))
    return git(root, "rev-parse", "refs/heads/main").decode().strip()


def checkout(root: Path) -> None:
    """The index and the working tree at HEAD, the way a clone leaves them."""
    git(root, "reset", "-q", "--hard")
