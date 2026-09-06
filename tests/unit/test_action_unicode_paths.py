"""The Action's changed-file command preserves the worklist's path identity."""
from pathlib import Path
import shlex
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True, encoding="utf-8").strip()


@pytest.mark.parametrize("filename", ["café.py", "世界.py"])
def test_action_changed_files_match_unicode_worklist_paths(tmp_path, filename):
    git(tmp_path, "init")
    git(tmp_path, "config", "core.quotePath", "true")
    path = tmp_path / filename
    path.write_text("before\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=Probe", "-c", "user.email=probe@example.test",
        "commit", "-qm", "before")
    base = git(tmp_path, "rev-parse", "HEAD")
    path.write_text("after\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=Probe", "-c", "user.email=probe@example.test",
        "commit", "-qm", "after")
    command = next(line.strip().split(" > ")[0]
                   for line in (ROOT / "action.yml").read_text(encoding="utf-8").splitlines()
                   if 'diff --name-only "$BASE_SHA...HEAD"' in line)
    argv = [part.replace("$BASE_SHA", base) for part in shlex.split(command)]
    actual = subprocess.check_output(argv, cwd=tmp_path, text=True, encoding="utf-8").splitlines()
    assert actual == [filename]
