"""The worktree pool is kept between runs, and every run starts from a tree that
holds exactly what a fresh checkout of the repo's HEAD would hold.

Real git here, unlike test_mutate_pool.py: what is under test is what a git
worktree does to a directory across two runs, and a fake add that makes a folder
would go green whether the re-prepare works or not. The repos are two files.

A fresh set of four checkouts costs 35.5 s on a 31,459-file tree; re-preparing
the kept set costs 0.44 s. That is the whole point of the pool, so the tests
that matter are the ones saying the reused tree is not a cheaper LIE: the last
run's mutant is gone, the last suite's artifacts are gone, and a commit made
between two runs is there.
"""
import os
import subprocess
from pathlib import Path

import pytest

from crapkit import mutate_pool
from crapkit.errors import GitError
from crapkit.mutate_pool import _take_lock, _worktrees, drop_pool, pool_dir


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "gone.py").write_text("old = 1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\nartifacts/\n", encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "one")
    return tmp_path


@pytest.fixture()
def adds(monkeypatch) -> list:
    """Every worktree git actually builds, still built."""
    made: list = []
    real = mutate_pool.worktree_add

    def counted(root: Path, path: Path) -> None:
        made.append(path)
        real(root, path)

    monkeypatch.setattr(mutate_pool, "worktree_add", counted)
    return made


def listed(repo: Path) -> list[str]:
    return [ln for ln in git(repo, "worktree", "list", "--porcelain").splitlines()
            if ln.startswith("worktree ")]


def text(path: Path) -> str:
    """Line endings normalized: a checkout under core.autocrlf writes CRLF and
    what these tests are about is which COMMIT the tree holds."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def contents(tree: Path) -> dict[str, bytes]:
    """Every file in the tree bar git's own admin pointer."""
    return {str(p.relative_to(tree)).replace("\\", "/"): p.read_bytes()
            for p in sorted(tree.rglob("*")) if p.is_file() and p.name != ".git"}


# --- the pool is kept --------------------------------------------------------

def test_the_second_run_reuses_the_pool_instead_of_building_it_again(repo, adds):
    with _worktrees(repo, 2) as first:
        asked = list(first)
    assert [t.is_dir() for t in asked] == [True, True], "the trees outlive the run"
    assert len(adds) == 2

    with _worktrees(repo, 2) as second:
        assert list(second) == asked

    assert len(adds) == 2, "the kept trees were re-prepared, not rebuilt"


def test_the_pool_lives_under_the_repos_crapkit_directory(repo):
    with _worktrees(repo, 2) as trees:
        assert [t.parent for t in trees] == [pool_dir(repo)] * 2
        assert pool_dir(repo) == repo / ".crapkit" / "mutate-pool"


def test_a_reused_tree_loses_the_last_runs_mutant(repo):
    with _worktrees(repo, 1) as (tree,):
        (tree / "m.py").write_text("x = 99  # mutant\n", encoding="utf-8")

    with _worktrees(repo, 1) as (tree,):
        assert text(tree / "m.py") == "x = 1\n"


def test_a_reused_tree_loses_what_the_last_suite_wrote(repo):
    """`checkout --force` restores tracked files and leaves every artifact the
    suite wrote beside them, ignored ones included: the clean is what makes the
    reused tree equal to a fresh one."""
    with _worktrees(repo, 1) as (tree,):
        (tree / "artifacts").mkdir()
        (tree / "artifacts" / "junit.xml").write_text("<x/>", encoding="utf-8")
        (tree / "loose.txt").write_text("residue", encoding="utf-8")

    with _worktrees(repo, 1) as (tree,):
        assert not (tree / "artifacts").exists(), "an ignored artifact directory is still residue"
        assert not (tree / "loose.txt").exists()


def test_a_reused_tree_holds_what_a_fresh_checkout_would(repo, tmp_path):
    """The whole claim in one assertion: after the pool has been run in, mutated,
    littered and had a tracked file deleted, its content equals a worktree git
    just made."""
    with _worktrees(repo, 1) as (tree,):
        (tree / "m.py").write_text("x = 99\n", encoding="utf-8")
        (tree / "gone.py").unlink()
        (tree / "junk").mkdir()
        (tree / "junk" / "a.json").write_text("{}", encoding="utf-8")

    fresh = tmp_path / "fresh"
    git(repo, "worktree", "add", "-q", "--detach", str(fresh))
    with _worktrees(repo, 1) as (tree,):
        assert contents(tree) == contents(fresh)
        assert git(tree, "status", "--porcelain") == ""


def test_the_pool_follows_a_head_that_moved_between_two_runs(repo):
    """The hazard the sha closes. `checkout --force HEAD` inside a kept tree
    means the tree's OWN detached HEAD, which is the commit the pool was built
    at: the second run would mutate the first commit's content and score it."""
    with _worktrees(repo, 1):
        pass
    (repo / "m.py").write_text("x = 2\n", encoding="utf-8")
    moved = commit(repo, "two")

    with _worktrees(repo, 1) as (tree,):
        assert text(tree / "m.py") == "x = 2\n"
        assert git(tree, "rev-parse", "HEAD").strip() == moved


def test_a_tree_git_no_longer_knows_is_rebuilt_and_never_run_in(repo, adds):
    """The hazard of keeping the pool INSIDE the repo. A pool directory whose
    `.git` pointer is gone — a killed run, a hand-deleted admin entry — is not a
    worktree, and git commands find their repository by walking UP: a re-prepare
    there would `checkout --force` and `clean -xdff` the USER's working tree.
    The answer is a rebuilt pool, with the dirty main tree untouched."""
    with _worktrees(repo, 1) as (tree,):
        pass
    (tree / ".git").unlink()
    (repo / "m.py").write_text("x = 1  # uncommitted\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("mine", encoding="utf-8")

    with _worktrees(repo, 1) as (again,):
        assert again == tree
        assert git(again, "rev-parse", "HEAD").strip() == git(repo, "rev-parse", "HEAD").strip()

    assert len(adds) == 2, "the second run added the tree back"
    assert text(repo / "m.py") == "x = 1  # uncommitted\n", "the user's edit survived"
    assert (repo / "untracked.txt").exists()


# --- what a failure leaves ---------------------------------------------------

def test_a_build_that_fails_leaves_no_pool_to_be_reused_blind(repo, monkeypatch):
    """Half a pool reused is silent, where a fresh add that dies is loud."""
    real = mutate_pool.worktree_add

    def refuse_the_second(root: Path, path: Path) -> None:
        if path.name == "w1":
            raise GitError("checkout refused")
        real(root, path)

    monkeypatch.setattr(mutate_pool, "worktree_add", refuse_the_second)

    with pytest.raises(GitError):
        with _worktrees(repo, 2):
            pass

    assert sorted(pool_dir(repo).glob("w*")) == []
    assert len(listed(repo)) == 1, "no admin entry for a tree that is not there"


def test_a_run_that_raises_keeps_the_pool_for_the_next_one(repo, adds):
    with pytest.raises(ValueError):
        with _worktrees(repo, 1):
            raise ValueError("the run blew up")

    with _worktrees(repo, 1) as (tree,):
        assert tree.is_dir()
    assert len(adds) == 1


# --- two runs at once --------------------------------------------------------

def test_a_second_run_finding_the_pool_held_works_outside_it(repo):
    """A fixed path is shared state, which `tempfile.mkdtemp` never was. The
    loser of the lock gets today's throwaway set: slower, never the first run's
    tree with a second run's mutant in it."""
    pool_dir(repo).mkdir(parents=True, exist_ok=True)
    handle = os.open(pool_dir(repo) / ".lock", os.O_CREAT | os.O_RDWR)
    assert _take_lock(handle), "the peer's lock"
    try:
        with _worktrees(repo, 1) as (tree,):
            assert pool_dir(repo) not in tree.parents
            assert tree.is_dir()
        assert not tree.exists(), "the throwaway set is still removed on the way out"
        assert sorted(pool_dir(repo).glob("w*")) == [], "the pool was left alone"
    finally:
        os.close(handle)


def test_the_lock_is_released_so_the_next_run_gets_the_pool(repo, adds):
    with _worktrees(repo, 1):
        pass
    with _worktrees(repo, 1) as (tree,):
        assert tree.parent == pool_dir(repo)
    assert len(adds) == 1


# --- removing it -------------------------------------------------------------

def test_drop_pool_takes_the_admin_entries_with_the_directories(repo):
    """rmtree alone leaves an entry per abandoned checkout in `git worktree
    list`, and they accumulate for the life of the repo."""
    with _worktrees(repo, 2) as trees:
        asked = list(trees)
    assert len(listed(repo)) == 3

    dropped = drop_pool(repo)

    assert sorted(dropped) == sorted(asked)
    assert listed(repo) == listed(repo)[:1]
    assert not pool_dir(repo).exists()


def test_drop_pool_on_a_repo_that_never_pooled_says_nothing_was_there(repo):
    assert drop_pool(repo) == []
    assert len(listed(repo)) == 1


# --- one worker still touches nothing ----------------------------------------

def test_one_worker_builds_no_pool_at_all(repo, monkeypatch):
    """`mutation_workers = 1` mutates the live tree, exactly as it always has:
    the saving is 0 s there and so is the cost."""
    monkeypatch.setattr(mutate_pool, "run_one", lambda tree, cfg, mutant: True)
    cfg = type("Cfg", (), {"mutation_workers": 1, "mutation_command": "true",
                           "mutation_timeout_seconds": 5})()
    mutant = type("M", (), {"path": "m.py", "line": 1, "op": "x"})()

    assert mutate_pool.run_mutants(repo, cfg, [mutant], lambda *a: None) == [True]

    assert not (repo / ".crapkit").exists()
