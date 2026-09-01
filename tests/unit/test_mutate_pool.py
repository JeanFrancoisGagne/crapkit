"""Mutant distribution, result merge, and the worktree pool the workers run in.

The merge is the correctness seam: worker order is whatever the OS scheduler
picked, so the reported order has to come from the mutant list, never from who
finished first. The pool's seam is what survives a run: the kept trees, and
nothing half-built, because a half-built pool is reused in silence.
"""
import os
import shutil
import threading
import time

import pytest

from crapkit import mutate_pool
from crapkit.errors import GitError
from crapkit.mutate_pool import _merge, _shards, _take_lock, _worktrees, drop_pool, pool_dir


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


# --- the worktree pool: built together, and dropped together ------------------

def _fake_git(monkeypatch, on_add=None):
    """worktree_add and worktree_remove as plain directory create and delete,
    the re-prepare as nothing, HEAD as a constant. What is under test is the
    pool's ordering; git's own behaviour is gitio's and tests/unit/
    test_mutate_pool_kept.py drives the real thing.
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
    monkeypatch.setattr(mutate_pool, "worktree_reset", lambda tree, head: None)
    monkeypatch.setattr(mutate_pool, "head_commit", lambda root: "0" * 40)
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


def test_dropping_the_pool_removes_the_worktrees_at_once_too(tmp_path, monkeypatch):
    """Teardown is the same checkout in reverse and was the same serial loop."""
    together = threading.Barrier(4, timeout=15)
    removed = []
    _fake_git(monkeypatch)

    def remove(root, path):
        together.wait()  # four arrivals or none: a serial loop stalls right here
        removed.append(path)
        shutil.rmtree(path, ignore_errors=True)

    with _worktrees(tmp_path, 4) as trees:
        asked = list(trees)
    monkeypatch.setattr(mutate_pool, "worktree_remove", remove)

    assert sorted(drop_pool(tmp_path)) == sorted(asked)
    assert sorted(str(p) for p in removed) == sorted(str(p) for p in asked)


def test_an_add_that_fails_leaves_no_tree_behind_not_even_a_slow_one(tmp_path, monkeypatch):
    """The failure and its siblings race. An add still running when the first
    one raises would create its directory AFTER the cleanup had already been
    past it, so the adds have to be waited on before the drop starts."""
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
    assert list(pool_dir(tmp_path).glob("w*")) == [], "and none was left half-built"


def test_a_clean_run_leaves_the_pool_on_disk_for_the_next_one(tmp_path, monkeypatch):
    """The change the pool IS: 30.6 s of `worktree add` on a 31,459-file tree
    against 0.46 s to re-prepare what is already there."""
    tried = _fake_git(monkeypatch)

    with _worktrees(tmp_path, 3) as trees:
        assert [t.is_dir() for t in trees] == [True, True, True]

    assert [p for p in tried if p.is_dir()] == tried
    assert sorted(pool_dir(tmp_path).glob("w*")) == sorted(tried)


def test_a_body_that_raises_keeps_them_too(tmp_path, monkeypatch):
    """A run that died left dirty trees, and dirty is exactly what the next
    run's re-prepare handles. Rebuilding them would cost 35 s to reach the same
    state."""
    tried = _fake_git(monkeypatch)

    with pytest.raises(ValueError):
        with _worktrees(tmp_path, 2):
            raise ValueError("the run blew up")

    assert [p for p in tried if p.is_dir()] == tried


def test_a_run_that_loses_the_lock_removes_its_own_trees(tmp_path, monkeypatch):
    """The peer's path is the old one, and the old one cleans up after itself."""
    tried = _fake_git(monkeypatch)
    pool_dir(tmp_path).mkdir(parents=True)
    handle = os.open(pool_dir(tmp_path) / ".lock", os.O_CREAT | os.O_RDWR)
    assert _take_lock(handle)
    try:
        with _worktrees(tmp_path, 2) as trees:
            assert [t.parent for t in trees] != [pool_dir(tmp_path)] * 2
    finally:
        os.close(handle)

    assert [p for p in tried if p.exists()] == []
    assert not tried[0].parent.exists(), "the temp directory went with them"


# --- the baseline: a command that cannot run is not a suite that kills --------
#
# run_one reads any nonzero exit as a killed mutant, so a mutation_command whose
# first word is not the interpreter holding pytest killed every mutant without
# ever importing the code under test, and `mutate` printed a 100% score for a
# suite that never ran. docs ship `python -m pytest -q -x`, a bare name, so the
# machine decides.

class _Cfg:
    """The three keys run_mutants reads. mutation_command is a plain string, so
    nothing upstream of here has pinned an interpreter to it."""

    def __init__(self, command: str, workers: int = 1):
        self.mutation_command = command
        self.mutation_timeout_seconds = 60
        self.mutation_workers = workers


def _mutant(tmp_path):
    from crapkit.mutate import Mutant

    (tmp_path / "a.py").write_text("def f(x):\n    return x > 1\n", encoding="utf-8")
    return Mutant(path="a.py", line=2, op="cmp", original="x > 1", mutated="x >= 1")


def test_a_command_that_cannot_run_is_refused_before_any_mutant_is_scored(tmp_path):
    """The whole finding: exit(nonzero) with nothing mutated means the runner is
    broken, and scoring anything from it prints a perfect score for a suite that
    never started."""
    from crapkit.errors import ToolError
    from crapkit.mutate_pool import run_mutants

    scored = []
    cfg = _Cfg("no-such-runner-anywhere -m pytest")

    with pytest.raises(ToolError) as caught:
        run_mutants(tmp_path, cfg, [_mutant(tmp_path)],
                    lambda i, m, killed: scored.append(i))

    assert scored == [], "no mutant may be scored off a runner that does not run"
    assert "no-such-runner-anywhere" in str(caught.value), caught.value
    assert "100%" in str(caught.value), "the reader has to know what the score would be"


def test_a_command_that_passes_on_the_unmutated_tree_scores_normally(tmp_path):
    """The other half: the baseline must not turn a working suite into an error.
    `exit 0` is a command every shell this runs under can start."""
    from crapkit.mutate_pool import run_mutants

    verdicts = run_mutants(tmp_path, _Cfg("exit 0"), [_mutant(tmp_path)],
                           lambda i, m, killed: None)

    assert verdicts == [False], "exit 0 per mutant is a survivor, not a kill"


def test_a_baseline_that_times_out_says_so_rather_than_naming_an_exit_code(tmp_path):
    """run_bounded answers None for the deadline, and `exits None` would send the
    reader looking for an exit code nothing produced."""
    from crapkit.errors import ToolError
    from crapkit.mutate_pool import require_live_suite

    monkey = _Cfg("exit 0")
    original = mutate_pool.run_bounded
    try:
        mutate_pool.run_bounded = lambda *a, **kw: None
        with pytest.raises(ToolError) as caught:
            require_live_suite(tmp_path, monkey)
    finally:
        mutate_pool.run_bounded = original

    assert "timed out" in str(caught.value), caught.value
