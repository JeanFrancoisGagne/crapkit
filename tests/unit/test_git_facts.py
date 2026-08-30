"""GitFacts: the per-command answer to the three questions every lane asks.

Before it existed, `--reuse-unchanged` paid a `git status` and a `git diff` per
lane (twice per lane, counting the staleness warning) for byte-identical output.
These tests count the spawns through the real seam, with `git` itself stubbed.
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from crapkit import gitio
from crapkit.config import Lane
from crapkit.errors import GitError
from crapkit.gitio import GitFacts
from crapkit.lanes import lane_unchanged, write_stamps

SCOPE_PATHS = {"src": ("src",)}


@pytest.fixture()
def counted(monkeypatch) -> dict:
    """Every gitio call GitFacts can make, counted instead of spawned."""
    calls = {"head": 0, "status": 0, "diff": 0, "ancestor": 0}

    def bump(key, value):
        def stub(*_args):
            calls[key] += 1
            return value
        return stub

    monkeypatch.setattr(gitio, "head_commit", bump("head", "c0ffee1234567890"))
    monkeypatch.setattr(gitio, "status_names", bump("status", []))
    monkeypatch.setattr(gitio, "diff_names_since", bump("diff", []))
    monkeypatch.setattr(gitio, "is_ancestor", bump("ancestor", True))
    return calls


def test_head_commit_is_asked_once_however_often_it_is_read(tmp_path, counted):
    facts = GitFacts(tmp_path)
    assert facts.head_commit() == "c0ffee1234567890"
    assert facts.head_commit() == "c0ffee1234567890"
    assert counted["head"] == 1


def test_the_dirty_file_set_is_asked_once(tmp_path, counted):
    facts = GitFacts(tmp_path)
    assert facts.status_names() == ()
    assert facts.status_names() == ()
    assert counted["status"] == 1


def test_a_diff_is_cached_per_commit_not_globally(tmp_path, counted):
    facts = GitFacts(tmp_path)
    facts.diff_names_since("aaa")
    facts.diff_names_since("aaa")
    facts.diff_names_since("bbb")
    assert counted["diff"] == 2, "one spawn per distinct commit, not per call"


def test_a_git_failure_is_never_memoized(tmp_path, monkeypatch):
    """A cached failure would turn one bad moment into a whole command's verdict."""
    attempts = []

    def boom(_root):
        attempts.append(1)
        raise GitError("no HEAD")

    monkeypatch.setattr(gitio, "head_commit", boom)
    facts = GitFacts(tmp_path)
    for _ in range(2):
        with pytest.raises(GitError):
            facts.head_commit()
    assert len(attempts) == 2


def _stamped_lane(root: Path, name: str, artifact: str, commit: str) -> Lane:
    lane = Lane(name=name, command="", artifact=artifact, parser="istanbul", scopes=("src",))
    (root / artifact).write_text("{}", encoding="utf-8")
    write_stamps(root, {artifact: {"commit": commit, "lane": name, "seconds": 1.0}})
    return lane


def test_two_lanes_sharing_one_context_ask_git_once_each(tmp_path, counted):
    """The finding: three git commands, once per lane. Two lanes stamped at the
    same commit now cost one status, one diff and one ancestry check between them."""
    first = _stamped_lane(tmp_path, "unit", "a.json", "beef" * 10)
    second = _stamped_lane(tmp_path, "py", "b.json", "beef" * 10)
    facts = GitFacts(tmp_path)

    assert lane_unchanged(tmp_path, first, SCOPE_PATHS, facts) is True
    assert lane_unchanged(tmp_path, second, SCOPE_PATHS, facts) is True

    assert counted["status"] == 1
    assert counted["diff"] == 1
    assert counted["ancestor"] == 1, "one stamp commit, one answer"


def test_ancestry_is_cached_per_commit_not_globally(tmp_path, counted):
    """The same shape the diffs have: a verify asks about the baseline commit,
    every open claim's commit and every lane stamp, and repeats are the rule."""
    facts = GitFacts(tmp_path)

    facts.is_ancestor("aaa")
    facts.is_ancestor("aaa")
    facts.is_ancestor("bbb")

    assert counted["ancestor"] == 2, "one spawn per distinct commit, not per call"


def test_ancestry_against_a_named_commit_is_a_different_question(tmp_path, counted):
    """`verify --base` asks whether a run sits behind the FORK POINT, not behind
    HEAD. The two answers differ mid-branch, so they cannot share an entry."""
    facts = GitFacts(tmp_path)

    facts.is_ancestor("aaa")
    facts.is_ancestor("aaa", "fork")

    assert counted["ancestor"] == 2


def test_a_lane_without_a_context_still_answers_on_its_own(tmp_path, counted):
    """Direct callers (and the tests above) must not have to build one."""
    lane = _stamped_lane(tmp_path, "unit", "a.json", "beef" * 10)
    assert lane_unchanged(tmp_path, lane, SCOPE_PATHS) is True
    assert counted["status"] == 1


def test_four_lanes_asking_at_the_same_instant_still_spawn_git_once(tmp_path, monkeypatch):
    """Parallel lanes share one context, so the lazy fill has to hold a lock:
    four threads arriving together must not each pay for the same answer."""
    spawns = []
    barrier = threading.Barrier(4)

    def slow_status(_root):
        spawns.append(1)
        time.sleep(0.05)
        return []

    monkeypatch.setattr(gitio, "status_names", slow_status)
    facts = GitFacts(tmp_path)

    def ask(_):
        barrier.wait(timeout=10)
        return facts.status_names()

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(ask, range(4))) == [()] * 4
    assert len(spawns) == 1


def test_write_stamps_merges_into_what_is_already_recorded(tmp_path):
    write_stamps(tmp_path, {"a.json": {"commit": "aaa", "lane": "unit", "seconds": 3.0}})
    write_stamps(tmp_path, {"b.json": {"commit": "bbb", "lane": "py", "seconds": 4.0}})
    stamps = json.loads((tmp_path / ".crapkit" / "artifacts.json").read_text(encoding="utf-8"))
    assert sorted(stamps) == ["a.json", "b.json"]


def test_write_stamps_records_nothing_for_a_lane_that_reused_its_artifact(tmp_path):
    write_stamps(tmp_path, {"a.json": {}})
    assert not (tmp_path / ".crapkit" / "artifacts.json").exists()


# --- every reader spells a non-ASCII path the way ls-files does ------------

NON_ASCII = "src/bêta.py"


def _git(repo: Path, *args: str) -> str:
    import subprocess
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout.strip()


@pytest.fixture()
def quoted_repo(tmp_path: Path) -> tuple:
    """A repo with core.quotePath ON and one non-ASCII file changed twice.

    The file is committed once, edited in a second commit, then edited again in
    the working tree, so the committed readers and the dirty readers both have
    something to name. quotePath is pinned true rather than left to the default
    because that is the setting crapkit's own flag has to beat.
    """
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "core.quotePath", "true")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    src = tmp_path / "src"
    src.mkdir()
    (src / "bêta.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (src / "plain.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "one")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (src / "bêta.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "two")
    (src / "bêta.py").write_text("def a():\n    return 3\n", encoding="utf-8")
    return tmp_path, base


def _churn_paths(root: Path, base: str) -> list[str]:
    """The path rows of the churn log, without its author/timestamp headers."""
    return [line.strip() for line in gitio.churn_log_lines(root, 12)
            if line.strip() and not line.startswith("\x01")]


_PATH_READERS = {
    "status_names": lambda root, base: gitio.status_names(root),
    "unstaged_paths": lambda root, base: gitio.unstaged_paths(root),
    "diff_names_since": lambda root, base: gitio.diff_names_since(root, base),
    "churn_log_lines": _churn_paths,
}


@pytest.mark.parametrize("reader", sorted(_PATH_READERS))
def test_a_reader_names_a_non_ascii_path_the_way_ls_files_spells_it(quoted_repo, reader):
    r"""Under git's default quoting these readers answer `"src/b\303\252ta.py"`,
    which no ls-files row equals, so a dirty file drops out of every set built by
    intersecting them and lane reuse republishes a stale score."""
    root, base = quoted_repo
    tracked = set(gitio.ls_files(root))
    assert NON_ASCII in tracked

    named = set(_PATH_READERS[reader](root, base))

    assert NON_ASCII in named
    assert named <= tracked


def test_a_diff_header_names_a_non_ascii_path_unquoted(quoted_repo):
    """diffparse reads the file it is about off this line, so a quoted header
    attributes changed lines to a path nothing else in crapkit knows."""
    root, base = quoted_repo
    headers = [line for line in gitio.diff_since(root, base).splitlines()
               if line.startswith("diff --git")]
    assert headers == [f"diff --git a/{NON_ASCII} b/{NON_ASCII}"]
