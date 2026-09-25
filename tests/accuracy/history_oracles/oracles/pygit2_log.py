"""Commit messages, dates and ids read straight from the commit objects. No crapkit.

Two readers of the same objects: pygit2 1.20.1 (libgit2, no git process) and
`git cat-file commit`, which prints the raw object. Neither goes through a
log format, so a message holding \\x01, \\x02 or any other control character
comes back exactly as it was committed.

What explain --history prints for a commit (README.md explain row;
docs/agent-json.md `explain`: each commit carries its message `body`):
- subject: git's %s, the message's first paragraph with its line breaks
  joined by single spaces (git-log PRETTY FORMATS, "subject");
- body: git's %b, the message after that paragraph and the blank lines that
  end it, with the newlines at either end trimmed;
- date: the author date in the author's own offset, as YYYY-MM-DD (git-log
  --date=short).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .history_git import git


@dataclass(frozen=True)
class Message:
    sha: str
    date: str
    subject: str
    body: str


GIT_SPACE = " \t\n\r"  # git's own isspace (git-compat-util.h): not \v, not \f


def _blank(line: str) -> bool:
    return not line.rstrip(GIT_SPACE)


def split_message(message: str) -> tuple[str, str]:
    """(subject, body) the way git's %s and %b cut a message: skip blank lines,
    take the paragraph up to the next blank line as the subject (each line's
    trailing space trimmed), skip the blank lines after it, and the rest is the
    body."""
    lines = message.split("\n")
    first = next((i for i, line in enumerate(lines) if not _blank(line)), len(lines))
    end = next((i for i in range(first, len(lines)) if _blank(lines[i])), len(lines))
    subject = " ".join(line.rstrip(GIT_SPACE) for line in lines[first:end])
    return subject, "\n".join(lines[end:]).strip("\n")


def _short_date(stamp: int, offset_minutes: int) -> str:
    zone = timezone(timedelta(minutes=offset_minutes))
    return datetime.fromtimestamp(stamp, zone).date().isoformat()


def via_pygit2(root: Path, shas: list[str]) -> list[Message]:
    import pygit2

    repo = pygit2.Repository(str(root))
    commits = [repo.get(sha) for sha in shas]
    return [Message(str(c.id), _short_date(c.author.time, c.author.offset),
                    *split_message(c.message)) for c in commits]


def _raw(root: Path, sha: str) -> tuple[list[str], str]:
    """The object's header lines and its message."""
    raw = git(root, "cat-file", "commit", sha).decode("utf-8")
    head, _, message = raw.partition("\n\n")
    return head.split("\n"), message


def _author_date(header: list[str]) -> str:
    """'author Name <mail> 1750000000 +0130' read to a short date."""
    author = next(line for line in header if line.startswith("author "))
    stamp, zone = author.rsplit(" ", 2)[1:]
    sign = -1 if zone.startswith("-") else 1
    return _short_date(int(stamp), sign * (int(zone[1:3]) * 60 + int(zone[3:5])))


def via_cat_file(root: Path, shas: list[str]) -> list[Message]:
    found = []
    for sha in shas:
        header, message = _raw(root, sha)
        found.append(Message(sha, _author_date(header), *split_message(message)))
    return found
