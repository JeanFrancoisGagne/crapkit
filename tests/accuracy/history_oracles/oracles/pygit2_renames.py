"""Renames between two commits as libgit2 detects them through pygit2 1.20.1. No
crapkit, and no git process: libgit2 computes the similarity itself.

find_similar with FIND_RENAMES at a 50 percent threshold, the default git
diff -M reads (git-diff docs, -M). Copies are not looked for, so a copied
file stays an added one, as `git diff -M` without -C reports it. libgit2
scores similarity its own way (a hashed line signature), so a file near the
threshold may split the two; the fixtures keep their moves far from it.
"""
from __future__ import annotations

from pathlib import Path

THRESHOLD = 50


def renames(root: Path, base: str, head: str = "HEAD") -> dict[str, str]:
    """{old top-relative path: new path} for every rename from `base` to `head`."""
    import pygit2

    repo = pygit2.Repository(str(root))
    diff = repo.diff(base, head)
    diff.find_similar(flags=pygit2.enums.DiffFind.FIND_RENAMES, rename_threshold=THRESHOLD)
    renamed = pygit2.enums.DeltaStatus.RENAMED
    return {patch.delta.old_file.path: patch.delta.new_file.path
            for patch in diff if patch.delta.status == renamed}
