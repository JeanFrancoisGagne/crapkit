"""gitio.start_read: one git read started now and collected later, for modules outside gitio.

lane_changes starts several reads at once and collects each when it needs the
answer. It did so through gitio's private process class. start_read is the
public door: bytes out, no stdin, and a failure raised as GitError naming the
command.
"""
import subprocess

import pytest

from crapkit.errors import GitError
from crapkit.gitio import start_read


def _repo(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "commit", "-q", "-m", "one"], check=True)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_a_started_read_answers_bytes_when_collected(tmp_path):
    head = _repo(tmp_path)

    read = start_read(tmp_path, "rev-parse", "HEAD")

    assert read.result() == f"{head}\n".encode()


def test_a_failed_read_names_its_command(tmp_path):
    _repo(tmp_path)

    with pytest.raises(GitError, match="git rev-parse no-such-rev failed in"):
        start_read(tmp_path, "rev-parse", "no-such-rev").result()
