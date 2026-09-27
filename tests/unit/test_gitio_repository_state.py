"""A git read that fails because of the repository, not the command, says so.

Three states make every git command fail whatever it asks: no repository at
all, a repository with no commit yet, and a repository git refuses to open
(the `safe.directory` ownership check). Each read used to pass on the stderr of
whichever command ran first. Outside a repository `git diff` falls back to its
`--no-index` mode, which rejects `--cached` and prints 129 lines of usage, and
`crapkit verify` in a copied tree printed exactly that. Before the first commit
`rev-parse HEAD` answers "ambiguous argument 'HEAD': unknown revision", which is
all `crapkit coverage` said at the end of the 60-second start.

git exits 128 when it dies and 129 on a usage error, so only those exits pay for
the one `rev-parse --verify --quiet HEAD` that tells the three states apart. An
exit 1 is an answer (`config --get` of an unset key, `merge-base --is-ancestor`
saying no) and costs no probe.
"""
import subprocess
from pathlib import Path

import pytest

from crapkit import gitio
from crapkit.errors import GitError


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout.strip()


def not_a_repository(root: Path) -> str:
    return (f"{root} is not a git repository, and no directory above it is one: crapkit reads "
            "the files it scores and the commit it measures from git, so run it inside a "
            "checkout, or run git init, git add and git commit here first")


def no_commit(root: Path) -> str:
    return (f"the git repository at {root} has no commit yet: crapkit measures a commit, so "
            "make the first one (git add, then git commit) and run it again")


def refusal(read) -> str:
    with pytest.raises(GitError) as refused:
        read()
    assert refused.value.exit_code == 4
    return str(refused.value)


@pytest.fixture()
def loose(tmp_path: Path, monkeypatch) -> Path:
    """A directory holding source and no repository, with git kept from finding
    one further up should the machine's temp directory sit inside a checkout."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    root = tmp_path / "loose"
    (root / "calc").mkdir(parents=True)
    (root / "calc" / "f.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    return root


@pytest.fixture()
def unborn(tmp_path: Path) -> Path:
    """`git init` and `git add`: the index holds the files, HEAD names no commit."""
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "f.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    return tmp_path


@pytest.fixture()
def committed(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "f.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "one")
    return tmp_path


def _staged_reads_diff(root: Path) -> str:
    with gitio.staged_reads(root) as reads:
        return reads.staged_diff()


READS = {
    "status_names": gitio.status_names,
    "unstaged_paths": gitio.unstaged_paths,
    "staged_diff": gitio.staged_diff,
    "staged_reads": _staged_reads_diff,
    "ls_files": gitio.ls_files,
    "head_commit": gitio.head_commit,
    "is_shallow": gitio.is_shallow,
    "file_log": lambda root: gitio.file_log(root, "calc/f.py"),
    "log_lines": lambda root: list(gitio._git_lines(root, "log")),
    "staged_blobs": lambda root: gitio.staged_blobs(root, ["calc/f.py"]),
    "merge_base": lambda root: gitio.merge_base(root, "main"),
}


@pytest.mark.parametrize("name", sorted(READS))
def test_outside_a_repository_every_read_says_so(loose, name):
    message = refusal(lambda: READS[name](loose))

    assert message == not_a_repository(loose)


HEAD_READS = {
    "head_commit": gitio.head_commit,
    "diff_since_head": lambda root: gitio.diff_since(root, "HEAD"),
    "file_log": lambda root: gitio.file_log(root, "f.py"),
    "log_lines": lambda root: list(gitio._git_lines(root, "log")),
    "merge_base": lambda root: gitio.merge_base(root, "main"),
    "is_ancestor": lambda root: gitio.is_ancestor(root, "HEAD"),
}


@pytest.mark.parametrize("name", sorted(HEAD_READS))
def test_before_the_first_commit_a_read_that_needs_head_says_to_make_it(unborn, name):
    """is_ancestor is here because it answered False, and verify then blamed a
    rebase for a baseline commit a repository with no commit cannot hold."""
    message = refusal(lambda: HEAD_READS[name](unborn))

    assert message == no_commit(unborn)


def test_before_the_first_commit_the_gates_reads_still_answer(unborn):
    """The commit gate runs on the first commit too: the index against the empty
    tree needs no HEAD, and nothing here may start refusing it."""
    assert gitio.status_names(unborn) == ["f.py"]
    assert "+def f(x):" in gitio.staged_diff(unborn)


def test_a_repository_git_will_not_open_quotes_gits_own_fix(committed, monkeypatch):
    """git's ownership check sends `git diff` into --no-index mode as well, and
    the usage it printed hid the one line that says what to run."""
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    if subprocess.run(["git", "rev-parse", "HEAD"], cwd=committed, capture_output=True).returncode == 0:
        pytest.skip("this git predates the safe.directory check")

    message = refusal(lambda: gitio.status_names(committed))

    assert message.startswith(f"git cannot open the repository at {committed}: fatal: ")
    assert "safe.directory" in message
    assert "usage:" not in message


def test_a_refusal_in_a_locale_that_is_not_utf8_still_reads(tmp_path, monkeypatch):
    """git words its refusal in the user's locale, and a Latin-1 one is not
    UTF-8. The probe runs on a path that is already failing, so a strict decode
    there would trade the refusal for a UnicodeDecodeError."""
    (tmp_path / ".git").mkdir()
    stderr = "fatal: d\xe9p\xf4t refus\xe9\n".encode("latin-1")
    refused = subprocess.CompletedProcess(["git"], 128, b"", stderr)
    monkeypatch.setattr(gitio, "_spawn", lambda root, argv, binary=False: refused)

    message = gitio._repository_gap(tmp_path)

    assert message == f"git cannot open the repository at {tmp_path}: fatal: d�p�t refus�"


def test_a_healthy_repository_keeps_the_commands_own_reason(committed):
    message = refusal(lambda: gitio.merge_base(committed, "missing-ref"))

    assert message.startswith(f"git merge-base missing-ref HEAD failed in {committed}: fatal: ")


def test_an_answer_git_gives_in_its_exit_code_costs_no_probe(committed, monkeypatch):
    """Exit 1 is how `config --get` says unset and `--is-ancestor` says no; both
    run on every doctor and every lane reuse check."""
    def probed(root):
        raise AssertionError(f"probed {root} after an exit 1")

    orphan = git(committed, "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit-tree", "HEAD^{tree}", "-m", "unrelated")
    monkeypatch.setattr(gitio, "_repository_gap", probed)

    assert gitio.config_value(committed, "crapkit.no-such-key") == ""
    assert gitio.is_ancestor(committed, orphan) is False
