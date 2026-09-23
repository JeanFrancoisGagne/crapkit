"""Per-file churn from git history. Pure parser; the shell supplies the log text.

The log format is one \\x01-prefixed author line per commit followed by the
commit's file paths (git log --format=%x01%an --name-only). A header may carry
the author date after a \\x02 (%at), and the stored log adds the commit date
after another (%ct); the parser reads both shapes.

Churn is a pure function of the window's commits (`WindowCommits`): who wrote
each one, its two dates, and which paths it touched. A table carried to a new
HEAD, the new commits folded in on top and the aged-out ones expired, answers
what parsing the whole window again would.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator
from typing import NamedTuple

from .gitpaths import history_line, unquote_path


class FileChurn(NamedTuple):
    commits: int
    authors: int
    weight: float = 0.0  # recency-weighted commit sum; commit count when untimestamped


class Commit(NamedTuple):
    author: int  # index into WindowCommits.authors
    at: int | None  # author date: what the recency weight reads
    ct: int | None  # commit date: what --since, and so expiry, reads


def _twr(ts: int, oldest: int, newest: int) -> float:
    """Time-weighted recency: a logistic over the commit's position in the log,
    0.5 at the newest commit and near nothing at the oldest.

    One timestamp is no range. The newest and oldest commit coincide, and a
    span clamped to one second weighed every commit at 1/(1+e^12), which
    rounds to nothing: every row on a one-commit repo read `risk 0.0`, and an
    agent read ccn 10 at risk 0.0 as no risk. With nothing to weight against a
    commit counts once, the degrade an untimestamped log already gets, so the
    ranking is ccn times one until the history grows.
    """
    if newest == oldest:
        return 1.0
    t_norm = (ts - oldest) / (newest - oldest)
    return 1.0 / (1.0 + math.exp(-12.0 * t_norm + 12.0))


def _stamp(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


def _split_header(line: str) -> tuple[str, int | None, int | None]:
    """Author, author date and commit date off a header; a date it lacks is None."""
    name, _, dates = line[1:].partition("\x02")
    at, _, ct = dates.partition("\x02")
    return name, _stamp(at), _stamp(ct)


def _blocks(lines: Iterable[str]) -> Iterator[tuple[str | None, list[str]]]:
    """(header, paths) per commit in log order. Blank lines drop out, and paths
    ahead of the first header arrive under a None header.

    Only a quoted path goes through the unquoter: every other line already is
    the path, and the call cost more than the rest of the loop."""
    header, paths = None, []
    for raw in lines:
        line = history_line(raw)
        if line.startswith("\x01"):
            yield header, paths
            header, paths = line, []
        elif line:
            paths.append(unquote_path(line) if line[0] == '"' else line)
    yield header, paths


def _weight(seqs: list[int], weights: dict[int, float]) -> float:
    """The path's stamped commits summed exactly and rounded once. A plain sum
    depends on the order the commits arrive in, and a carried table lists a
    merged branch's commits where the range walk put them while git's own log
    interleaves them by date: at a rounding edge the two orders round apart."""
    stamped = [weights[seq] for seq in seqs if seq in weights]
    return round(math.fsum(stamped), 4) if stamped else float(len(seqs))


class WindowCommits:
    """The window's commits, newest first, and for every path the commits that
    touched it, newest first.

    A commit is named by a sequence number that only grows toward the present:
    a full parse numbers the newest 0 and counts down, and commits folded in on
    top continue upward. A path's numbers therefore always fall, which its
    expiry relies on. They follow git's log order only until a merge: a carried
    table lists a merged branch's commits above the ones they were merged over,
    where git's log interleaves them by date, so the weight is an exact sum no
    order can move. Commits that touched no path are not kept: they weigh
    nothing and date no range.
    """

    def __init__(self, authors: list[str], commits: dict[int, Commit],
                 files: dict[str, list[int]]):
        self.authors = authors
        self.commits = commits
        self.files = files

    @property
    def dated(self) -> bool:
        """Whether every commit carries its commit date, without which it cannot expire."""
        return all(commit.ct is not None for commit in self.commits.values())

    def churn(self) -> dict[str, FileChurn]:
        author = {seq: commit.author for seq, commit in self.commits.items()}.__getitem__
        weight = self._weigher()
        return {path: FileChurn(len(seqs), len(set(map(author, seqs))), weight(seqs))
                for path, seqs in self.files.items()}

    def _weigher(self) -> Callable[[list[int]], float]:
        """A path's weight from its commits, summed exactly (math.fsum) so the
        order they are listed in cannot move the rounding. Every commit git
        lists has an author date, and then each weight is one lookup per
        commit; an untimestamped header, which only a hand-written log carries,
        takes the path that skips it."""
        weights = self._weights()
        if len(weights) == len(self.commits):
            return lambda seqs: round(math.fsum(map(weights.__getitem__, seqs)), 4)
        return lambda seqs: _weight(seqs, weights)

    def _weights(self) -> dict[int, float]:
        stamped = {seq: commit.at for seq, commit in self.commits.items()
                   if commit.at is not None}
        if not stamped:
            return {}
        oldest, newest = min(stamped.values()), max(stamped.values())
        return {seq: _twr(at, oldest, newest) for seq, at in stamped.items()}

    def carry(self, newer: WindowCommits) -> None:
        """Fold `newer`, the commits HEAD added since this table, in on top."""
        shift = next(iter(self.commits), 0) + len(newer.commits)
        ids = self._author_ids(newer.authors)
        fresh = {seq + shift: Commit(ids[c.author], c.at, c.ct)
                 for seq, c in newer.commits.items()}
        self.commits = {**fresh, **self.commits}
        for path, seqs in newer.files.items():
            self.files[path] = [seq + shift for seq in seqs] + self.files.get(path, [])

    def _author_ids(self, names: list[str]) -> list[int]:
        """This table's id for each name, adding the names it has not seen."""
        ids = {name: i for i, name in enumerate(self.authors)}
        for name in names:
            ids.setdefault(name, len(ids))
        self.authors = list(ids)
        return [ids[name] for name in names]

    def expire(self, cutoff: int) -> None:
        """Drop every commit a `--since` walk would no longer list: the ones
        committed before `cutoff`. The commit date, not the author date:
        --since reads the committer's clock, and a rebased commit has two."""
        gone = {seq for seq, commit in self.commits.items() if _aged(commit, cutoff)}
        if gone:
            self._drop(gone)
            self._forget_authors()

    def _forget_authors(self) -> None:
        """Keep only the names a commit still carries, numbered in commit order
        the way a fold numbers them. A name whose last commit aged out goes, or
        a table that is only ever carried keeps every name since its last full
        rebuild."""
        ids: dict[int, int] = {}
        for commit in self.commits.values():
            ids.setdefault(commit.author, len(ids))
        if len(ids) < len(self.authors):
            self._renumber(ids)

    def _renumber(self, ids: dict[int, int]) -> None:
        self.authors = [self.authors[old] for old in ids]
        self.commits = {seq: commit._replace(author=ids[commit.author])
                        for seq, commit in self.commits.items()}

    def _drop(self, gone: set[int]) -> None:
        for seq in gone:
            del self.commits[seq]
        newest = max(gone)
        for path in [path for path, seqs in self.files.items() if seqs[-1] <= newest]:
            self._drop_from(path, gone)

    def _drop_from(self, path: str, gone: set[int]) -> None:
        kept = [seq for seq in self.files[path] if seq not in gone]
        if kept:
            self.files[path] = kept
        else:
            del self.files[path]


def _aged(commit: Commit, cutoff: int) -> bool:
    return commit.ct is None or commit.ct < cutoff


def fold(lines: Iterable[str]) -> WindowCommits:
    """The window's commits off a log, in either header shape."""
    ids: dict[str, int] = {}
    commits: dict[int, Commit] = {}
    files: dict[str, list[int]] = {}
    seq = 0
    for header, paths in _blocks(lines):
        if header is None or not paths:
            continue
        name, at, ct = _split_header(header)
        commits[seq] = Commit(ids.setdefault(name, len(ids)), at, ct)
        for path in paths:
            files.setdefault(path, []).append(seq)
        seq -= 1
    return WindowCommits(list(ids), commits, files)


def parse_git_log(text: str) -> dict[str, FileChurn]:
    """Whole-text entrypoint: the log already in hand."""
    return parse_git_log_lines(text.split("\n"))


def parse_git_log_lines(lines: Iterable[str]) -> dict[str, FileChurn]:
    """Streaming entrypoint: one commit block resident at a time, whatever the log's size."""
    return fold(lines).churn()
