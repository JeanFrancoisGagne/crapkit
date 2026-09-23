"""Which paths moved since a lane's stamp, read from git with every read started at once.

Lane staleness and lane reuse ask git the same questions: is the stamp's commit
still behind HEAD, which files changed in the commits since, which are staged
or edited, and which are new and untracked. Each answer is one git process.
Asked one after another they cost the sum of all of them; started together they
cost about the slowest one.

Every read also takes the paths in question as a pathspec. `ls-files --others`
over a large untracked tree that no lane reads (drafts, output nobody ignored)
was most of the cost, and none of its answer could change a verdict.
"""
from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from .errors import GitError

_NAMES = ("--name-only", "--no-renames", "-z")


def _start(root: Path, *args: str):
    """One git read, started now and collected later.

    gitio owns how crapkit spawns git: the diff.relative and core.quotePath
    flags and the display state it strips. `--literal-pathspecs` makes every
    path a path, so a scope named `src/[id]` is not read as a glob."""
    from .gitio import start_read

    return start_read(root, "--literal-pathspecs", *args)


def _names(out: bytes) -> tuple[str, ...]:
    """NUL records, decoded without newline conversion, quoting or trimming."""
    return tuple(name for name in out.decode("utf-8").split("\0") if name)


def visible_paths(root: Path, paths) -> tuple[str, ...]:
    """The tracked and untracked files under `paths`, ignored ones left out:
    every file whose change ChangeReads can report. No paths asks git nothing."""
    if not paths:
        return ()
    read = _start(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z",
                  "--", *paths)
    return _names(read.result())


class ChangeReads:
    """Git's answers for some stamp commits, narrowed to `paths`, all started at once.

    Answers the three questions lane staleness asks a GitFacts (is_ancestor,
    diff_names_since, status_names). The commits it was built for are read
    from the start; any other commit is started when first asked about, since a
    concurrent run can rewrite the stamps between two reads of them. With no
    paths nothing can change under them, so no diff or status read starts at
    all. Use it as a context manager: on the way out it waits for every read
    nobody collected and drops the answer. None is killed: a worktree `git diff`
    refreshes the index under .git/index.lock when tracked files are stat-dirty,
    and one killed mid-refresh leaves the lock behind, after which every `git
    add` and commit in the checkout fails.
    """

    def __init__(self, root: Path, commits, paths) -> None:
        self._root, self._spec, self._paths = root, ("--", *paths), bool(paths)
        self._uncollected: set = set()
        self._ancestry: dict = {}
        self._diffs: dict = {}
        self._answers: dict = {}
        try:
            self._status = self._start_all(commits)
        except GitError:
            self.close()
            raise

    def _start_all(self, commits) -> tuple:
        for commit in commits:
            self._ancestor_read(commit)
            self._diff_read(commit)
        if not self._paths:
            return ()
        return (self._begin("diff", *_NAMES, "--cached", *self._spec),
                self._begin("diff", *_NAMES, *self._spec),
                self._begin("ls-files", "--others", "--exclude-standard", "-z", *self._spec))

    def _begin(self, *args: str):
        read = _start(self._root, *args)
        self._uncollected.add(read)
        return read

    def _collect(self, read) -> bytes:
        """The read's answer. Each read is collected once, here or in close()."""
        self._uncollected.discard(read)
        return read.result()

    def _succeeds(self, read) -> bool:
        """`merge-base --is-ancestor` answers in its exit code alone: 0 is yes, and
        anything else, a commit this clone does not hold included, is no."""
        try:
            self._collect(read)
        except GitError:
            return False
        return True

    def _ancestor_read(self, commit: str):
        if commit not in self._ancestry:
            self._ancestry[commit] = self._begin("merge-base", "--is-ancestor", commit, "HEAD")
        return self._ancestry[commit]

    def _diff_read(self, commit: str):
        if self._paths and commit not in self._diffs:
            self._diffs[commit] = self._begin("diff", *_NAMES, commit, "HEAD", *self._spec)
        return self._diffs.get(commit)

    def _once(self, key, answer):
        if key not in self._answers:
            self._answers[key] = answer()
        return self._answers[key]

    def is_ancestor(self, commit: str) -> bool:
        return self._once(("ancestor", commit),
                          lambda: self._succeeds(self._ancestor_read(commit)))

    def diff_names_since(self, commit: str) -> tuple[str, ...]:
        read = self._diff_read(commit)
        return self._once(("diff", commit), lambda: _names(self._collect(read)) if read else ())

    def status_names(self) -> tuple[str, ...]:
        return self._once("status", lambda: tuple(sorted(
            {name for read in self._status for name in _names(self._collect(read))})))

    def changed_since(self, commit: str) -> tuple[str, ...]:
        """Committed, staged, unstaged or untracked: every change under the paths."""
        return tuple(sorted({*self.diff_names_since(commit), *self.status_names()}))

    def close(self) -> None:
        for read in tuple(self._uncollected):
            with suppress(GitError):
                self._collect(read)

    def __enter__(self) -> ChangeReads:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
