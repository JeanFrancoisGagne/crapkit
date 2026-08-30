"""A concurrent `git worktree add` dies inside git's scan of its peers.

Before it creates anything, git's add enumerates the existing .git/worktrees/*
entries and reads each one's commondir. An entry another add is still building
kills the reader: 7 failures in 960 adds at 8 workers, 1 in 1080 at 4. The
message names the admin path, so the retry matches on the path and never on the
errno, which arrives as "No error" when the read hits a zero-byte file and "No
such file or directory" when the entry is gone by the time git opens it.

Git is fake here on purpose. The race fires once per thousand adds and never on
demand, so a real-git test would go green whether the retry exists or not.
"""
from pathlib import Path

import pytest

from crapkit import gitio
from crapkit.errors import GitError
from crapkit.gitio import worktree_add

ROOT = Path("repo")
TREE = Path("trees/w1")

# Real messages, copied from the reproduction. The prefix is what GitError adds.
PREFIX = "git worktree add --detach trees\\w1 failed in repo: "
PLAIN = PREFIX + ("Preparing worktree (detached HEAD 121dfbd)\n"
                  "fatal: failed to read .git/worktrees/w0/commondir: No error")
PLAIN_ENOENT = PREFIX + "fatal: failed to read .git/worktrees/w5/commondir: No such file or directory"
SUBMODULE = PREFIX + "fatal: failed to read C:/x/super/.git/modules/kid/worktrees/w0/commondir: No error"
SEPARATE_GIT_DIR = PREFIX + "fatal: failed to read C:/x/sgd/gitdirstore/worktrees/w0/commondir: No error"

# Failures a retry must not touch: every one of them is still there a second later.
ALREADY_EXISTS = PREFIX + "fatal: 'C:/x/trees/w1' already exists"
BAD_REF = PREFIX + "fatal: invalid reference: nosuchref"
IN_USE = PREFIX + "fatal: 'main' is already used by worktree at 'C:/x/trees/w0'"
BRANCH_TAKEN = PREFIX + "fatal: a branch named 'tmpb' already exists"


class FakeGit:
    """Records every spawn and raises the scripted failures in order."""

    def __init__(self, *failures: GitError) -> None:
        self.calls: list[tuple[Path, tuple[str, ...]]] = []
        self.failures = list(failures)

    def __call__(self, root: Path, *args: str) -> str:
        self.calls.append((root, args))
        if self.failures:
            raise self.failures.pop(0)
        return ""


@pytest.fixture()
def sleeps(monkeypatch) -> list[float]:
    """Every nap gitio takes, recorded and never slept: tests/unit is the 6-second suite."""
    naps: list[float] = []
    monkeypatch.setattr(gitio.time, "sleep", naps.append)
    return naps


def install(monkeypatch, *failures: GitError) -> FakeGit:
    fake = FakeGit(*failures)
    monkeypatch.setattr(gitio, "_git", fake)
    return fake


def test_a_clean_add_spawns_git_once(monkeypatch, sleeps) -> None:
    fake = install(monkeypatch)

    worktree_add(ROOT, TREE)

    assert fake.calls == [(ROOT, ("worktree", "add", "--detach", str(TREE)))]
    assert sleeps == []


@pytest.mark.parametrize("message", [PLAIN, PLAIN_ENOENT, SUBMODULE, SEPARATE_GIT_DIR],
                         ids=["plain", "plain-enoent", "submodule", "separate-git-dir"])
def test_a_peer_still_building_is_retried_once(monkeypatch, sleeps, message: str) -> None:
    """The admin path is relative to the git dir, which is `.git` only in a plain
    clone: a submodule names .git/modules/<name>/worktrees/, a --separate-git-dir
    repo names the store it points at. Matching `.git/worktrees/` would retry the
    first two rows here and leave the other two failing exactly as they do today."""
    fake = install(monkeypatch, GitError(message))

    worktree_add(ROOT, TREE)

    assert len(fake.calls) == 2
    assert fake.calls[0] == fake.calls[1]
    assert sleeps == [0.05]


@pytest.mark.parametrize("message", [ALREADY_EXISTS, BAD_REF, IN_USE, BRANCH_TAKEN],
                         ids=["exists", "bad-ref", "in-use", "branch-taken"])
def test_a_failure_that_is_not_the_race_propagates_at_once(monkeypatch, sleeps, message: str) -> None:
    """The assertion that keeps the retry from becoming a blanket swallow. `is
    already used by worktree at` carries the word worktrees' stem, which is why
    the guard also demands `commondir`."""
    install(monkeypatch, GitError(message))
    fake = gitio._git

    with pytest.raises(GitError) as caught:
        worktree_add(ROOT, TREE)

    assert str(caught.value) == message
    assert len(fake.calls) == 1
    assert sleeps == []


def test_a_second_failure_propagates_on_its_own_message(monkeypatch, sleeps) -> None:
    """A commondir left zero-byte for good (a killed add, a full volume) repeats
    the same message on attempt two. One retry, so it costs one spawn and 0.05 s,
    then the caller sees what git said the second time."""
    fake = install(monkeypatch, GitError(PLAIN), GitError(PLAIN_ENOENT))

    with pytest.raises(GitError) as caught:
        worktree_add(ROOT, TREE)

    assert str(caught.value) == PLAIN_ENOENT
    assert len(fake.calls) == 2
