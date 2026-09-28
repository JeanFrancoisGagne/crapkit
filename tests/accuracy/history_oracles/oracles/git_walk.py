"""The churn window walked with git's numstat records, and the churn a reader of
the docs computes from them. No crapkit.

What churn is, from the docs:
- README.md#risk-what-ranks-the-worklist: a file's weight sums, over its commits
  in the churn window, a logistic weight that rises to 0.5 for the newest commit
  and falls to near zero for the oldest. When every commit shares one timestamp
  there is no range, and each commit counts once.
- CONTEXT.md#the-worklist ("Churn window"): the months are counted back from
  HEAD's commit date, never from today's; a commit counts while its commit
  date is at or after the window's cutoff; its recency weight reads the author
  date. README.md#risk-what-ranks-the-worklist: so a fixed tree ranks
  identically forever.
- docs/agent-json.md#next-item: `commits` and `authors` are the file's churn in
  the window.

How this walk reads git, and where it departs from crapkit's reader on purpose:
- The window is what `git log --since="<months> months ago"` lists: git's own
  reading of the months, on a clock GIT_TEST_DATE_NOW sets to the walked
  commit's commit date. The clock a test runs crapkit on plays no part.
- `--numstat -z`: git prints every path verbatim, never C-quoted (git-log docs
  on -z; git-config core.quotePath). An earlier walk read numstat without -z
  and missed every non-ASCII path, because git quoted it; that was the oracle's
  fault (ruling H1), and this walk no longer can.
- A rename is one numstat record naming both paths. It counts under the new
  path, where `git log --numstat` reports the change. A merge prints no record.
- The walk runs at the git top and keeps the paths under the crapkit root, cut
  to root-relative. It does not use --relative, the flag crapkit reads with.
- `authors` counts distinct author names (%an). The recency range runs from the
  oldest to the newest author date among the window's commits that change a
  file under the root (ruling H5 pins that reading).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
import re

from accuracy.kit import exact
from .history_git import git, text, top_and_prefix, under

HEADER = "%x01%H%x02%an%x02%ae%x02%at%x02%ct"
_RECORD = re.compile(rb"\A(-|\d+)\t(-|\d+)\t(.*)\Z", re.S)


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    email: str
    at: int  # author date: the recency weight reads it
    ct: int  # commit date: the window cutoff reads it
    paths: tuple[str, ...] = ()
    renames: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Churn:
    commits: int
    authors: int
    weight: Decimal  # half-even to 4 places, as a report prints it


@dataclass
class _Open:
    header: bytes
    paths: list = field(default_factory=list)
    renames: list = field(default_factory=list)


def _commit(block: _Open) -> Commit:
    sha, author, email, at, ct = block.header[1:].decode("utf-8").split("\x02")
    return Commit(sha, author, email, int(at), int(ct), tuple(block.paths), tuple(block.renames))


def _record(tokens: list[bytes], i: int, block: _Open) -> int:
    """One numstat record at tokens[i]; a rename spends the two path tokens after it."""
    match = _RECORD.match(tokens[i])
    if match is None:
        raise AssertionError(f"not a numstat record: {tokens[i]!r}")
    if match.group(3):
        block.paths.append(match.group(3).decode("utf-8"))
        return i + 1
    old, new = tokens[i + 1].decode("utf-8"), tokens[i + 2].decode("utf-8")
    block.paths.append(new)
    block.renames.append((old, new))
    return i + 3


def parse(out: bytes) -> list[Commit]:
    """The commits of one `git log --numstat -z --format=HEADER` answer, in log order."""
    tokens, blocks, i = out.split(b"\0"), [], 0
    while i < len(tokens):
        token = tokens[i].removeprefix(b"\n")
        tokens[i] = token
        if token.startswith(b"\x01"):
            blocks.append(_Open(token))
            i += 1
        elif not token:
            i += 1
        else:
            i = _record(tokens, i, blocks[-1])
    return [_commit(block) for block in blocks]


def _cut(paths, prefix: str) -> tuple:
    return tuple(path for path in (under(p, prefix) for p in paths) if path is not None)


def _rooted(commit: Commit, prefix: str) -> Commit:
    """The commit with only its paths under the root, cut to root-relative. A
    rename stays when its new path lies under the root; its old path reads None
    when it came from above the root."""
    renames = tuple((under(old, prefix), under(new, prefix)) for old, new in commit.renames)
    return Commit(commit.sha, commit.author, commit.email, commit.at, commit.ct,
                  _cut(commit.paths, prefix), tuple(pair for pair in renames if pair[1]))


def commit_date(root: Path, rev: str = "HEAD") -> int:
    """`rev`'s commit date (%ct): the moment the window counts back from."""
    return int(text(root, "log", "-1", "--format=%ct", rev))


def walk(root: Path, months: int | None, rev: str = "HEAD") -> list[Commit]:
    """The window's commits at `rev`, newest first, their paths cut to `root`.
    `months` None lists every commit: a window that reaches back past 1970."""
    top, prefix = top_and_prefix(root)
    if months is None:
        cut, now = (), None
    else:
        cut, now = (f"--since={months} months ago",), commit_date(top, rev)
    out = git(top, "-c", "core.quotePath=true", "log", *cut,
              "--numstat", "-z", f"--format={HEADER}", rev, "--", now=now)
    return [_rooted(commit, prefix) for commit in parse(out)]


def span(commits: list[Commit]) -> tuple[int, int] | None:
    """The oldest and newest author date among commits that change a file (H5)."""
    dated = [commit.at for commit in commits if commit.paths]
    return (min(dated), max(dated)) if dated else None


def churn(commits: list[Commit]) -> dict[str, Churn]:
    """{path: Churn} over the window's commits."""
    stamps, names = defaultdict(list), defaultdict(set)
    for commit in commits:
        for path in set(commit.paths):
            stamps[path].append(commit.at)
            names[path].add(commit.author)
    bounds = span(commits)
    return {path: Churn(len(stamps[path]), len(names[path]), _weight(stamps[path], bounds))
            for path in stamps}


def _weight(stamps: list[int], bounds: tuple[int, int]) -> Decimal:
    return exact.half_even(exact.churn_weight(stamps, *bounds), 4)


def window_authors(commits: list[Commit]) -> set[str]:
    """The author names of the window's commits that change a file."""
    return {commit.author for commit in commits if commit.paths}
