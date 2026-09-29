"""The two reads behind `explain --history`: which commits touched a line span,
and what their messages say.

line_commits asks `git log -L` for names alone, and -s drops the hunks only on a
git that honors it with -L. The full -p output is read here too, so the header
rule is proven on the output an older git prints: hunks in the file's own bytes,
one of them a NUL followed by an object name.
"""
import subprocess
from pathlib import Path

import pytest

from crapkit import gitio
from crapkit.errors import GitError

FAKE_HEADER = b"\x00" + b"a" * 40


def git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=root, input=stdin, check=True,
                          capture_output=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    for key, value in (("user.name", "t"), ("user.email", "t@t"), ("core.autocrlf", "false")):
        git(tmp_path, "config", key, value)
    (tmp_path / "f.py").write_bytes(b"def f():\n    return 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    (tmp_path / "f.py").write_bytes(b"def f():\n" + FAKE_HEADER + b"\n    return 2  # caf\xe9\n")
    git(tmp_path, "commit", "-q", "-a", "-m", "two")
    return tmp_path


def _names(repo: Path) -> list[str]:
    return git(repo, "log", "--format=%H").decode().split()


def test_line_commits_names_every_commit_that_touched_the_span_newest_first(repo):
    assert gitio.line_commits(repo, "f.py", 1, 3, 10) == _names(repo)


def test_line_commits_stops_at_the_limit(repo):
    assert gitio.line_commits(repo, "f.py", 1, 3, 1) == _names(repo)[:1]


def test_a_hunk_line_is_never_read_as_a_header(repo):
    """What a git that ignores -s prints: each hunk line behind its indicator.
    --text keeps the hunks on every git: git 2.55 prints `Binary files ... differ`
    for a file holding a NUL where git 2.43 printed its hunks."""
    patched = git(repo, "log", "-L1,3:f.py", "--text", "--format=%x00%H")
    assert b"+" + FAKE_HEADER in patched and b"caf\xe9" in patched

    found = [name.decode() for name in gitio._LINE_LOG_HEADER.findall(patched)]

    assert found == _names(repo)


def test_commit_messages_keeps_every_byte_but_the_newlines_git_adds(repo):
    message = b"odd\rsubject\n\n\x01zz 2025-01-01 fake\n\x02\nend\x0cof\x85it"
    git(repo, "commit", "-q", "--allow-empty", "--cleanup=verbatim", "-F", "-", stdin=message)
    head = _names(repo)[0]

    (entry,) = gitio.commit_messages(repo, [head])

    assert entry[0] == head[:len(entry[0])] and len(entry[1]) == 10
    assert entry[2:] == ("odd\rsubject", "\x01zz 2025-01-01 fake\n\x02\nend\x0cof\x85it")


def test_commit_messages_answers_in_the_order_named(repo):
    names = _names(repo)[::-1]

    subjects = [subject for _, _, subject, _ in gitio.commit_messages(repo, names)]

    assert subjects == ["one", "two"]


def test_commit_messages_of_no_commits_is_empty_without_asking_git(tmp_path):
    assert gitio.commit_messages(tmp_path / "not-a-repo", []) == []


def test_an_unknown_commit_is_a_git_error(repo):
    with pytest.raises(GitError, match="no-walk"):
        gitio.commit_messages(repo, ["0" * 40])
