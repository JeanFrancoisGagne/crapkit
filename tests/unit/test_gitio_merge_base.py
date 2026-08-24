"""merge-base and ancestry against an arbitrary commit, not just HEAD.

`verify --base REF` needs both: the commit a branch actually forked from, and
whether a recorded run sits at or behind it. Real git in tmp_path — the answer
these two give decides what a verify measures, so a stub would prove nothing.
"""
import subprocess
from pathlib import Path

import pytest

from crapkit.errors import GitError
from crapkit.gitio import is_ancestor, merge_base


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout.strip()


def commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def forked(tmp_path: Path) -> tuple[Path, str, str, str]:
    """main at C1; a feature branch two commits past it, checked out."""
    git(tmp_path, "init", "-q", "-b", "main")
    base = commit(tmp_path, "one")
    git(tmp_path, "checkout", "-q", "-b", "feature")
    mid = commit(tmp_path, "two")
    head = commit(tmp_path, "three")
    return tmp_path, base, mid, head


def test_merge_base_is_the_fork_point_not_head(forked):
    repo, base, _, head = forked

    assert merge_base(repo, "main") == base
    assert merge_base(repo, "main") != head


def test_a_commit_is_its_own_ancestor(forked):
    repo, base, _, _ = forked

    assert is_ancestor(repo, base, base) is True


def test_a_branch_commit_is_not_behind_the_fork_point(forked):
    repo, base, mid, _ = forked

    assert is_ancestor(repo, mid, base) is False
    assert is_ancestor(repo, base, mid) is True


def test_ancestry_still_defaults_to_head(forked):
    repo, base, mid, _ = forked

    assert is_ancestor(repo, base) is True
    assert is_ancestor(repo, mid) is True


def test_an_unrelated_history_has_no_merge_base(tmp_path: Path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "one")
    git(tmp_path, "checkout", "-q", "--orphan", "other")
    commit(tmp_path, "two")

    with pytest.raises(GitError):
        merge_base(tmp_path, "main")
