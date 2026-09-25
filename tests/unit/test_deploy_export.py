"""tools/deploy/export.py hands the tree under test to a deploy container.

The container gets a git bundle (history, branches and tags) and a tarball of
the files git would show: tracked and untracked, never what .gitignore keeps
out. Mounting the checkout instead broke three ways: git refused the mount as
"dubious ownership" when the runner's uid differed from the file owner, a
linked worktree's `.git` file pointed at a host path the container cannot see,
and build/, dist/ and venvs leaked into the candidate.
"""
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "deploy"))

import export  # noqa: E402  (tools/deploy is a script dir, not a package)

IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false"]


def git(repo, *args):
    return subprocess.run(["git", *IDENTITY, *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def worktree(tmp_path):
    """A repo with a tag, and a linked worktree carrying one untracked file and
    the three ignored directories a developer checkout grows."""
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    (main / ".gitignore").write_text("build/\ndist/\n.venv/\n", encoding="utf-8")
    (main / "hook.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (main / "pkg.py").write_text("X = 1\n", encoding="utf-8")
    git(main, "add", "-A")
    git(main, "update-index", "--chmod=+x", "hook.sh")
    git(main, "commit", "-q", "-m", "one")
    git(main, "tag", "v1")
    linked = tmp_path / "linked"
    git(main, "worktree", "add", "-q", "--detach", str(linked))
    (linked / "new.py").write_text("Y = 2\n", encoding="utf-8")
    for ignored in ("build", "dist", ".venv"):
        (linked / ignored).mkdir()
        (linked / ignored / "junk.py").write_text("", encoding="utf-8")
    return linked


def members(tar_path):
    with tarfile.open(tar_path) as tar:
        return {member.name: member for member in tar.getmembers()}


def test_the_tarball_holds_tracked_and_untracked_files_and_nothing_ignored(worktree, tmp_path):
    bundle, tree = export.export(worktree, tmp_path / "out")

    assert sorted(members(tree)) == [".gitignore", "hook.sh", "new.py", "pkg.py"]


def test_the_index_mode_survives_a_checkout_that_has_no_exec_bit(worktree, tmp_path):
    """Windows checkouts carry no exec bit on disk, so the mode comes from git."""
    bundle, tree = export.export(worktree, tmp_path / "out")

    assert members(tree)["hook.sh"].mode == 0o755
    assert members(tree)["pkg.py"].mode == 0o644


def test_the_bundle_clones_with_its_tags_and_the_worktree_head(worktree, tmp_path):
    bundle, tree = export.export(worktree, tmp_path / "out")
    clone = tmp_path / "clone.git"
    subprocess.run(["git", "clone", "-q", "--mirror", str(bundle), str(clone)], check=True)

    assert git(clone, "tag") == "v1"
    assert git(clone, "rev-parse", "v1") == git(worktree, "rev-parse", "HEAD")


def test_two_exports_of_one_tree_are_the_same_bytes(worktree, tmp_path):
    first = export.export(worktree, tmp_path / "a")[1].read_bytes()
    second = export.export(worktree, tmp_path / "b")[1].read_bytes()

    assert first == second


def test_a_tracked_file_deleted_from_disk_is_left_out(worktree, tmp_path):
    os.remove(worktree / "pkg.py")

    assert "pkg.py" not in members(export.export(worktree, tmp_path / "out")[1])


def test_the_command_line_writes_both_files(worktree, tmp_path):
    out = tmp_path / "out"
    assert export.main(["--repo", str(worktree), "--out", str(out)]) == 0

    assert sorted(path.name for path in out.iterdir()) == ["src.bundle", "tree.tar"]


# --- unpacking ---------------------------------------------------------------------
# CPython took extraction filters in 3.11.4. Debian 12's python3 is 3.11.2, and
# so was the Windows `python` a run.py user starts with, where every unpack of
# tree.tar or a pinned archive stopped with "extractall() got an unexpected
# keyword argument 'filter'".

def _tarball(tmp_path, name, text="x"):
    source = tmp_path / "member.txt"
    source.write_text(text, encoding="utf-8")
    archive = tmp_path / "in.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(source, arcname=name)
    return archive


# The stand-in calls the real extractall with no filter, which 3.12 and 3.13 warn about.
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_a_python_without_extraction_filters_still_unpacks(tmp_path, monkeypatch):
    original = tarfile.TarFile.extractall

    def before_filters(self, path=".", members=None, *, numeric_owner=False, **extra):
        if extra:
            raise TypeError(f"extractall() got an unexpected keyword argument {next(iter(extra))!r}")
        return original(self, path, members, numeric_owner=numeric_owner)

    monkeypatch.delattr(tarfile, "tar_filter", raising=False)
    monkeypatch.setattr(tarfile.TarFile, "extractall", before_filters)

    export.unpack_tar(_tarball(tmp_path, "pkg/a.txt", "kept"), tmp_path / "dest")

    assert (tmp_path / "dest" / "pkg" / "a.txt").read_text(encoding="utf-8") == "kept"


@pytest.mark.skipif(not hasattr(tarfile, "tar_filter"), reason="this Python has no extraction filters")
def test_a_python_with_filters_refuses_a_member_outside_the_destination(tmp_path):
    with pytest.raises(tarfile.FilterError):
        export.unpack_tar(_tarball(tmp_path, "../escape.txt"), tmp_path / "dest")

    assert not (tmp_path / "escape.txt").exists()
