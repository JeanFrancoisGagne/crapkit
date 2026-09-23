"""A worktree teardown whose git failed to start still falls back and never raises.

worktree_remove promises it never raises, so a cleanup error cannot hide the
failure that caused the cleanup. An owned git that exits 0xC0000142 on Windows
raises the start failure, a ToolError, where it once came back as a code the
teardown read as a GitError. The fallback, rmtree and then a quiet prune, has
to cover that failure as well.
"""
from pathlib import Path

from crapkit import gitio, procs

DLL_INIT_FAILED = 0xC0000142


def _git_fails_to_start(monkeypatch):
    calls = []

    def run_owned(argv, *args, **kwargs):
        calls.append(argv[argv.index("worktree"):])
        procs._refuse_failed_start(DLL_INIT_FAILED)  # what an owned run raises for that exit

    monkeypatch.setattr(procs, "run_owned", run_owned)
    return calls


def test_a_remove_whose_git_failed_to_start_falls_back_without_raising(monkeypatch, tmp_path):
    calls = _git_fails_to_start(monkeypatch)
    tree = tmp_path / "trees" / "w1"
    tree.mkdir(parents=True)
    (tree / "edited.py").write_text("x = 1\n", encoding="utf-8")

    gitio.worktree_remove(tmp_path, tree, owner=object())

    assert not tree.exists(), "the rmtree fallback must still run"
    assert calls == [["worktree", "remove", "--force", str(tree)], ["worktree", "prune"]]


def test_a_prune_whose_git_failed_to_start_stays_quiet(monkeypatch):
    calls = _git_fails_to_start(monkeypatch)
    gitio._prune_quietly(Path("repo"), owner=object())
    assert calls == [["worktree", "prune"]]
