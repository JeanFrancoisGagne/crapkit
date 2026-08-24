"""Per-file churn from git history. Pure parser; the shell supplies the log text.

The log format is one \\x01-prefixed author line per commit followed by the
commit's file paths (git log --format=%x01%an --name-only).
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple


class FileChurn(NamedTuple):
    commits: int
    authors: int
    weight: float = 0.0  # recency-weighted commit sum; commit count when untimestamped


_SIMPLE_ESCAPES = {"n": 10, "t": 9, "r": 13, '"': 34, "\\": 92, "a": 7, "b": 8, "f": 12, "v": 11}


def _is_quoted(line: str) -> bool:
    return len(line) >= 2 and line.startswith('"') and line.endswith('"')


def _escape_at(body: str, i: int) -> tuple[bytes, int]:
    """Decode the escape starting at the backslash on `i`; return its bytes and the next index."""
    nxt = body[i + 1] if i + 1 < len(body) else ""
    if nxt.isdigit():
        return bytes([int(body[i + 1:i + 4], 8)]), i + 4
    if nxt in _SIMPLE_ESCAPES:
        return bytes([_SIMPLE_ESCAPES[nxt]]), i + 2
    return nxt.encode("utf-8"), i + 2


def _unquote_git_path(line: str) -> str:
    """Undo git's core.quotePath C-style quoting so churn keys match ls-files paths.

    Quoted lines are wrapped in double quotes with backslash escapes; non-ASCII
    bytes appear as literal octal text (\\303\\251). Decoded octal bytes are
    UTF-8.
    """
    if not _is_quoted(line):
        return line
    body = line[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out += ch.encode("utf-8")
            i += 1
            continue
        chunk, i = _escape_at(body, i)
        out += chunk
    return out.decode("utf-8", errors="replace")


def _twr(ts: int, oldest: int, newest: int) -> float:
    import math

    span = max(newest - oldest, 1)
    t_norm = (ts - oldest) / span
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
        line = raw.strip()
        if not line:
            continue
        if line.startswith("\x01"):
            author, ts = _split_author(line)
            continue
        if author is None:
            continue
        path = _unquote_git_path(line).replace("\\", "/")
        commits[path] = commits.get(path, 0) + 1
        authors.setdefault(path, set()).add(author)
        if ts is not None:
            stamps.setdefault(path, []).append(ts)
    return commits, authors, stamps


def parse_git_log(text: str) -> dict[str, FileChurn]:
    """Whole-text entrypoint: the log already in hand."""
    return parse_git_log_lines(text.splitlines())


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
