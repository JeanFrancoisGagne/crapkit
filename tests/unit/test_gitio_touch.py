"""A file whose bytes did not change is not a change, and asking leaves .git/index alone.

crapkit reads the uncommitted set for lane staleness, lane reuse, verify's dirty
tags and the mutation pool. It used to read the worktree half with `git diff
--name-only`, which answers from the index's stat cache. With
`diff.autoRefreshIndex` off, that names every file whose mtime moved, so a
`touch` or a copied checkout alone read as an edit: a reused lane printed "1
file(s) in its scopes changed" and next-item dropped every dark line. With it
on, git checks the content and then writes the refreshed index over .git/index,
whatever GIT_OPTIONAL_LOCKS says. crapkit starts its lane reads at once, and on
Windows a sibling read that opens the index during that rename fails with
"index file open failed: Permission denied": the lane then read stale or
stamped no proof on a tree nobody touched.

`git --no-optional-locks status` answers the same question, compares content
whatever diff.autoRefreshIndex says, and leaves .git/index as it found it.

Only a patch carries line numbers, so verify, rescore, mutate and the advisory
hook still read one with `git diff`. Those reads turn diff.autoRefreshIndex off:
git then prints no patch for a same-bytes file and writes nothing back.

The checkout holds CRLF under `core.autocrlf=true` while the blob holds LF: a
check that compared raw bytes instead of going through git would read every
such file as changed. Real git processes, because the bug lives in the argv
crapkit builds.
"""
import os
import subprocess
from pathlib import Path

import pytest

from crapkit.cli.claude_hook import _diff_proc, _porcelain
from crapkit.gitio import diff_since, status_names, unstaged_paths, worktree_changes
from crapkit.lane_changes import ChangeReads
from hang_guard import HANG_SECONDS


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          timeout=HANG_SECONDS, check=True).stdout


def _touch(path: Path) -> None:
    """A new mtime on the same bytes, two minutes ahead, so no stat cache matches it."""
    later = os.stat(path).st_mtime_ns + 120 * 10**9
    os.utime(path, ns=(later, later))


def _scoped(root: Path) -> tuple:
    with ChangeReads(root, (), ("src",)) as reads:
        return reads.status_names()


READERS = {"status_names": lambda root: tuple(status_names(root)),
           "unstaged_paths": lambda root: tuple(sorted(unstaged_paths(root))),
           "worktree_changes": lambda root: tuple(worktree_changes(root)),
           "ChangeReads": _scoped}


def _commit_one_crlf_file(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "core.autocrlf", "true")
    (root / "src").mkdir()
    (root / "src" / "a.ts").write_bytes(b"export const a = 1;\r\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """One CRLF source file committed under core.autocrlf=true, with the stat
    refresh off: the repo config a same-bytes touch used to read as an edit in."""
    _commit_one_crlf_file(tmp_path)
    _git(tmp_path, "config", "diff.autoRefreshIndex", "false")
    return tmp_path


@pytest.mark.parametrize("reader", sorted(READERS))
def test_a_touched_file_is_no_change(repo, reader):
    _touch(repo / "src" / "a.ts")

    assert READERS[reader](repo) == ()


@pytest.mark.parametrize("reader", sorted(READERS))
def test_an_edit_is_still_a_change(repo, reader):
    """The guard cuts both ways: new bytes stay dirty in every read."""
    (repo / "src" / "a.ts").write_bytes(b"export const a = 2;\r\n")

    assert READERS[reader](repo) == ("src/a.ts",)


def _lf_blob_under_autocrlf_input(root: Path) -> Path:
    """One LF file committed, then core.autocrlf=input: `git add` turns CRLF
    into LF, so CRLF bytes written over the file store the same blob."""
    _git(root, "init", "-q", "-b", "main")
    (root / "src").mkdir()
    source = root / "src" / "a.ts"
    source.write_bytes(b"export const a = 1;\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    _git(root, "config", "core.autocrlf", "input")
    return source


@pytest.mark.parametrize("reader", sorted(READERS))
def test_crlf_bytes_over_an_lf_blob_under_autocrlf_input_are_no_change(tmp_path, reader):
    """git status calls a file whose size moved modified without reading it,
    and these bytes are the blob the index holds once git's filters run."""
    _lf_blob_under_autocrlf_input(tmp_path).write_bytes(b"export const a = 1;\r\n")

    assert READERS[reader](tmp_path) == ()


def test_a_staged_file_rewritten_as_crlf_under_autocrlf_input_is_staged_only(tmp_path):
    """The hook's re-stage note reads the worktree half: the working copy
    holds the staged blob, so only the staged change is left."""
    source = _lf_blob_under_autocrlf_input(tmp_path)
    source.write_bytes(b"export const a = 2;\n")
    _git(tmp_path, "add", "src/a.ts")
    source.write_bytes(b"export const a = 2;\r\n")

    assert (status_names(tmp_path), unstaged_paths(tmp_path)) == (["src/a.ts"], set())
    assert (_scoped(tmp_path), worktree_changes(tmp_path)) == (("src/a.ts",), [])


def _index_state(root: Path) -> tuple:
    """git writes a refreshed index to index.lock and renames it over .git/index,
    which gives the file a new id and a new mtime."""
    stat = os.stat(root / ".git" / "index")
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


@pytest.mark.parametrize("reader", sorted(READERS))
def test_a_read_leaves_the_index_as_it_found_it(tmp_path, reader):
    """git's default config, where a worktree `git diff` refreshes the index of
    a touched file and writes it back: the write that raced crapkit's own
    concurrent reads on Windows."""
    _commit_one_crlf_file(tmp_path)
    _touch(tmp_path / "src" / "a.ts")
    before = _index_state(tmp_path)

    assert READERS[reader](tmp_path) == ()
    assert _index_state(tmp_path) == before


def test_the_advisory_hooks_status_read_leaves_the_index_alone(tmp_path):
    """The Claude Code hook asks git status after every shell command, beside
    whatever git commands the agent runs next: the read git-status(1) tells to
    take --no-optional-locks."""
    _commit_one_crlf_file(tmp_path)
    _touch(tmp_path / "src" / "a.ts")
    before = _index_state(tmp_path)

    assert _porcelain(tmp_path) == ""
    assert _index_state(tmp_path) == before


def _hook_patch(root: Path) -> str:
    read = _diff_proc(root, "src/a.ts")
    try:
        return read.result()
    finally:
        read.close()


PATCHES = {"diff_since": lambda root: diff_since(root, "HEAD"),
           "the advisory hook's diff": _hook_patch}


@pytest.mark.parametrize("reader", sorted(PATCHES))
def test_a_patch_read_leaves_the_index_as_it_found_it(tmp_path, reader):
    """verify, rescore and mutate read the worktree patch through diff_since,
    and the hook reads one file's patch beside the agent's own git commands. A
    plain `git diff` refreshes a touched file's index entry and writes the
    index back, --no-optional-locks or not."""
    _commit_one_crlf_file(tmp_path)
    _touch(tmp_path / "src" / "a.ts")
    before = _index_state(tmp_path)

    assert PATCHES[reader](tmp_path) == ""
    assert _index_state(tmp_path) == before


@pytest.mark.parametrize("reader", sorted(PATCHES))
def test_a_patch_read_still_carries_an_edit(repo, reader):
    """With the stat refresh off, a same-bytes file prints no patch and new
    bytes still do."""
    _touch(repo / "src" / "a.ts")
    assert PATCHES[reader](repo) == ""

    (repo / "src" / "a.ts").write_bytes(b"export const a = 2;\r\n")

    assert "\n+export const a = 2;" in PATCHES[reader](repo)


def test_the_binary_source_read_leaves_the_index_as_it_found_it(tmp_path):
    """Under `*.py -diff` git prints `Binary files ... differ` for a Python
    file, so diff_since asks `git diff --numstat` which binary files are
    source and reads their patch again with --text. The numstat read is a
    worktree diff too: with the stat refresh on it rewrote the index over a
    touched file, as the plain patch read did."""
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".gitattributes").write_bytes(b"*.py -diff\n")
    (tmp_path / "src").mkdir()
    for name in ("touched.py", "edited.py"):
        (tmp_path / "src" / name).write_bytes(b"def f():\n    return 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    _touch(tmp_path / "src" / "touched.py")
    (tmp_path / "src" / "edited.py").write_bytes(b"def f():\n    return 2\n")
    before = _index_state(tmp_path)

    patch = diff_since(tmp_path, "HEAD")

    assert _index_state(tmp_path) == before
    assert "\n+    return 2" in patch
    assert "src/touched.py" not in patch


def test_status_names_each_kind_of_change_from_a_root_below_the_top(tmp_path):
    """git status names every path from the repo top; the readers hand back
    paths relative to crapkit's root, the way ls-files spells them there."""
    _commit_one_crlf_file(tmp_path)
    root = tmp_path / "app"
    (root / "src").mkdir(parents=True)
    for name in ("staged.py", "edited.py", "gone.py"):
        (root / "src" / name).write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "app")
    (root / "src" / "staged.py").write_text("x = 2\n", encoding="utf-8")
    _git(root, "add", "src/staged.py")
    (root / "src" / "edited.py").write_text("x = 3\n", encoding="utf-8")
    (root / "src" / "gone.py").unlink()
    (root / "src" / "new.py").write_text("x = 4\n", encoding="utf-8")
    (tmp_path / "src" / "a.ts").write_bytes(b"outside the root\r\n")

    assert _scoped(root) == ("src/edited.py", "src/gone.py", "src/new.py", "src/staged.py")
    assert status_names(root) == ["src/edited.py", "src/gone.py", "src/new.py", "src/staged.py"]
    assert unstaged_paths(root) == {"src/edited.py", "src/gone.py"}


def test_a_path_both_deleted_from_the_index_and_left_untracked_is_named_once(tmp_path):
    """`git rm --cached` makes git status print the file twice, once staged as
    deleted and once untracked."""
    _commit_one_crlf_file(tmp_path)
    _git(tmp_path, "rm", "-q", "--cached", "src/a.ts")

    assert status_names(tmp_path) == ["src/a.ts"]
    assert _scoped(tmp_path) == ("src/a.ts",)
    assert unstaged_paths(tmp_path) == set()


@pytest.fixture()
def default_repo(tmp_path: Path) -> Path:
    """One LF source file under git's default config, its index entry settled
    well before the index was written, so only a later touch makes it stat-dirty."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "a.ts"
    source.write_bytes(b"export const a = 1;\n")
    earlier = source.stat().st_mtime - 60
    os.utime(source, (earlier, earlier))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return tmp_path


def _reads_after_a_touch(repo: Path) -> None:
    from crapkit.gitio import worktree_changes

    _touch(repo / "src" / "a.ts")
    with ChangeReads(repo, (), ("src",)) as reads:
        reads.status_names()
    status_names(repo)
    worktree_changes(repo, ("src",))


def _environments(monkeypatch) -> list[dict]:
    """Every environment crapkit hands a git process from here on."""
    import subprocess as sp

    seen: list[dict] = []
    real_run, real_popen = sp.run, sp.Popen

    def run(argv, *args, **kwargs):
        seen.append(kwargs.get("env") or {})
        return real_run(argv, *args, **kwargs)

    class Popen(real_popen):
        def __init__(self, argv, *args, **kwargs):
            seen.append(kwargs.get("env") or {})
            super().__init__(argv, *args, **kwargs)

    monkeypatch.setattr(sp, "run", run)
    monkeypatch.setattr(sp, "Popen", Popen)
    return seen


def test_every_git_process_takes_no_optional_lock(default_repo, monkeypatch):
    """GIT_OPTIONAL_LOCKS=0 is git's own spelling of --no-optional-locks: a
    read never writes the index for its own convenience, and a write takes the
    lock it needs anyway."""
    from crapkit import gitio

    seen = _environments(monkeypatch)
    commit = gitio.head_commit(default_repo)
    gitio.is_ancestor(default_repo, commit)
    gitio.has_commit(default_repo, commit)
    gitio.is_shallow(default_repo)
    gitio.index_blobs(default_repo, ("src",))
    gitio.worktree_blobs(default_repo, ["src/a.ts"])
    gitio.staged_blobs(default_repo, ["src/a.ts"])
    list(gitio._git_lines(default_repo, "log", "--format=%H"))
    _reads_after_a_touch(default_repo)
    gitio.stage_path(default_repo, "src/a.ts")

    assert seen and all(env.get("GIT_OPTIONAL_LOCKS") == "0" for env in seen)
