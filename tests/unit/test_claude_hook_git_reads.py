"""What claude-hook does when git cannot say what an edit changed.

The advisory judges only the functions an edit changed, and it learns them from
`git diff HEAD -- <file>`, then `git ls-files` when the diff is empty. Both
answers used to fold a failure into a verdict: a failed diff read as "nothing
changed" for any file the index lists, and a failed ls-files read as
"untracked, judge everything". So a repo before its first commit went silent
about a staged breach, and a corrupt index made a one-line edit answer for
every legacy function in the file.

Each row feeds one real PostToolUse Edit payload through the real ladder.
"""
from __future__ import annotations

import subprocess

import pytest

from crapkit.cli import claude_hook
from claude_hook_repo import (BREACH, CLEAN, HEAD_LINE, TOML, edit_event, git, hook,
                              measured, write)

LEGACY = BREACH  # a ccn-8 function HEAD already carries
SMALL = "\n\ndef small(x):\n    return x + 1\n"
BODY = "  ccn 8  calc/grade.py:1  sprawl( n )"
CLOSING = "the commit gate enforces this; decompose there or mark the debt"


def _unborn(tmp_path, *, staged: bool):
    repo = measured(tmp_path, commit=False)
    path = write(repo, "calc/grade.py", BREACH)
    if staged:
        git(repo, "add", "-A")
    return path


# --- the repository before its first commit ---------------------------------

@pytest.mark.parametrize("staged", [True, False], ids=["unborn-staged", "unborn-untracked"])
def test_before_the_first_commit_a_file_is_judged_whole(staged, tmp_path, monkeypatch, capsys):
    """`git diff HEAD` exits 128 with no HEAD, and the staged file drew silence
    while the same file left untracked drew the advisory. With no commit, every
    function in the file is new."""
    path = _unborn(tmp_path, staged=staged)

    assert hook(monkeypatch, capsys, edit_event(path)) == (2, [HEAD_LINE, BODY, CLOSING])


def test_a_committed_repo_judges_only_what_the_edit_added(tmp_path, monkeypatch, capsys):
    """Control: HEAD holds a clean file, the edit adds the breach."""
    repo = measured(tmp_path)
    path = write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)

    code, err = hook(monkeypatch, capsys, edit_event(path))

    assert (code, err[0]) == (2, HEAD_LINE)
    assert err[1:] == ["  ccn 8  calc/grade.py:7  sprawl( n )", CLOSING]


# --- a git that runs and fails ----------------------------------------------

def _legacy_repo(tmp_path):
    """HEAD carries a ccn-8 function; the edit appends one small one."""
    repo = measured(tmp_path)
    write(repo, "calc/grade.py", LEGACY)
    git(repo, "commit", "-qam", "legacy")
    return repo, write(repo, "calc/grade.py", LEGACY + SMALL)


def test_an_edit_beside_legacy_debt_is_silent_when_git_answers(tmp_path, monkeypatch, capsys):
    """Control: the edit touched `small`, and `sprawl` is not its to answer for."""
    _, path = _legacy_repo(tmp_path)

    assert hook(monkeypatch, capsys, edit_event(path)) == (0, [])


def test_a_corrupt_index_is_named_and_no_untouched_function_is_listed(tmp_path, monkeypatch,
                                                                       capsys):
    """`git diff HEAD` and `git ls-files` both exit 128. The failed ls-files
    read as "untracked", so the advisory listed `sprawl`, which the edit never
    touched. It now says git could not report the change and quotes git's
    error."""
    repo, path = _legacy_repo(tmp_path)
    (repo / ".git" / "index").write_bytes(b"DIRC garbage")

    code, err = hook(monkeypatch, capsys, edit_event(path))

    assert code == 2
    assert err[0] == ("crapkit advisory: git could not report what changed in calc/grade.py, "
                      "so no function in it was judged (the edit landed; nothing was blocked)")
    assert err[1].startswith("  git diff HEAD -- calc/grade.py: fatal: "), err
    assert "index file" in err[1] and "--output-indicator" not in err[1], err
    assert err[2] == claude_hook._GIT_NEXT
    assert not any("sprawl" in line for line in err), err


def test_a_failed_ls_files_is_named_rather_than_read_as_untracked(tmp_path, monkeypatch):
    """The second read: the diff came back empty, and `git ls-files` failing
    used to mean "untracked, judge every function"."""
    failed = subprocess.CompletedProcess([], 128, "", "fatal: index file corrupt\n")
    monkeypatch.setattr(claude_hook.subprocess, "run", lambda *a, **k: failed)

    answer = claude_hook._listed(tmp_path, "calc/grade.py")

    assert answer.reason == "git ls-files -- calc/grade.py: fatal: index file corrupt"


@pytest.mark.parametrize("message,said", [
    ("git diff HEAD failed in {root}: fatal: bad object HEAD", "fatal: bad object HEAD"),
    ("git executable not found", "git executable not found"),
], ids=["git-spoke", "no-root-in-message"])
def test_the_named_error_is_gits_own_words(message, said, tmp_path):
    assert claude_hook._git_said(Exception(message.format(root=tmp_path)), tmp_path) == said


@pytest.mark.parametrize("listed,ranges", [("calc/grade.py\n", []), ("", None)],
                         ids=["tracked", "untracked"])
def test_an_answered_ls_files_still_splits_tracked_from_untracked(listed, ranges, tmp_path,
                                                                  monkeypatch):
    answered = subprocess.CompletedProcess([], 0, listed, "")
    monkeypatch.setattr(claude_hook.subprocess, "run", lambda *a, **k: answered)

    assert claude_hook._listed(tmp_path, "calc/grade.py") == ranges


def test_a_glob_character_in_the_name_is_read_literally(tmp_path, monkeypatch, capsys):
    """`git ls-files -- calc/[id].py` is a glob that also matches a tracked
    `calc/i.py`, which read the new untracked file as tracked and unchanged."""
    repo = measured(tmp_path)
    write(repo, "calc/i.py", CLEAN)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "sibling")
    path = write(repo, "calc/[id].py", BREACH)

    code, err = hook(monkeypatch, capsys, edit_event(path))

    assert (code, err[1]) == (2, "  ccn 8  calc/[id].py:1  sprawl( n )")


# --- no repository, no git --------------------------------------------------

def test_a_measured_directory_outside_any_repository_is_judged_whole(tmp_path, monkeypatch,
                                                                     capsys):
    """A copied or unpacked tree that ships a crapkit.toml and no `.git` has no
    commit to diff against, as a repo before its first commit has none: every
    function in the file is new."""
    write(tmp_path, "crapkit.toml", TOML)
    path = write(tmp_path, "calc/grade.py", BREACH + "\n\n" + CLEAN)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    assert hook(monkeypatch, capsys, edit_event(path)) == (2, [HEAD_LINE, BODY, CLOSING])


def test_git_missing_from_path_leaves_an_edit_silent(tmp_path, monkeypatch, capsys):
    """A machine with no git has no commit gate either; exit 0 and silence is
    the contract, as the Bash half keeps it."""
    repo = measured(tmp_path)
    path = write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)
    monkeypatch.setenv("PATH", str(tmp_path / "no-git-here"))

    assert hook(monkeypatch, capsys, edit_event(path)) == (0, [])
