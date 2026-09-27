"""Every history walk, judged at a `git mv`.

Q87 made the marks file's history follow a rename: its log walked no renames,
so after `git mv crapkit-ratchet.tsv debt.tsv` every mark read 0 days old
(tests/unit/test_marks_history.py and test_shallow_history_is_named.py). The
other walks over git's or the store's history, each at a rename:

- verify's stand-in for a deleted marks file walks the commits that touched
  the new name, and the rename commit holds the marks: it follows.
- `explain --history` reads `git log -L`, which follows the lines across a
  source file's rename.
- churn counts `git log --name-only` per path, and explain's trajectory reads
  the store's rows per path: both start a renamed source file's history at
  the rename. They rank and report rather than judge a policy, and changing
  either moves every renamed file's numbers, so each waits for its own ruling;
  a strict xfail marks where each one stands.
"""
from __future__ import annotations

import os
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from crapkit.marks_history import newest_committed_marks
from crapkit.ratchet import KEY_VERSION, RatchetEntry, dump_ratchet, metric_version
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

MARKS = "crapkit-ratchet.tsv"
KNOT = "def f(a):\n    if a:\n        return {n}\n    return 0\n"


def git(root: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ)
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                           "-c", "commit.gpgsign=false", *args], cwd=root, check=True,
                          capture_output=True, text=True, encoding="utf-8", env=env).stdout.strip()


def commit(root: Path, message: str, date: str | None = None) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message, date=date)
    return git(root, "rev-parse", "HEAD")


def write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path


def test_verify_stands_in_with_the_marks_the_rename_commit_holds(repo):
    """Marks seeded, the file renamed, then deleted: the newest commit that
    held marks under the new name is the rename itself."""
    base = commit(repo, "base")
    write(repo, MARKS, dump_ratchet([RatchetEntry("src/a.py", "hot( n )", 12.0)],
                                    stamp=metric_version(), key_version=KEY_VERSION))
    commit(repo, "seed")
    git(repo, "mv", MARKS, "debt.tsv")
    renamed = commit(repo, "rename the marks file")
    (repo / "debt.tsv").unlink()
    commit(repo, "delete the marks file")

    held, marks = newest_committed_marks(repo, base, "debt.tsv")

    assert held == renamed
    assert [(e.path, e.long_name, e.crap) for e in marks.entries] == [("src/a.py", "hot( n )", 12.0)]


def test_explain_history_lists_the_commits_before_a_source_rename(repo):
    from crapkit.cli.reports import _function_commits

    write(repo, "src/a.py", KNOT.format(n=1))
    commit(repo, "first")
    write(repo, "src/a.py", KNOT.format(n=2))
    commit(repo, "second")
    git(repo, "mv", "src/a.py", "src/b.py")
    commit(repo, "rename")

    subjects = [c["subject"] for c in _function_commits(repo, "src/b.py", 1, 4)]

    assert subjects == ["second", "first"]


@pytest.mark.xfail(strict=True, reason="churn counts commits per path; a renamed file's churn "
                                       "restarts at the rename (open, needs a ruling)")
def test_churn_counts_a_renamed_files_commits_before_the_rename(repo):
    from crapkit.churn_cache import load_churn

    for n in (1, 2, 3):
        write(repo, "src/a.py", KNOT.format(n=n))
        commit(repo, f"edit {n}")
    git(repo, "mv", "src/a.py", "src/b.py")
    commit(repo, "rename")

    assert load_churn(repo, 12)["src/b.py"].commits == 4


def _row(path: str) -> ScoredRow:
    return ScoredRow("src", path, "f( a )", 1, 4, 2, 2, 2, 4, 1, 1, 0.0, "untested", 6.0,
                     "add-tests", 0, 1)


@pytest.mark.xfail(strict=True, reason="the store keys a function's runs by path; a renamed "
                                       "file's trajectory restarts at the rename (open, needs a "
                                       "ruling)")
def test_explain_trajectory_carries_the_runs_before_a_source_rename(repo):
    write(repo, "src/a.py", KNOT.format(n=1))
    before = commit(repo, "first")
    git(repo, "mv", "src/a.py", "src/b.py")
    after = commit(repo, "rename")
    (repo / ".crapkit").mkdir()
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    with closing(store._conn):
        for sha, path in ((before, "src/a.py"), (after, "src/b.py")):
            store.write_run(commit=sha, tool_versions={"crapkit": "0", "lizard": "0"},
                            rows=[_row(path)], lanes={}, kind="coverage")

        runs = store.function_history("src/b.py", "f( a )")

    assert len(runs) == 2
