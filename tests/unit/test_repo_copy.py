"""A repository copied while git's background maintenance drops its lock."""
import shutil
from pathlib import Path

import pytest

from repo_copy import copy_repo


def _vanishing_lock(src, dst, **options):
    """The detached `git maintenance run --auto` removing its lock after the
    copy listed it, as it did under the dogfood job's copy of a fixture repo."""
    if src.endswith(".lock"):
        Path(src).unlink()
    return shutil.copy2(src, dst, **options)


def _repo(tmp_path: Path) -> Path:
    objects = tmp_path / "src" / ".git" / "objects"
    objects.mkdir(parents=True)
    (objects / "maintenance.lock").write_text("", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path / "src"


def test_a_lock_that_vanishes_mid_copy_fails_a_plain_copytree(tmp_path):
    with pytest.raises(shutil.Error, match="maintenance.lock"):
        shutil.copytree(_repo(tmp_path), tmp_path / "plain", copy_function=_vanishing_lock)


def test_a_repository_copy_leaves_the_lock_out_and_keeps_the_rest(tmp_path):
    copy = copy_repo(_repo(tmp_path), tmp_path / "copy", copy_function=_vanishing_lock)

    assert (copy / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (copy / ".git" / "objects" / "maintenance.lock").exists()
