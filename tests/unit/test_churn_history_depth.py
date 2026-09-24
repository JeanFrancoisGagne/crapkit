"""History depth in the churn map, churn log and coupling keys.

A sha pins which history HEAD reaches, not how much of it a clone holds.
`git fetch --deepen` rewrites git's shallow boundary and `--unshallow` deletes
it while HEAD stays put, so the three caches key on that boundary too. These
tests read real clones for the depth itself and fake only the log for the
carry rule, the same seam test_churn_log.py fakes.
"""
import json
import subprocess
from pathlib import Path

import pytest

from crapkit import churn_cache, churn_log, coupling_cache

HEAD = "cafe1234" * 5


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture(scope="module")
def history(tmp_path_factory) -> Path:
    """Four commits, so a depth-1 clone can be deepened twice before it is whole."""
    source = tmp_path_factory.mktemp("history")
    _git(source, "init", "-q", "-b", "main")
    for i in range(4):
        (source / "f.txt").write_text(f"{i}\n", encoding="utf-8")
        _git(source, "add", "f.txt")
        _git(source, "commit", "-q", "-m", f"c{i}")
    return source


def _clone(history: Path, dest: Path, *depth: str) -> Path:
    subprocess.run(["git", "clone", "-q", *depth, history.as_uri(), str(dest)],
                   check=True, capture_output=True)
    return dest


def test_a_full_clone_reads_as_the_full_history(history, tmp_path):
    clone = _clone(history, tmp_path / "full")

    assert churn_log.history_depth(clone) == churn_log.FULL_HISTORY


def test_each_deepen_moves_the_depth_and_unshallow_ends_it(history, tmp_path):
    clone = _clone(history, tmp_path / "shallow", "--depth", "1")
    seen = [churn_log.history_depth(clone)]
    _git(clone, "fetch", "-q", "--deepen=1")
    seen.append(churn_log.history_depth(clone))
    _git(clone, "fetch", "-q", "--unshallow")
    seen.append(churn_log.history_depth(clone))

    assert seen[0].startswith("shallow-") and seen[1].startswith("shallow-")
    assert seen[0] != seen[1], "--deepen moved the boundary under the same HEAD"
    assert seen[2] == churn_log.FULL_HISTORY


def test_a_linked_worktree_reads_the_boundary_its_repository_keeps(history, tmp_path):
    """git keeps `shallow` in the common git directory, not in a worktree's own."""
    clone = _clone(history, tmp_path / "shallow", "--depth", "1")
    _git(clone, "worktree", "add", "-q", str(tmp_path / "linked"), "HEAD")

    assert churn_log.history_depth(tmp_path / "linked") == churn_log.history_depth(clone)
    assert churn_log.history_depth(clone) != churn_log.FULL_HISTORY


def test_a_git_dir_outside_the_tree_is_asked_of_git(history, tmp_path, monkeypatch):
    """No .git above the root: HEAD's fast path finds nothing, and neither
    would the boundary read, so git names the directory it uses."""
    clone = _clone(history, tmp_path / "shallow", "--depth", "1")
    tree = tmp_path / "tree"
    tree.mkdir()
    monkeypatch.setenv("GIT_DIR", str(clone / ".git"))
    churn_log._asked_common_dir.cache_clear()

    assert churn_log.history_depth(tree) == churn_log.history_depth(clone)


def test_a_root_git_places_nowhere_has_no_boundary(tmp_path, monkeypatch):
    for name in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    churn_log._asked_common_dir.cache_clear()

    assert churn_log.history_depth(tmp_path) == churn_log.FULL_HISTORY


def test_an_empty_boundary_file_lists_no_boundary(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".git" / "shallow").write_bytes(b"")

    assert churn_log.history_depth(tmp_path) == churn_log.FULL_HISTORY


@pytest.fixture()
def unreadable_boundary(tmp_path, monkeypatch) -> Path:
    """A boundary that is there and cannot be read: a directory where git
    keeps the file. Reading it raises an OSError that is not FileNotFoundError
    on every OS."""
    _git(tmp_path, "init", "-q")
    (tmp_path / ".git" / "shallow").mkdir()
    for module in (churn_log, churn_cache, coupling_cache):
        monkeypatch.setattr(module, "head_commit", lambda root: HEAD)
    return tmp_path


def test_an_unreadable_boundary_is_no_depth(unreadable_boundary):
    assert churn_log.history_depth(unreadable_boundary) is None


@pytest.mark.parametrize("key", [
    lambda root: churn_log._cache_key(root, 12),
    lambda root: churn_cache._cache_key(root, 12),
    lambda root: coupling_cache._cache_key(root, 12, ["src/a.py"]),
], ids=["churn-log", "churn-map", "coupling"])
def test_an_unreadable_boundary_keys_nothing(unreadable_boundary, key):
    """None reads as a cold cache: the answer is walked, never served."""
    assert key(unreadable_boundary) is None


@pytest.mark.parametrize("key", [
    lambda root: churn_log._cache_key(root, 12),
    lambda root: churn_cache._cache_key(root, 12),
    lambda root: coupling_cache._cache_key(root, 12, ["src/a.py"]),
], ids=["churn-log", "churn-map", "coupling"])
def test_every_history_key_holds_the_depth(history, tmp_path, key):
    clone = _clone(history, tmp_path / "shallow", "--depth", "1")

    assert key(clone)["depth"] == churn_log.history_depth(clone)


@pytest.mark.parametrize("key", [
    lambda root: churn_log._cache_key(root, 12),
    lambda root: churn_cache._cache_key(root, 12),
    lambda root: coupling_cache._cache_key(root, 12, ["src/a.py"]),
], ids=["churn-log", "churn-map", "coupling"])
def test_an_unreadable_head_keys_nothing_whatever_the_depth(history, tmp_path, key):
    """The control beside the depth: a HEAD git cannot name is no key either."""
    clone = _clone(history, tmp_path / "full")
    (clone / ".git" / "HEAD").write_text("ref: refs/heads/nowhere\n", encoding="utf-8")

    assert key(clone) is None


def block(author: str, at: int, *paths: str) -> list[str]:
    return [f"\x01{author}\x02{at}\x02{at}\n"] + [f"{p}\n" for p in paths]


SHALLOW_LOG = block("alice", 1000000900, "src/a.py")
FULL_LOG = SHALLOW_LOG + block("bob", 1000000500, "src/a.py") + block("carol", 1000000100, "src/a.py")


class FakeHistory:
    """The window as the clone's depth shows it; counts the walks."""

    def __init__(self):
        self.depth = "shallow-0001"
        self.walks = 0

    def window(self, root, months, *walked_from):
        self.walks += 1
        return iter(SHALLOW_LOG if self.depth.startswith("shallow-") else FULL_LOG)


@pytest.fixture()
def fake(monkeypatch) -> FakeHistory:
    history = FakeHistory()
    monkeypatch.setattr(churn_log, "_window_log", history.window)
    monkeypatch.setattr(churn_log, "_range_log", lambda root, base, head: iter(()))
    monkeypatch.setattr(churn_log, "_window_cutoff", lambda root, months: 999999999)
    monkeypatch.setattr(churn_log, "is_ancestor", lambda root, commit, other: True)
    monkeypatch.setattr(churn_log, "head_commit", lambda root: HEAD)
    monkeypatch.setattr(churn_log, "history_depth", lambda root: history.depth)
    return history


def test_a_log_cut_at_another_depth_is_walked_again_not_carried(tmp_path, fake):
    """Same HEAD, same day, same window: only the depth moved. Carrying the
    shallow log forward would add no commit, since HEAD grew from itself."""
    assert list(churn_log.log_lines(tmp_path, 12)) == SHALLOW_LOG
    fake.depth = churn_log.FULL_HISTORY

    assert list(churn_log.log_lines(tmp_path, 12)) == FULL_LOG
    assert fake.walks == 2


def test_an_unmoved_depth_serves_the_laid_log(tmp_path, fake):
    list(churn_log.log_lines(tmp_path, 12))

    assert list(churn_log.log_lines(tmp_path, 12)) == SHALLOW_LOG
    assert fake.walks == 1


def test_a_key_written_before_depth_joined_it_answers_a_full_clone(tmp_path, fake):
    """0.8.0 wrote no depth. Nearly every such log was laid by a full clone,
    so it keeps serving one instead of costing every upgrade a walk."""
    fake.depth = churn_log.FULL_HISTORY
    list(churn_log.log_lines(tmp_path, 12))
    key_path = churn_log._key_path(tmp_path / ".crapkit" / churn_log.LOG_NAME)
    doc = json.loads(key_path.read_text(encoding="utf-8"))
    del doc["depth"]
    key_path.write_text(json.dumps(doc), encoding="utf-8")

    assert list(churn_log.log_lines(tmp_path, 12)) == FULL_LOG
    assert fake.walks == 1
    fake.depth = "shallow-0002"
    list(churn_log.log_lines(tmp_path, 12))
    assert fake.walks == 2, "a shallow clone never takes a depthless log as its own"
