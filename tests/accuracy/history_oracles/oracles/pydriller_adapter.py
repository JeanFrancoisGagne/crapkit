"""PyDriller 2.12 as a second walker of the churn window, and its own process
metrics. No crapkit.

walk() reads each commit PyDriller traverses (GitPython, not git's numstat)
into git_walk.Commit records, so git_walk.churn computes churn from
PyDriller's reading of the history. Read as PyDriller documents its fields:
- modified_files is empty for a merge commit;
- new_path is None for a deleted file, so a deletion keeps its old path;
- a path comes back in the OS's spelling (str(Path)): it is read with '/';
- paths are top-relative and are cut to the crapkit root here.

commits_count() and contributors_count() are PyDriller's process metrics as it
ships them. They differ from crapkit on purpose in two named ways, each a
ruling with a hand case: CommitsCount folds a renamed file's earlier commits
into its newest name (H2), and ContributorsCount tells contributors apart by
e-mail address and drops a file whose changes add and delete no line (H3).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .git_walk import Commit, commit_date
from .history_git import top_and_prefix, under

FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)


def since(root: Path, months: int) -> datetime:
    """`months` calendar months before HEAD's commit date, the window's start."""
    from dateutil.relativedelta import relativedelta

    return datetime.fromtimestamp(commit_date(root), timezone.utc) - relativedelta(months=months)


def _posix(path: str | None) -> str | None:
    return None if path is None else path.replace("\\", "/")


def _path(modified) -> str:
    return _posix(modified.new_path) or _posix(modified.old_path)


def _commit(commit, prefix: str) -> Commit:
    paths = [under(_path(modified), prefix) for modified in commit.modified_files]
    return Commit(commit.hash, commit.author.name, commit.author.email,
                  int(commit.author_date.timestamp()), int(commit.committer_date.timestamp()),
                  tuple(path for path in paths if path is not None))


def walk(root: Path, months: int) -> list[Commit]:
    """The window's commits as PyDriller reads them, their paths cut to `root`."""
    from pydriller import Repository

    top, prefix = top_and_prefix(root)
    repo = Repository(str(top), since=since(top, months), to=FAR - timedelta(days=1))
    return [_commit(commit, prefix) for commit in repo.traverse_commits()]


def _metric(kind, root: Path, months: int) -> dict[str, int]:
    counts = kind(str(root), since=since(root, months), to=FAR).count()
    return {_posix(path): count for path, count in counts.items()}


def commits_count(root: Path, months: int) -> dict[str, int]:
    from pydriller.metrics.process.commits_count import CommitsCount

    return _metric(CommitsCount, root, months)


def contributors_count(root: Path, months: int) -> dict[str, int]:
    from pydriller.metrics.process.contributors_count import ContributorsCount

    return _metric(ContributorsCount, root, months)
