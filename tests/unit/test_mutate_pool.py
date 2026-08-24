"""Mutant distribution, result merge, and the worktree pool the workers run in.

The merge is the correctness seam: worker order is whatever the OS scheduler
picked, so the reported order has to come from the mutant list, never from who
finished first. The pool's seam is cleanup: a tree left behind is a whole
checkout sitting in the temp directory and a stale entry in git's worktree list.
"""
import shutil
import threading
import time

import pytest

from crapkit import mutate_pool
from crapkit.errors import GitError
from crapkit.mutate_pool import _merge, _shards, _worktrees


def test_round_robin_covers_every_mutant_exactly_once():
    indexed = list(enumerate("abcdefg"))
    shards = _shards(indexed, 3)
    assert [len(s) for s in shards] == [3, 2, 2]
    assert sorted(p for s in shards for p in s) == indexed


def test_one_worker_keeps_the_whole_list_in_order():
    indexed = list(enumerate("abc"))
    assert _shards(indexed, 1) == [indexed]


def test_more_workers_than_mutants_leaves_empty_shards():
    assert _shards([(0, "a")], 3) == [[(0, "a")], [], []]


def test_merge_ignores_completion_order():
    scrambled = [(2, True), (0, False), (3, True), (1, True)]
    assert _merge(scrambled) == [False, True, True, True]


def test_merge_of_nothing_is_nothing():
    assert _merge([]) == []


@pytest.mark.parametrize("workers", [1, 2, 3, 4, 7])
def test_merge_of_any_shard_split_reproduces_the_mutant_order(workers: int):
    flags = [i % 3 == 0 for i in range(7)]
    indexed = list(enumerate(flags))
    done = [(i, flag) for shard in _shards(indexed, workers) for i, flag in shard]
    assert _merge(done) == flags


# --- the worktree pool: created together, and gone whatever happened ---------

def _fake_git(monkeypatch, on_add=None):
    """worktree_add and worktree_remove as plain directory create and delete.
    What is under test is the pool's ordering; git's own behaviour is gitio's.
    Returns the paths the pool ASKED for, failed adds included."""
    tried = []

    def add(root, path):
        tried.append(path)
        if on_add is not None:
            on_add(path)
        path.mkdir(parents=True)

    def remove(root, path):
        shutil.rmtree(path, ignore_errors=True)

    monkeypatch.setattr(mutate_pool, "worktree_add", add)
    monkeypatch.setattr(mutate_pool, "worktree_remove", remove)
    return tried


def test_the_worktrees_are_all_created_at_once_not_one_after_another(tmp_path, monkeypatch):
    """Each add is a full checkout, almost all of it waiting on the disk. Four
    of them serialized cost 55.1 s on a 31,620-file repo against 29.6 s
    overlapped, so a barrier that only four concurrent adds can clear is the
    contract: a serial loop never gets a second arrival."""
    together = threading.Barrier(4, timeout=15)
    _fake_git(monkeypatch, on_add=lambda path: together.wait())

    with _worktrees(tmp_path, 4) as trees:
        assert len(trees) == 4
        assert all(t.is_dir() for t in trees)


def test_the_worktrees_are_all_removed_at_once_too(tmp_path, monkeypatch):
    """Teardown is the same checkout in reverse and was the same serial loop."""
    together = threading.Barrier(4, timeout=15)
    removed = []
    _fake_git(monkeypatch)

    def remove(root, path):
        together.wait()  # four arrivals or none: a serial loop stalls right here
        removed.append(path)
        shutil.rmtree(path, ignore_errors=True)

    monkeypatch.setattr(mutate_pool, "worktree_remove", remove)

    with _worktrees(tmp_path, 4) as trees:
        asked = list(trees)

    assert sorted(str(p) for p in removed) == sorted(str(p) for p in asked)


def test_an_add_that_fails_leaves_no_tree_behind_not_even_a_slow_one(tmp_path, monkeypatch):
    """The failure and its siblings race. An add still running when the first
    one raises would create its directory AFTER the cleanup had already been
    past it, so the adds have to be waited on before teardown starts."""
    made = threading.Semaphore(0)

    def refuse_the_first(path):
        if path.name == "w0":
            raise GitError("checkout refused")
        time.sleep(0.25)  # still creating when w0's failure surfaces
        made.release()

    tried = _fake_git(monkeypatch, on_add=refuse_the_first)

    with pytest.raises(GitError):
        with _worktrees(tmp_path, 4):
            pass

    for _ in [p for p in tried if p.name != "w0"]:
        assert made.acquire(timeout=15), "every slow add finished before we looked"
    assert [p for p in tried if p.exists()] == [], "no tree survived the failure"
    assert not tried[0].parent.exists(), "the temp directory went with them"


def test_a_clean_run_removes_every_tree_and_the_directory_holding_them(tmp_path, monkeypatch):
    tried = _fake_git(monkeypatch)

    with _worktrees(tmp_path, 3) as trees:
        assert [t.is_dir() for t in trees] == [True, True, True]

    assert [p for p in tried if p.exists()] == []
    assert not tried[0].parent.exists()


def test_a_body_that_raises_still_takes_the_trees_with_it(tmp_path, monkeypatch):
    tried = _fake_git(monkeypatch)

    with pytest.raises(ValueError):
        with _worktrees(tmp_path, 2):
            raise ValueError("the run blew up")

    assert [p for p in tried if p.exists()] == []
