"""Renames between two commits as libgit2 detects them through pygit2 1.20.1. No
crapkit, and no git process: libgit2 computes the similarity itself.

find_similar with FIND_RENAMES at a 50 percent threshold, the default git
diff -M reads (git-diff docs, -M). Copies are not looked for, so a copied
file stays an added one, as `git diff -M` without -C reports it. libgit2
scores similarity its own way (a hashed line signature), so a file near the
threshold may split the two; the fixtures keep their moves far from it.

On real histories the two scores can sit far apart: click's docs/index.rst to
docs/index.md (an rst page rewritten as Markdown) is 36 percent to git and 64
to libgit2. crapkit's rule is git's ("git calls that path renamed",
docs/ratchet.md), so a pair whose two scores fall on either side of the
threshold is the oracle's to explain, not crapkit's: contested() names them,
from libgit2's score and the score git prints (`git diff -M1 --name-status`).
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


def scores(root: Path, base: str, head: str = "HEAD") -> dict[str, int]:
    """{old path: libgit2's similarity} for every rename it finds at any score."""
    import pygit2

    repo = pygit2.Repository(str(root))
    diff = repo.diff(base, head)
    diff.find_similar(flags=pygit2.enums.DiffFind.FIND_RENAMES, rename_threshold=1)
    renamed = pygit2.enums.DeltaStatus.RENAMED
    return {patch.delta.old_file.path: patch.delta.similarity
            for patch in diff if patch.delta.status == renamed}


def git_scores(name_status: bytes) -> dict[str, int]:
    """{old path: git's score} from `git diff -M1 --name-status -z` output."""
    fields = name_status.decode("utf-8").split("\0")
    found, i = {}, 0
    while i < len(fields) - 1:
        status = fields[i]
        if status.startswith("R"):
            found[fields[i + 1]] = int(status[1:])
        i += 3 if status[:1] in ("R", "C") else 2
    return found


def contested(ours: dict[str, int], gits: dict[str, int]) -> set[str]:
    """Old paths whose libgit2 and git scores fall on either side of THRESHOLD."""
    return {path for path in {*ours, *gits}
            if (ours.get(path, 0) >= THRESHOLD) != (gits.get(path, 0) >= THRESHOLD)}
