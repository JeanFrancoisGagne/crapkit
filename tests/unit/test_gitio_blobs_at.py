"""gitio.blobs_at: the bytes a set of paths held at one revision, from one
`git cat-file --batch` process, None where the revision holds no blob there.

hook-precommit and claude-hook both need a function's ccn at the diff base,
so both read the base blobs through this one call. A path the revision does
not hold answers None; a revision the clone does not hold raises, because the
revision's own presence is asked in the same process. Every repo here is
written through raw_git, so the stored bytes are exactly the bytes the test
names.
"""
from __future__ import annotations

import codecs
import subprocess
from pathlib import Path

import pytest

from crapkit import gitio
from crapkit.errors import GitError
from raw_git import checkout, commit, git, repository, stage

SOURCE = b"def f(x):\n    return x\n"
OTHER = b"def g(y):\n    return y\n"


@pytest.fixture
def spawns(monkeypatch):
    """The git processes started while the test runs, by argv. subprocess.run
    starts through Popen, so one seam counts both."""
    started = []
    real = subprocess.Popen

    def counted(argv, *args, **kwargs):
        if argv and argv[0] == "git":
            started.append(argv)
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(gitio.subprocess, "Popen", counted)
    return started


def gitlink(root: Path, path: str, sha: str) -> str:
    """A commit on main that adds a submodule entry at `path` pointing at `sha`."""
    git(root, "read-tree", "HEAD")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{sha},{path}")
    tree = git(root, "write-tree").decode().strip()
    made = git(root, "commit-tree", tree, "-p", "HEAD", "-m", "submodule").decode().strip()
    git(root, "update-ref", "refs/heads/main", made)
    return made


@pytest.fixture
def held(tmp_path):
    """HEAD holds a.py, pkg/b.py and a submodule; gone.py was deleted at HEAD;
    staged.py is only in the index."""
    root = repository(tmp_path / "repo")
    commit(root, files={b"a.py": SOURCE, b"pkg/b.py": OTHER, b"gone.py": SOURCE})
    commit(root, files={b"a.py": SOURCE}, deletes=(b"gone.py",))
    gitlink(root, "vendor/sub", "1" * 40)
    checkout(root)
    stage(root, b"staged.py", OTHER)
    return root


# --- the interface -------------------------------------------------------------

def test_each_path_answers_its_bytes_at_the_revision_or_none(held):
    answer = gitio.blobs_at(held, "HEAD", ["a.py", "pkg/b.py", "staged.py", "gone.py", "pkg",
                                           "vendor/sub", "never.py"])

    assert answer == {"a.py": SOURCE, "pkg/b.py": OTHER, "staged.py": None, "gone.py": None,
                      "pkg": None, "vendor/sub": None, "never.py": None}


def test_a_revision_named_by_sha_answers_that_commits_bytes(tmp_path):
    root = repository(tmp_path / "repo")
    first = commit(root, files={b"a.py": SOURCE})
    commit(root, files={b"a.py": OTHER})

    assert gitio.blobs_at(root, first, ["a.py"]) == {"a.py": SOURCE}
    assert gitio.blobs_at(root, "HEAD", ["a.py"]) == {"a.py": OTHER}


def test_an_unborn_head_answers_none_for_every_path(tmp_path):
    root = repository(tmp_path / "repo")
    stage(root, b"a.py", SOURCE)

    assert gitio.blobs_at(root, "HEAD", ["a.py", "new\nline.py"]) == {"a.py": None,
                                                                     "new\nline.py": None}


@pytest.mark.parametrize("revision", ["0123456789abcdef0123456789abcdef01234567", "no-such-branch"])
def test_a_revision_the_clone_does_not_hold_raises(held, revision):
    with pytest.raises(GitError, match=revision):
        gitio.blobs_at(held, revision, ["a.py"])


def test_a_detached_head_the_clone_does_not_hold_raises_rather_than_reading_unborn(held):
    """Only a HEAD that names no commit at all is unborn: a detached HEAD
    naming an object this clone lacks is a missing revision."""
    (held / ".git" / "HEAD").write_text("1" * 40 + "\n", encoding="ascii")

    with pytest.raises(GitError, match="HEAD"):
        gitio.blobs_at(held, "HEAD", ["a.py"])


def test_a_missing_revision_raises_before_any_per_path_fallback(held):
    with pytest.raises(GitError, match="no-such-branch"):
        gitio.blobs_at(held, "no-such-branch", ["new\nline.py"])


def test_a_commit_a_shallow_clone_cut_off_raises(tmp_path):
    origin = repository(tmp_path / "origin")
    cut = commit(origin, files={b"a.py": SOURCE})
    commit(origin, files={b"a.py": OTHER})
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--depth", "1", "--branch", "main", origin.resolve().as_uri(),
        str(clone))

    assert gitio.blobs_at(clone, "HEAD", ["a.py"]) == {"a.py": OTHER}
    with pytest.raises(GitError, match=cut[:12]):
        gitio.blobs_at(clone, cut, ["a.py"])


def test_the_staged_reads_basis_is_head_or_the_merge_base(tmp_path):
    root = repository(tmp_path / "repo")
    fork = commit(root, files={b"a.py": SOURCE})
    git(root, "branch", "feature")
    commit(root, files={b"a.py": OTHER})
    git(root, "symbolic-ref", "HEAD", "refs/heads/feature")
    checkout(root)
    git(root, "commit", "-q", "--allow-empty", "-m", "on feature")

    with gitio.staged_reads(root) as reads:
        assert reads.basis == "HEAD"
    with gitio.staged_reads(root, base="main") as reads:
        assert reads.basis == fork


def test_one_process_answers_every_path_without_a_line_break(held, spawns):
    spawns.clear()
    gitio.blobs_at(held, "HEAD", ["a.py", "pkg/b.py", "gone.py", "pkg", "vendor/sub"])

    assert len(spawns) == 1
    assert spawns[0][1:] == ["cat-file", "--batch"]


def test_a_name_holding_a_line_break_costs_one_process_of_its_own(tmp_path, spawns):
    root = repository(tmp_path / "repo")
    commit(root, files={b'"new\\nline.py"': SOURCE, b"a.py": OTHER})
    spawns.clear()

    answer = gitio.blobs_at(root, "HEAD", ["a.py", "new\nline.py", "gone\nline.py"])

    assert answer == {"a.py": OTHER, "new\nline.py": SOURCE, "gone\nline.py": None}
    assert len(spawns) == 3


def test_an_empty_path_list_starts_no_process(held, spawns):
    spawns.clear()
    assert gitio.blobs_at(held, "HEAD", []) == {}
    assert gitio.blobs_at(held, "no-such-branch", []) == {}
    assert spawns == []


# --- variations ----------------------------------------------------------------

def test_a_root_nested_below_the_git_top_reads_its_own_paths(tmp_path):
    top = repository(tmp_path / "repo")
    commit(top, files={b"x.py": SOURCE, b"app/x.py": OTHER})
    checkout(top)

    assert gitio.blobs_at(top / "app", "HEAD", ["x.py"]) == {"x.py": OTHER}
    assert gitio.blobs_at(top, "HEAD", ["x.py"]) == {"x.py": SOURCE}


NAMES = {
    "a space.py": b'"a space.py"',
    "café.py": "café.py".encode("utf-8"),
    "new\nline.py": b'"new\\nline.py"',
    "#hash.py": b"#hash.py",
    "cr\rname.py": b'"cr\\rname.py"',
}


@pytest.mark.parametrize("name", sorted(NAMES))
def test_every_spelling_of_a_name_resolves(tmp_path, name):
    root = repository(tmp_path / "repo")
    commit(root, files={NAMES[name]: SOURCE, b"other.py": OTHER})

    assert gitio.blobs_at(root, "HEAD", [name, "other.py"]) == {name: SOURCE, "other.py": OTHER}


def test_a_name_that_is_not_utf8_goes_out_as_its_own_bytes(tmp_path):
    root = repository(tmp_path / "repo")
    commit(root, files={b"caf\xe9.py": SOURCE, b"other.py": OTHER})
    name = b"caf\xe9.py".decode("utf-8", "surrogateescape")

    assert gitio.blobs_at(root, "HEAD", [name, "other.py"]) == {name: SOURCE, "other.py": OTHER}


BODIES = {
    "crlf": SOURCE.replace(b"\n", b"\r\n"),
    "cr-only": SOURCE.replace(b"\n", b"\r"),
    "utf8-bom": codecs.BOM_UTF8 + SOURCE,
    "utf16le-bom": codecs.BOM_UTF16_LE + SOURCE.decode("ascii").encode("utf-16-le"),
    "no-final-newline": b"x = 1",
    "empty": b"",
}


@pytest.mark.parametrize("kind", sorted(BODIES))
def test_the_bytes_come_back_unchanged(tmp_path, kind):
    root = repository(tmp_path / "repo")
    commit(root, files={b"f.py": BODIES[kind], b"g.py": OTHER})

    assert gitio.blobs_at(root, "HEAD", ["f.py", "g.py"]) == {"f.py": BODIES[kind], "g.py": OTHER}


# --- blob_at answers through blobs_at ------------------------------------------

def test_blob_at_is_one_process_and_none_for_a_directory(held, spawns):
    spawns.clear()
    assert gitio.blob_at(held, "HEAD", "a.py") == SOURCE
    assert gitio.blob_at(held, "HEAD", "pkg") is None
    assert len(spawns) == 2
