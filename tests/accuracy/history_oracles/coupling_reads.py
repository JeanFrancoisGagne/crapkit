"""What crapkit says about change coupling, and what git says the pairs are.

crapkit is read through `coupling --json`; the expected pairs are counted by
oracles/pair_count.py over the commits oracles/git_walk.py reads, ranked over
the paths `git ls-files -z` lists. No crapkit import.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from accuracy.kit import drive
from accuracy.history_oracles.oracles import git_walk, pair_count
from accuracy.history_oracles.oracles.history_git import git

ALL = ("--min-support", "1", "--min-confidence", "0", "--top", "1000")


def said(root: Path, now: int, *flags: str) -> list[tuple]:
    """(files, support, confidence) per pair `coupling --json` prints, in its order."""
    result = drive.Driver(root, date_now=now).run("coupling", "--json", *flags)
    assert result.code == 0, result.stderr
    return [(tuple(pair["files"]), pair["support"], Decimal(repr(pair["confidence"])))
            for pair in result.json()["pairs"]]


def tracked(root: Path) -> set[str]:
    """What `git ls-files` lists at the root, root-relative and verbatim."""
    out = git(root, "ls-files", "-z")
    return {path.decode("utf-8") for path in out.split(b"\0") if path}


def expected(root: Path, months: int = 12, min_support: int = 1,
             min_confidence: Fraction = Fraction(0)) -> list[tuple]:
    commits = git_walk.walk(root, months)
    pairs = pair_count.ranked(pair_count.change_sets(commits), tracked(root), min_support,
                              min_confidence)
    return [(pair.files, pair.support, pair.confidence) for pair in pairs]
