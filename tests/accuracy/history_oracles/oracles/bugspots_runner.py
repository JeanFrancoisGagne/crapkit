"""Run bugspots_adapter.rb under libfaketime, and the transforms that make its
scores comparable with crapkit's weights. No crapkit.

- The clock: frozen at the newest commit's committer time (faketime -f with an
  absolute date freezes it), so bugspots' range runs to the newest commit, as
  crapkit's does.
- Every commit a fix: the adapter's regex is /./.
- The window (ruling H16): bugspots has no churn window. It walks the whole
  branch in topological order and dates its range from the last commit it
  walked. window() lists the commits crapkit's window holds, newest author
  date first, and the adapter walks those and no others.
- Committer time (ruling H4): bugspots dates a fix by its committer, crapkit
  by its author. retimed() copies a history with every committer date set to
  its author date (git fast-export, the committer lines rewritten, git
  fast-import), and both read the copy.
- Merges (ruling H13): bugspots reads a merge's diff against its first parent
  as the merge's own change; crapkit's log names no file for a merge.
  window() leaves merges out.
- Renames (ruling H15): bugspots reads a rename as a change to both paths;
  crapkit's log names only the new one. A caller compares the other files.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import tempfile

import hang_guard
from accuracy.kit import tiers
from .history_git import git, text

ADAPTER = Path(__file__).resolve().parent / "bugspots_adapter.rb"
_PERSON = re.compile(rb"^(author|committer) (.*) (-?\d+) ([+-]\d{4})$")


def _frozen_at(stamp: int) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _listed(commits: list[str] | None, scratch: Path) -> list[str]:
    if not commits:
        return []
    listing = scratch / "window.txt"
    listing.write_text("".join(f"{commit}\n" for commit in commits), encoding="utf-8")
    return [str(listing)]


def scores(root: Path, branch: str = "main", commits: list[str] | None = None) -> dict[str, Decimal]:
    """{top-relative path: bugspots score}, the clock at the newest commit: the
    branch tip, or the first of `commits` when bugspots walks those (newest first)."""
    tiers.require_process("bugspots")
    newest = int(text(root, "log", "-1", "--format=%ct", commits[0] if commits else branch))
    with tempfile.TemporaryDirectory() as scratch:
        argv = ["faketime", "-f", _frozen_at(newest), "ruby", str(ADAPTER), str(root), branch,
                *_listed(commits, Path(scratch))]
        done = hang_guard.run(argv, env={**os.environ, "TZ": "UTC"}, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return {path: Decimal(score) for path, score in json.loads(done.stdout)["spots"]}


def window(commits: list) -> list[str]:
    """The ids of a walk's commits that name a file (a merge names none),
    newest author date first: the commits crapkit weighs, in the order that
    makes bugspots' last fix the oldest."""
    named = [commit for commit in commits if commit.paths]
    return [commit.sha for commit in sorted(named, key=lambda commit: -commit.at)]


def _retime_lines(stream: bytes) -> bytes:
    """A fast-export stream with each committer date set to its author's.
    Blob and message bodies pass through untouched: `data N` spans N raw bytes."""
    out, i, author = bytearray(), 0, None
    while i < len(stream):
        end = stream.index(b"\n", i) + 1
        line = stream[i:end]
        i = end
        if line.startswith(b"data "):
            size = int(line[5:])
            out += line + stream[i:i + size]
            i += size
            continue
        out += _person(line.rstrip(b"\n"), author) + b"\n" if _PERSON.match(line.rstrip(b"\n")) else line
        author = _date_of(line, author)
    return bytes(out)


def _date_of(line: bytes, author):
    match = _PERSON.match(line.rstrip(b"\n"))
    return match.group(3, 4) if match and match.group(1) == b"author" else author


def _person(line: bytes, author) -> bytes:
    match = _PERSON.match(line)
    if match.group(1) == b"author" or author is None:
        return line
    return b"committer " + match.group(2) + b" " + b" ".join(author)


def retimed(root: Path, dest: Path) -> Path:
    """A copy of the history at `root` whose committer dates are its author dates."""
    stream = git(root, "fast-export", "--all", "--signed-tags=strip")
    dest.mkdir(parents=True)
    git(dest, "init", "-q")
    tiers.require_process("git")
    done = hang_guard.run(["git", "fast-import", "--quiet"], cwd=dest,
                          input=_retime_lines(stream))
    assert done.returncode == 0, done.stderr
    git(dest, "symbolic-ref", "HEAD", text(root, "symbolic-ref", "HEAD"))
    return dest
