"""Changed new-side lines between two commits, computed by libgit2 through pygit2
1.20.1 with no context lines. No crapkit, and no git process: libgit2 diffs the
trees itself, so this checks git's diff text and crapkit's reading of it
together.

Renames are not detected (a moved file is a new one), as in the -U0 diff with
--no-renames that crapkit reads. A pure deletion takes the H9 reading of
oracles/unidiff_ranges.py: the new-side line its hunk header names.
"""
from __future__ import annotations

from pathlib import Path


def _span(hunk) -> tuple[int, int]:
    if hunk.new_lines:
        return hunk.new_start, hunk.new_start + hunk.new_lines - 1
    line = max(hunk.new_start, 1)
    return line, line


def _kept(patch) -> bool:
    import pygit2

    return patch.delta.status != pygit2.enums.DeltaStatus.DELETED and bool(patch.hunks)


def patch(old: str, new: str, path: str) -> tuple[str, dict[str, list[tuple[int, int]]]]:
    """(the patch text libgit2 writes from `old` to `new` with no context lines,
    {path: hunk spans}) for one file's two texts; no spans when they are equal."""
    import pygit2

    made = pygit2.Patch.create_from(old, new, path, path, context_lines=0, interhunk_lines=0)
    return made.text or "", ({path: [_span(hunk) for hunk in made.hunks]} if made.hunks else {})


def ranges(root: Path, base: str, head: str = "HEAD") -> dict[str, list[tuple[int, int]]]:
    """{top-relative path: hunk spans} from `base` to `head`."""
    import pygit2

    repo = pygit2.Repository(str(root))
    diff = repo.diff(base, head, context_lines=0, interhunk_lines=0)
    return {patch.delta.new_file.path: [_span(hunk) for hunk in patch.hunks]
            for patch in diff if _kept(patch)}
