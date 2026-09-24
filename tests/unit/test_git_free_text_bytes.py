"""Free text git prints as stored: bytes that are not UTF-8 read, never crash.

git re-encodes a commit's author and message to UTF-8 only when the commit
names its encoding in a header, and never touches a blob. A commit without
that header (an import from another VCS, a client set to a legacy code page)
comes out as the raw bytes it was written with, and `René` in Latin-1 is
`Ren\\xe9`: invalid UTF-8. Every reader here builds that commit with
fast-import, which writes the bytes as given; `git commit` would re-encode the
message as Latin-1 before storing it.
"""
import subprocess
import time

import pytest

from crapkit import churn_log
from crapkit.churn import parse_git_log_lines
from crapkit.cli.reports import _function_commits

LATIN1 = b"Ren\xe9"
REPLACED = "Ren�"
SOURCE = b"def f(x):\n    return x\n"


def git(root, *args, stdin=None):
    return subprocess.run(["git", *args], cwd=root, input=stdin, capture_output=True,
                          check=True).stdout


def raw_commit(root, *, author: bytes = b"a", message: bytes = b"edit",
               path: bytes = b"a.py", body: bytes = SOURCE) -> None:
    """One commit on HEAD's branch whose author, message and blob bytes git
    stores untouched."""
    stamp = b"%d +0000" % int(time.time())
    parent = b"from HEAD^0\n" if git_has_head(root) else b""
    git(root, "fast-import", "--quiet", stdin=b"".join([
        b"commit refs/heads/main\n",
        b"author " + author + b" <a@example.test> " + stamp + b"\n",
        b"committer c <c@example.test> " + stamp + b"\n",
        b"data %d\n" % len(message), message, b"\n", parent,
        b"M 100644 inline " + path + b"\ndata %d\n" % len(body), body, b"\n",
    ]))


def git_has_head(root) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=root,
                          capture_output=True).returncode == 0


def repository(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "symbolic-ref", "HEAD", "refs/heads/main")
    return tmp_path


def test_churn_window_reads_an_author_that_is_not_utf8(tmp_path):
    root = repository(tmp_path)
    raw_commit(root, author=LATIN1)
    walked = list(churn_log.log_lines(root, 12))
    assert walked[0].startswith(f"\x01{REPLACED}\x02")
    assert parse_git_log_lines(walked)["a.py"].commits == 1
    assert list(churn_log.log_lines(root, 12)) == walked  # the laid-down copy says the same


@pytest.mark.parametrize("message, body", [
    (LATIN1 + b" fix\n\n" + LATIN1 + b" body\n", SOURCE),
    (b"fix\n", b"def f(x):\n    # " + LATIN1 + b"\n    return x\n"),
], ids=["message", "source-line"])
def test_function_history_reads_a_commit_that_is_not_utf8(tmp_path, message, body):
    root = repository(tmp_path)
    raw_commit(root, message=message, body=body)
    subject, _, rest = message.decode("utf-8", "replace").partition("\n")
    assert [(c["subject"], c["body"]) for c in _function_commits(root, "a.py", 1, 2)] == [
        (subject, rest.strip("\n"))]
