"""Which files under a lane's scopes hold other bytes than the ones its artifact measured.

A lane's dark lines point into the bytes its run read. crapkit used to judge
them by git instead: the stamp's commit had to be behind HEAD, and nothing
under the lane's scopes could differ from that commit. Neither is the content.
A message-only amend, a rebase, a shallow CI clone or a stamp commit missing
from the clone withheld every dark line though no byte moved. An artifact
measured on an uncommitted edit read as stale at once, and reverting that edit
made git call the tree clean while the artifact still described the edit.

So a lane's stamp records a digest of every file under its scopes as its run
leaves them (`sources`), and freshness compares digests. git lists which files
are there; it never decides whether one changed.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .universe import owning_scope

# git's `ident` attribute writes `$Id: <blob> $` on checkout and stores `$Id$`.
_IDENT = re.compile(rb"\$Id:[^$\n]*\$")


def normalized(data: bytes) -> bytes:
    """The bytes as git's common filters store them: CRLF as LF, and an
    expanded `$Id$` as the bare keyword. One blob checks out either way under
    core.autocrlf, an eol attribute or the ident attribute, and none of them
    moves a line number or changes what a config file says."""
    return _IDENT.sub(b"$Id$", data.replace(b"\r\n", b"\n"))


def source_digest(path: Path) -> str:
    """sha256 of the file's `normalized` bytes, or "" when it cannot be read,
    so a deleted source never matches the digest its lane recorded."""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(normalized(data)).hexdigest()


def digests(root: Path, paths) -> dict[str, str]:
    """path -> digest for each readable file among `paths`."""
    found = {path: source_digest(root / path) for path in paths}
    return {path: digest for path, digest in found.items() if digest}


def scope_files(root: Path, declared: tuple[str, ...], matchers) -> tuple[str, ...]:
    """The tracked and untracked files under the declared scope paths that the
    lane's scopes own, ignored ones left out. Raises GitError."""
    from .lane_changes import visible_paths

    return tuple(path for path in visible_paths(root, declared) if owning_scope(path, matchers))


def settled(before: dict[str, str], after: dict[str, str]) -> dict[str, str]:
    """The digests a run can vouch for: every file as the run left it, except
    one whose bytes moved while the run was reading them."""
    return {path: digest for path, digest in after.items()
            if before.get(path, digest) == digest}


def moved(root: Path, recorded: dict, listed=()) -> list[str]:
    """Recorded files whose bytes differ now, deleted ones included, and listed
    files the record does not hold that have bytes now."""
    changed = {path for path, digest in recorded.items() if source_digest(root / path) != digest}
    return sorted(changed | _added(root, recorded, listed))


def _added(root: Path, recorded: dict, listed) -> set[str]:
    """Listed files the record does not hold and that have bytes now. git also
    lists paths no digest reads: a submodule, a directory here, and a tracked
    file deleted before the run. The record never held them, and counting them
    as new made every later read call the lane stale."""
    return {path for path in listed if path not in recorded and source_digest(root / path)}


def file_moved(root: Path, recorded: dict, path: str) -> bool:
    """Whether a file the record holds has other bytes now; False for a file it
    does not hold, which its lane never measured."""
    digest = recorded.get(path)
    return digest is not None and source_digest(root / path) != digest
