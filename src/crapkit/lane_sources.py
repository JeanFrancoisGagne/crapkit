"""The content record: which bytes each file under a lane's scopes held when its artifact measured it.

A lane's dark lines point into the bytes its run read. crapkit used to judge
them by git instead: the stamp's commit had to be behind HEAD, and nothing
under the lane's scopes could differ from that commit. Neither is the content.
A message-only amend, a rebase, a shallow CI clone or a stamp commit missing
from the clone withheld every dark line though no byte moved. An artifact
measured on an uncommitted edit read as stale at once, and reverting that edit
made git call the tree clean while the artifact still described the edit.

So a lane's stamp records the git blob id of every file under its scopes as its
run leaves them (`blobs`), and freshness compares blob ids. A blob id is what
`git add` would store: the bytes through the repo's filters, so a CRLF checkout
under core.autocrlf=true and an expanded `$Id$` hold their blob's id, while a
CRLF rewrite under core.autocrlf=false, or a pending renormalization, does not.

git's index is the fast path. A tracked file that `git status` calls
unchanged holds the id the index records, so only a file git calls changed,
one it does not track, and one flagged skip-worktree or assume-unchanged is
hashed. The trade: git decides "unchanged" from the index's stat cache, so a
same-size edit whose old modification time was put back (`cp -p`, `tar -x`,
`rsync -t`) keeps the index's id. That is a named limit until the cost of
hashing every file is measured. It holds on Windows, where the change time is
the creation time, and under `core.trustctime=false`. On Linux and macOS git's
default stat check also compares the change time, which no copy puts back, so
git sees the edit once the change time moves a second past the one it recorded.

`watch` records and compares through `record` too: its first poll records every
watched file's id, and each later poll asks again only for the files whose
mtime moved, so a touch and a line-ending rewrite git stores as the same blob
rescore nothing there either.
"""
from __future__ import annotations

from pathlib import Path

from .errors import GitError
from .gitio import index_blobs, worktree_blobs, worktree_changes
from .universe import owning_scope


def record(root: Path, paths, within=()) -> dict[str, str]:
    """path -> blob id for each of `paths` that is a file now, or a submodule
    whose checkout is clean (its commit). `within` narrows git's reads to the
    lane's declared paths; a path outside them is hashed. Raises GitError."""
    wanted = set(paths)
    if not wanted:
        return {}
    held = _held(root, wanted, tuple(within))
    return {**held, **worktree_blobs(root, _files(root, wanted - held.keys()))}


def _held(root: Path, wanted: set, spec: tuple) -> dict[str, str]:
    """The index's id for each wanted path `git status` calls unchanged."""
    changed = set(worktree_changes(root, spec))
    return {path: blob for path, blob in index_blobs(root, spec).items()
            if path in wanted and path not in changed}


def _files(root: Path, paths) -> list[str]:
    return sorted(path for path in paths if (root / path).is_file())


def scope_files(root: Path, declared: tuple[str, ...], matchers) -> tuple[str, ...]:
    """The tracked and untracked files under the declared scope paths that the
    lane's scopes own, ignored ones left out. Raises GitError."""
    from .lane_changes import visible_paths

    return tuple(path for path in visible_paths(root, declared) if owning_scope(path, matchers))


def settled(before: dict[str, str], after: dict[str, str]) -> dict[str, str]:
    """The ids a run can vouch for: every file as the run left it, except one
    whose bytes moved while the run was reading them."""
    return {path: blob for path, blob in after.items() if before.get(path, blob) == blob}


def moved(root: Path, recorded: dict, listed=(), within=()) -> list[str]:
    """Recorded files whose blob id differs now, deleted ones included, and
    listed files the record does not hold that are files now. A listed path the
    record never held and that holds nothing (a tracked file deleted before the
    run, a submodule with an edit in it) is not new. Raises GitError."""
    now = record(root, {*recorded, *listed}, within)
    changed = {path for path, blob in recorded.items() if now.get(path) != blob}
    return sorted(changed | _added(recorded, listed, now))


def _added(recorded: dict, listed, now: dict) -> set[str]:
    return {path for path in listed if path not in recorded and path in now}


def file_moved(root: Path, recorded: dict, path: str) -> bool:
    """Whether a file the record holds has another blob id now; False for a
    file it does not hold, which its lane never measured. Raises GitError."""
    blob = recorded.get(path)
    return blob is not None and record(root, (path,), (path,)).get(path) != blob


# --- a lane's record ---------------------------------------------------------------

def declared_paths(lane, scope_paths: dict) -> tuple[str, ...]:
    """The paths this lane's scopes declare, as written in the config."""
    return tuple(path for name in lane.scopes for path in scope_paths.get(name, ()))


def lane_matchers(lane, scope_paths: dict):
    """Ownership over the scopes THIS lane names, read by universe's one predicate.

    Both questions a lane asks about paths go through here: which of its scopes'
    files moved since the artifact was stamped, and whether the artifact reached
    any of them at all. A second hand-rolled prefix test would answer the exact
    arm, a scope that declares a FILE rather than a directory, differently from
    the rule that assigns files, and the two would drift.
    """
    from .universe import path_matchers

    return path_matchers({name: scope_paths.get(name, ()) for name in lane.scopes})


def listing(root: Path, lane, scope_paths: dict, outputs: frozenset = frozenset()) -> list[str]:
    """The files git lists under the lane's scopes, `outputs` left out; none when
    git cannot list them, and then a record holds what the artifact names."""
    matchers = lane_matchers(lane, scope_paths)
    try:
        listed = scope_files(root, declared_paths(lane, scope_paths), matchers) if matchers else ()
    except GitError:
        return []
    return [path for path in listed if path not in outputs]


def lane_record(root: Path, lane, scope_paths: dict | None, outputs: frozenset,
                measured=()) -> dict | None:
    """The blob id of every file under the lane's scopes and of every in-tree
    file its artifact measured (`measured`), but the lanes' own outputs; None
    when git cannot give them."""
    scope_paths = scope_paths or {}
    try:
        return record(root, {*listing(root, lane, scope_paths, outputs), *measured},
                      declared_paths(lane, scope_paths))
    except GitError:
        return None
