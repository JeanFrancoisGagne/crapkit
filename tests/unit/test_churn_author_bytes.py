"""An author name is data to churn, whatever bytes it holds.

The churn window's log frames each commit as `\\x01%an\\x02%at\\x02%ct`, and git
keeps a \\r, a \\x01 or a \\x02 inside an author name. The log was read in text
mode, whose universal newlines split a name at its \\r, and the header was cut
at its first \\x02, so the name ran into the dates: the commit lost its dates,
its author merged with another, and the next carried refresh dropped it. A name
whose bytes are not UTF-8, which git passes through when the commit declares no
encoding, stopped every churn reader with UnicodeDecodeError.

Real git throughout. A name with a \\r or a \\x02 must give the churn a plain
name gives, before and after a carried refresh.
"""
import json
import subprocess
import time
import zlib
from pathlib import Path

import pytest

from crapkit import churn_cache, churn_log, gitio
from crapkit.churn import Commit, fold

DAY = 86400
BASE = int(time.time()) - 10 * DAY  # inside any window of a month or more


def git(repo: Path, *args: str, author: str = "A U Thor", when: int = BASE,
        stdin: bytes | None = None) -> bytes:
    env = {"GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": "a@example.com",
           "GIT_COMMITTER_NAME": "C", "GIT_COMMITTER_EMAIL": "c@example.com",
           "GIT_AUTHOR_DATE": f"@{when} +0000", "GIT_COMMITTER_DATE": f"@{when} +0000"}
    return subprocess.run(["git", *args], cwd=repo, input=stdin, check=True, capture_output=True,
                          env={**__import__("os").environ, **env}).stdout


def commit(repo: Path, step: int, author: str, *paths: str) -> None:
    for path in paths:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(f"step {step}\n", encoding="utf-8", newline="\n")
    git(repo, "add", *paths)
    git(repo, "commit", "-q", "-m", f"step {step}", author=author, when=BASE + step * DAY)


def history(repo: Path, name: str) -> Path:
    """A U Thor, then `name`, then an author called X, each touching src/e.py."""
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "core.autocrlf", "false")
    commit(repo, 0, "A U Thor", "src/e.py", "src/g.py")
    commit(repo, 1, name, "src/e.py", "src/g.py")
    commit(repo, 2, "X", "src/e.py")
    return repo


def reads(repo: Path) -> list:
    """e.py's churn cold, then warm after one more commit: the carried read."""
    cold = churn_cache.load_churn(repo, 12)["src/e.py"]
    commit(repo, 3, "A U Thor", "src/e.py")
    return [cold, churn_cache.load_churn(repo, 12)["src/e.py"]]


@pytest.mark.parametrize("name", ["X\rY", "X\x02Y"], ids=["cr", "stx"])
def test_a_name_holding_a_record_byte_counts_as_its_own_author(tmp_path, name):
    odd = reads(history(tmp_path / "odd", name))

    assert odd == reads(history(tmp_path / "plain", "XY"))
    assert [(c.commits, c.authors) for c in odd] == [(3, 3), (4, 3)]


def test_a_header_reads_its_dates_from_the_right():
    table = fold(["\x01X\x02Y\x02100\x02200\n", "src/e.py\n"])

    assert table.authors == ["X\x02Y"]
    assert list(table.commits.values()) == [Commit(0, 100, 200)]


def test_the_log_is_split_at_lf_alone(tmp_path):
    repo = history(tmp_path / "repo", "X\rY")

    lines = list(gitio._git_lines(repo, "log", "--format=%x01%an", "-1", "HEAD~1"))

    assert lines == ["\x01X\rY\n"]


def _latin1_author(repo: Path) -> Path:
    """A commit whose author name is the raw byte 0xe9, with no encoding header,
    the way an old import writes one. Only hash-object can make it."""
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "src").mkdir()
    (repo / "src" / "e.py").write_text("step 0\n", encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    tree = git(repo, "write-tree").strip()
    stamp = str(BASE).encode()
    body = (b"tree " + tree + b"\nauthor caf\xe9 <a@a> " + stamp + b" +0000\n"
            b"committer C <c@c> " + stamp + b" +0000\n\nseed\n")
    name = git(repo, "hash-object", "-t", "commit", "-w", "--stdin", stdin=body).strip()
    git(repo, "update-ref", "HEAD", name.decode())
    return repo


def test_a_name_that_is_not_utf8_is_read_with_a_replacement_character(tmp_path):
    repo = _latin1_author(tmp_path / "repo")

    assert churn_cache.load_churn(repo, 12)["src/e.py"].authors == 1
    assert next(churn_log.log_lines(repo, 12)).startswith("\x01caf�\x02")


def test_a_repo_logging_in_latin1_still_reads_names_as_committed(tmp_path):
    repo = history(tmp_path / "repo", "café")
    git(repo, "config", "i18n.logOutputEncoding", "ISO-8859-1")

    headers = [line for line in churn_log.log_lines(repo, 12) if line.startswith("\x01")]

    assert headers[1].startswith("\x01café\x02")


def test_a_log_laid_down_before_lf_only_reads_is_walked_again(tmp_path):
    """Such a log may hold a name cut at its \\r; carrying it would keep the cut."""
    repo = history(tmp_path / "repo", "X\rY")
    list(churn_log.log_lines(repo, 12))
    path = repo / ".crapkit" / churn_log.LOG_NAME
    blob = zlib.compress(b"\x01stale\x021\x021\nsrc/stale.py\n", 1)
    path.write_bytes(blob)
    key = json.loads(churn_log._key_path(path).read_text(encoding="utf-8"))
    key.update(paths="root-relative", size=len(blob), crc=zlib.crc32(blob))
    churn_log._key_path(path).write_text(json.dumps(key), encoding="utf-8")

    assert "src/stale.py\n" not in list(churn_log.log_lines(repo, 12))


def test_a_map_laid_down_before_lf_only_reads_is_rebuilt(tmp_path):
    repo = history(tmp_path / "repo", "X\rY")
    churn_cache.load_churn(repo, 12)
    path = repo / ".crapkit" / churn_cache.CACHE_NAME
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["key"]["paths"] = "root-relative-exact"
    doc["files"] = {"src/stale.py": [1, 1, 1.0]}
    path.write_text(json.dumps(doc), encoding="utf-8")

    assert "src/stale.py" not in churn_cache.load_churn(repo, 12)
