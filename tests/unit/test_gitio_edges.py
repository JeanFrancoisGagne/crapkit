"""gitio at its edges: each read's exact answer and the exact words of each refusal.

A path that starts with a dash reaches git after `--`, so it never reads as an
option. A rename is two names in a name-only diff and one pair at git's own 50%
similarity default, whatever diff.renames says. Every refusal names the command
the caller asked for and what git said, with a byte that is not UTF-8 read as
U+FFFD, and a missing git says so in four words. Every read runs without
GIT_DIFF_OPTS and with GIT_OPTIONAL_LOCKS=0, and a pinned variable reaches the
one read that pins it. A git that dies (exit 128 or more) is first asked what
the repository lacks; the fakes below stand in a repository git opens, so the
refusal names the command.
"""
import io
import os
import subprocess
from pathlib import Path

import pytest

from crapkit import gitio, procs
from crapkit.errors import GitError

ROOT = Path("repo")
BINARY = {"bin.py": b"a\0b", "two.py": b"c\0d", "-dash.py": b"e\0f"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          encoding="utf-8").stdout


def commit(repo: Path, message: str = "one") -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


def write(repo: Path, files: dict) -> None:
    for name, body in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_bytes(body)


def refusal(call) -> str:
    with pytest.raises(GitError) as caught:
        call()
    return str(caught.value)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    for key, value in (("user.name", "t"), ("user.email", "t@t"), ("core.autocrlf", "false"),
                       ("diff.renames", "true"), ("commit.gpgsign", "false")):
        git(root, "config", key, value)
    return root


def an_open_repository(monkeypatch) -> None:
    """The repository probe a failed read makes, answered: nothing is missing."""
    monkeypatch.setattr(gitio, "_repository_gap", lambda root: None)


def fake_popen(monkeypatch, out: bytes = b"", err: bytes = b"", code: int = 0) -> list:
    """A git process that prints `out` and `err` and exits with `code`."""
    calls = []

    class Proc:
        def __init__(self, argv, **kwargs):
            calls.append((argv, kwargs))
            self.stdout, self.stderr, self.returncode = io.BytesIO(out), io.BytesIO(err), None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.returncode = code

        def communicate(self, payload=None):
            self.returncode = code
            return out, err

    monkeypatch.setattr(gitio.subprocess, "Popen", Proc)
    return calls


def fake_spawn(monkeypatch, code: int, out, err) -> list:
    calls = []

    def spawn(root, argv, *, binary=False, pinned=()):
        calls.append((argv, binary, pinned))
        return subprocess.CompletedProcess(argv, code, out, err)

    monkeypatch.setattr(gitio, "_spawn", spawn)
    return calls


class WorktreeGit:
    """Records the owner each worktree command ran under, raising `failures` in order."""

    def __init__(self, *failures: GitError) -> None:
        self.owners, self.failures = [], list(failures)

    def __call__(self, root, *args, owner=None):
        self.owners.append(owner)
        if self.failures:
            raise self.failures.pop(0)
        return ""


def test_untracked_files_are_the_ones_git_add_would_take_wherever_the_caller_stands(
        repo, tmp_path, monkeypatch):
    write(repo, {".gitignore": b"build/\n", "kept.py": b""})
    commit(repo)
    write(repo, {"new two.py": b"", "sub/b.py": b"", "build/out.py": b""})
    monkeypatch.chdir(tmp_path)

    assert gitio.untracked_files(repo) == ["new two.py", "sub/b.py"]


def test_index_modes_read_the_executable_bit_under_a_pathspec_that_starts_with_a_dash(repo):
    write(repo, {"-dir/x.py": b"x\n", "lib/a.py": b"a\n", "run.sh": b"echo\n"})
    git(repo, "add", "-A")
    git(repo, "update-index", "--chmod=+x", "run.sh")

    assert gitio.index_modes(repo, "-dir") == {"-dir/x.py": "100644"}
    assert gitio.index_modes(repo, ".") == {"-dir/x.py": "100644", "lib/a.py": "100644",
                                             "run.sh": "100755"}


def test_a_rename_is_both_names_and_a_dirty_worktree_is_not_a_commit(repo, tmp_path, monkeypatch):
    write(repo, {"f.txt": b"aaaaaaaaaa\nbbbbbbbbbb\n", "h.txt": b"h\n"})
    first = commit(repo)
    git(repo, "mv", "f.txt", "g.txt")
    commit(repo, "two")
    write(repo, {"h.txt": b"edited\n"})
    monkeypatch.chdir(tmp_path)

    assert gitio.diff_names_since(repo, first) == ["f.txt", "g.txt"]
    assert gitio.GitFacts(repo).diff_names_since(first) == ("f.txt", "g.txt")


def test_a_rename_at_exactly_half_similar_pairs_up_with_detection_off_and_the_worktree_edited(repo):
    git(repo, "config", "diff.renames", "false")
    write(repo, {"f.txt": b"aaaaaaaaaa\nbbbbbbbbbb\n"})
    first = commit(repo)
    git(repo, "mv", "f.txt", "g.txt")
    write(repo, {"g.txt": b"aaaaaaaaaa\ncccccccccc\n"})
    commit(repo, "two")
    write(repo, {"g.txt": b"unrelated\n"})

    assert gitio.renamed_paths(repo, first) == {"f.txt": "g.txt"}


def test_a_copy_record_holds_two_paths_and_pairs_nothing():
    fields = ["C75", "a.py", "b.py", "R90", "x.py", "y.py", "M", "z.py"]

    assert gitio._rename_pairs(fields) == {"x.py": "y.py"}


def test_status_records_keep_two_letters_and_cut_the_prefix():
    assert gitio.status_records("?? a.py\0 M sub/b.py\0", "") == [("??", "a.py"), (" M", "sub/b.py")]
    assert gitio.status_records(" M sub/b.py\0", "sub/\n") == [(" M", "b.py")]


def test_a_status_that_fails_names_the_step_that_failed(repo):
    write(repo, {"a.txt": b"a\n"})
    commit(repo)
    (repo / ".git" / "index").write_bytes(b"garbage")
    broken = refusal(lambda: gitio._status(repo))

    assert broken.startswith("git --literal-pathspecs --no-optional-locks status --porcelain -z "
                             "-uall --no-renames ")
    assert f" failed in {repo}: fatal: " in broken


@pytest.mark.skipif(os.name == "nt", reason="Windows file names hold no CR or LF")
def test_a_prefix_holding_cr_lf_is_cut_byte_for_byte(repo):
    root = repo / "a\r\nb"
    write(root, {"f.py": b""})

    assert gitio._status(root) == [("??", "f.py")]


def test_a_streamed_read_decodes_utf8_by_lf_alone_and_carries_no_diff_opts(monkeypatch):
    monkeypatch.setenv("GIT_DIFF_OPTS", "-u9")
    calls = fake_popen(monkeypatch, out="café\r\nx\n".encode("utf-8"))

    assert list(gitio._git_lines(ROOT, "log")) == ["café\r\n", "x\n"]
    [(argv, kwargs)] = calls
    assert argv == ["git", *gitio._RELATIVE, "log"]
    assert kwargs["env"] == {**{key: value for key, value in os.environ.items() if key != "GIT_DIFF_OPTS"},
                             "GIT_OPTIONAL_LOCKS": "0"}


def test_a_failed_streamed_read_yields_what_came_and_then_names_the_command(monkeypatch):
    an_open_repository(monkeypatch)
    fake_popen(monkeypatch, out=b"a\n", err=b"fatal: bad \xff name\n", code=128)
    lines = []

    message = refusal(lambda: lines.extend(gitio._git_lines(ROOT, "log", "-1")))

    assert lines == ["a\n"]
    assert message == "git log -1 failed in repo: fatal: bad � name"


def test_a_missing_git_is_named_by_every_way_in(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    for module, name in ((gitio.subprocess, "Popen"), (gitio.subprocess, "run"), (procs, "run_owned")):
        monkeypatch.setattr(module, name, missing)

    assert [refusal(call) for call in (
        lambda: list(gitio._git_lines(ROOT, "log")),
        lambda: gitio._batch_stream(ROOT, b""),
        lambda: gitio._Started(ROOT, ("log",), stdin=False),
        lambda: gitio._worktree_git(ROOT, "status", owner=object()),
    )] == ["git executable not found"] * 4


def test_a_failed_batch_read_says_what_git_said(monkeypatch):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 128, b"", b"fatal: bad \xff\n")

    monkeypatch.setattr(gitio.subprocess, "run", run)
    an_open_repository(monkeypatch)

    assert refusal(lambda: gitio._batch_stream(ROOT, b"")) == (
        "git cat-file --batch failed in repo: fatal: bad �")


def test_a_failed_read_names_only_what_the_caller_asked(monkeypatch):
    fake_spawn(monkeypatch, 128, b"", b"  fatal: bad \xff name\n")
    an_open_repository(monkeypatch)

    assert refusal(lambda: gitio._run(ROOT, ("-c", "x=y", "log", "-1"), ("log", "-1"),
                                      binary=True)) == "git log -1 failed in repo: fatal: bad � name"


def test_a_commit_check_that_fails_says_so(monkeypatch):
    fake_spawn(monkeypatch, 128, "", "fatal: boom\n")

    refused = refusal(lambda: gitio.has_commit(ROOT, "abc"))

    assert refused.startswith("git rev-parse --verify ")
    assert refused.endswith(" failed in repo: fatal: boom")


def test_the_ancestry_reads_ask_with_git_untranslated(monkeypatch):
    calls = fake_spawn(monkeypatch, 1, b"", b"warning: \xff\n")

    assert gitio.is_ancestor(ROOT, "abc") is False
    assert calls == [(("merge-base", "--is-ancestor", "abc", "HEAD"), True, gitio.UNTRANSLATED)]

    calls = fake_spawn(monkeypatch, 0, "abc\n", "")
    assert gitio.merge_base(ROOT, "main") == "abc"
    assert calls == [((*gitio._RELATIVE, "merge-base", "main", "HEAD"), False, gitio.UNTRANSLATED)]


def test_an_exit_1_with_an_error_line_is_a_failed_merge_base_not_a_missing_one(monkeypatch):
    res = subprocess.CompletedProcess([], 1, "", "error: Could not read abc\n")

    def unreadable(root):
        raise GitError("rev-parse failed")

    monkeypatch.setattr(gitio, "is_shallow", unreadable)

    assert gitio._merge_base_refusal(ROOT, "main", res) == (
        "git merge-base main HEAD failed in repo: error: Could not read abc")
    assert [gitio._reports_failure(said) for said in ("error: x", "fatal: y", "warning: z\nhint")] == [
        True, True, False]
    assert gitio.shallow_fix(ROOT) == ""


def test_text_and_binary_reads_keep_the_bytes_git_printed(repo):
    """git reads are bytes, decoded by gitio and never by subprocess, so no
    read translates a CRLF the blob holds."""
    write(repo, {"crlf.txt": b"a\r\nb\r\n"})
    commit(repo)

    assert gitio._git(repo, "cat-file", "-p", "HEAD:crlf.txt") == "a\r\nb\r\n"
    assert gitio._git_bytes(repo, "cat-file", "-p", "HEAD:crlf.txt") == b"a\r\nb\r\n"
    assert gitio._git_unflagged(repo, "cat-file", "-p", "HEAD:crlf.txt") == "a\r\nb\r\n"


def test_git_diff_opts_reaches_no_read_but_the_one_that_pins_it(repo, monkeypatch):
    write(repo, {"nine.txt": b"1\n2\n3\n4\n5\n6\n7\n8\n9\n"})
    commit(repo)
    write(repo, {"nine.txt": b"1\n2\n3\n4\nfive\n6\n7\n8\n9\n"})
    commit(repo, "café")
    monkeypatch.setenv("GIT_DIFF_OPTS", "-u0")

    with procs.own_processes(()) as owner:
        owned = gitio._worktree_git(repo, "show", "--format=", "HEAD", owner=owner)
    pinned = gitio.start_read(repo, "show", "--format=", "HEAD", pinned=(("GIT_DIFF_OPTS", "-u1"),))

    assert "\n@@ -2,7 +2,7 @@\n" in gitio._spawn(repo, ("show", "--format=", "HEAD")).stdout
    assert "\n@@ -2,7 +2,7 @@\n" in owned
    assert b"\n@@ -4,3 +4,3 @@\n" in pinned.result()
    assert gitio._spawn(repo, ("log", "-1", "--format=%s")).stdout == "café\n"


def test_a_started_read_keeps_stderr_empty_on_success_and_text_on_failure(repo):
    write(repo, {"a.txt": b"a\n"})
    commit(repo, "café")
    fine = gitio.start_read(repo, "rev-parse", "HEAD")
    fine.result()
    failed = gitio._Started(repo, ("rev-parse", "--verify", "nope"), stdin=False)

    assert fine.stderr == ""
    assert refusal(failed.result).startswith(f"git rev-parse --verify nope failed in {repo}: fatal: ")
    assert failed.stderr.startswith("fatal: ")
    assert gitio._Started(repo, ("log", "-1", "--format=%s"), stdin=False).result() == "café\n".encode()


def test_a_started_read_reads_stderr_bytes_as_utf8_with_replacements(monkeypatch):
    fake_popen(monkeypatch, err=b"fatal: \xff\n", code=1)
    read = gitio._Started(ROOT, ("log",), stdin=False)

    assert refusal(read.result) == "git log failed in repo: fatal: �"
    assert read.stderr == "fatal: �\n"


def test_closing_a_read_nobody_collected_stops_it(repo):
    read = gitio._Started(repo, ("cat-file", "--batch"), stdin=True)

    read.close()

    assert read.returncode is not None


def test_the_history_and_head_refusals_say_what_is_missing(repo, monkeypatch):
    write(repo, {"a.txt": b"a\n"})
    commit(repo)
    commit_time = refusal(lambda: gitio.commit_time(repo, "HEAD..HEAD"))
    history = refusal(lambda: gitio._log_entries(b"junk"))
    monkeypatch.setattr(gitio, "head_from_refs", lambda root: None)
    monkeypatch.setattr(gitio, "_git", lambda root, *args, binary=False: "\n")

    assert commit_time == f"git rev-list named no commit date for HEAD..HEAD in {repo}"
    assert history == "Git patch history has no timestamp header"
    assert refusal(lambda: gitio.head_commit(ROOT)) == "no HEAD commit in repo"


def test_a_commit_named_like_a_file_is_read_as_the_commit(repo):
    write(repo, {"a.py": b"a\n"})
    commit(repo)
    git(repo, "tag", "a.py")

    assert [message[2:] for message in gitio.commit_messages(repo, ["a.py"])] == [("one", "")]


def test_a_message_byte_that_is_not_utf8_reads_as_a_replacement(monkeypatch):
    monkeypatch.setattr(gitio, "_git_bytes",
                        lambda root, *args: b"abc\x002026-09-29\x00caf\xe9\x00body\n\x00")

    assert gitio.commit_messages(ROOT, ["x"]) == [("abc", "2026-09-29", "caf�", "body\n")]


def test_a_path_that_starts_with_a_dash_is_staged(repo):
    write(repo, {"-dir/x.py": b"x\n"})

    gitio.stage_path(repo, "-dir/x.py")

    assert git(repo, "diff", "--cached", "--name-only") == "-dir/x.py\n"


def test_a_binary_record_keeps_its_path_whole():
    assert gitio._binary_source_path(b"-\t-\ta\tb.py", (".py",)) == "a\tb.py"
    assert gitio._binary_source_path(b"-\t-\t lead.py", (".py",)) == " lead.py"


def test_binary_sources_are_read_without_renames_and_only_for_the_paths_asked(repo):
    write(repo, BINARY)
    commit(repo)
    git(repo, "mv", "bin.py", "moved.py")
    write(repo, {"two.py": b"c\0dX", "-dash.py": b"e\0fX"})

    assert gitio._binary_source_paths(repo, ("--cached",), ()) == ("bin.py", "moved.py")
    assert gitio._binary_source_paths(repo, (), ("-dash.py",)) == ("-dash.py",)


def test_a_missing_commondir_leaves_the_git_dir_as_it_was_spelled():
    assert gitio._common_dir(Path("x/../g")) == Path("x/../g")


def test_a_worktrees_own_ref_wins_over_the_shared_one(tmp_path):
    write(tmp_path, {"wt/refs/x": b"a" * 40 + b"\n", "wt/commondir": b"../common\n",
                     "common/refs/x": b"b" * 40 + b"\n"})

    assert gitio._ref_sha(tmp_path / "wt", "refs/x") == "a" * 40


def test_a_blob_request_git_calls_missing_is_named_whole():
    assert refusal(lambda: gitio._framed_blobs(b":./caf\xe9.py missing\n", ["x"])) == (
        "git cat-file --batch: :./caf�.py is not in the index")


def test_a_path_holding_a_line_break_is_asked_for_alone():
    assert [gitio._line_paths(paths) for paths in (["a\rb"], ["a\nb"], ["a.py", "b.py"])] == [
        True, True, False]


def test_a_ref_file_is_read_as_utf8(tmp_path):
    write(tmp_path, {"HEAD": "gitdir: café\n".encode("utf-8")})

    assert gitio._file_text(tmp_path / "HEAD") == "gitdir: café"


def test_every_worktree_command_runs_under_the_callers_owner(tmp_path, monkeypatch):
    fake = WorktreeGit(GitError("fatal: failed to read .git/worktrees/w0/commondir: No error"))
    monkeypatch.setattr(gitio, "_worktree_git", fake)
    monkeypatch.setattr(gitio.time, "sleep", lambda seconds: None)
    owner = object()
    write(tmp_path, {".git": b""})

    gitio.worktree_add(ROOT, Path("t"), owner=owner)
    gitio.worktree_reset(tmp_path, "abc", owner=owner)

    assert fake.owners == [owner] * 4


def test_a_worktree_failure_that_is_not_the_commondir_scan_is_not_retried(monkeypatch):
    fake = WorktreeGit(GitError("fatal: failed to read .git/worktrees/w0/HEAD"))
    monkeypatch.setattr(gitio, "_worktree_git", fake)

    assert refusal(lambda: gitio.worktree_add(ROOT, Path("t"))) == (
        "fatal: failed to read .git/worktrees/w0/HEAD")
    assert fake.owners == [None]


def test_an_owned_worktree_command_honors_a_cancel_and_names_a_failure(repo):
    write(repo, {"a.txt": b"a\n"})
    commit(repo)
    with procs.own_processes(()) as owner:
        failed = refusal(lambda: gitio._worktree_git(repo, "rev-parse", "--verify", "nope", owner=owner))
        owner.cancel()
        with pytest.raises(procs.CommandCancelled):
            gitio._worktree_git(repo, "status", owner=owner)

    assert failed.startswith(f"git rev-parse --verify nope failed in {repo}: fatal: ")
