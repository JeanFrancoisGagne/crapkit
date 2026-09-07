"""Per-file churn from git history. Pure parser; the shell supplies the log text.

The log format is one \\x01-prefixed author line per commit followed by the
commit's file paths (git log --format=%x01%an --name-only).
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple

from .gitpaths import history_line, unquote_path


class FileChurn(NamedTuple):
    commits: int
    authors: int
    weight: float = 0.0  # recency-weighted commit sum; commit count when untimestamped


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
    import math

    if newest == oldest:
        return 1.0
    t_norm = (ts - oldest) / (newest - oldest)
    return 1.0 / (1.0 + math.exp(-12.0 * t_norm + 12.0))


def _split_author(line: str) -> tuple[str, int | None]:
    body = line[1:]
    if "\x02" not in body:
        return body, None
    name, _, raw = body.partition("\x02")
    return name, int(raw) if raw.isdigit() else None


def _collect(lines: Iterable[str]):
    commits: dict[str, int] = {}
    authors: dict[str, set[str]] = {}
    stamps: dict[str, list[int]] = {}
    author, ts = None, None
    for raw in lines:
        line = history_line(raw)
        if not line:
            continue
        if line.startswith("\x01"):
            author, ts = _split_author(line)
            continue
        if author is None:
            continue
        path = unquote_path(line)
        commits[path] = commits.get(path, 0) + 1
        authors.setdefault(path, set()).add(author)
        if ts is not None:
            stamps.setdefault(path, []).append(ts)
    return commits, authors, stamps


def parse_git_log(text: str) -> dict[str, FileChurn]:
    """Whole-text entrypoint: the log already in hand."""
    return parse_git_log_lines(text.split("\n"))


def parse_git_log_lines(lines: Iterable[str]) -> dict[str, FileChurn]:
    """Streaming entrypoint: one commit block resident at a time, whatever the log's size."""
    commits, authors, stamps = _collect(lines)
    all_stamps = [s for lst in stamps.values() for s in lst]
    oldest, newest = (min(all_stamps), max(all_stamps)) if all_stamps else (0, 0)

    def weight(path: str, count: int) -> float:
        if path not in stamps:
            return float(count)  # untimestamped log: degrade to commit count
        return round(sum(_twr(s, oldest, newest) for s in stamps[path]), 4)

    return {p: FileChurn(commits=c, authors=len(authors[p]), weight=weight(p, c))
            for p, c in commits.items()}
