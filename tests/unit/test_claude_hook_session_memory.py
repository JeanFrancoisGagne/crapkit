"""The Bash fallback advises each file's bytes once per session.

A Bash PostToolUse names no file, so the fallback judges the dirty *.py files
whose mtime sits inside the 12-second window. The window stands in for "this
command wrote it", and an mtime moves without new bytes: a `touch`, a tool that
rewrites the bytes it read, or a test run right after an Edit repeated an
advisory the session had already heard, as exit 2 the model must read again.

Every judgement now records the sha256 of the bytes it read, per session and
per file, under the repo's git directory. The fallback skips a fresh file
whose bytes match the record. The window and the 25-file cap stay as they
were, so content that lands with an old mtime is still never judged: that is
the documented miss, pinned below so a change to it is a decision.

Each row feeds real PostToolUse payloads through the real ladder, in order.
"""
from __future__ import annotations

import os
import shutil
import time

import pytest

from crapkit.cli import claude_hook
from claude_hook_repo import (BREACH, CLEAN, HEAD_LINE, SESSION, age, bash_event, edit_event,
                              git, hook, measured, write)

OTHER_SESSION = "0a1b2c3d-4e5f-4061-8293-a4b5c6d7e8f9"


def _bash_wrote_breach(tmp_path, monkeypatch, capsys):
    """A command wrote the breach just now and drew its advisory."""
    repo = measured(tmp_path)
    path = write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)
    first = hook(monkeypatch, capsys, bash_event(repo))
    assert first[0] == 2, first
    return repo, path


def _later_touch(path):
    """A later Bash call, past the window, that moves the mtime and no byte."""
    age(path, 60)
    path.touch()


# --- the rows that repeated an advisory ---------------------------------------

def test_a_touch_after_the_advisory_repeats_nothing(tmp_path, monkeypatch, capsys):
    """A later `touch` of the advised file moves its mtime into the window."""
    repo, path = _bash_wrote_breach(tmp_path, monkeypatch, capsys)
    _later_touch(path)

    assert hook(monkeypatch, capsys, bash_event(repo, "touch calc/grade.py")) == (0, [])


def test_a_same_bytes_rewrite_repeats_nothing(tmp_path, monkeypatch, capsys):
    """`sed -i` with no match rewrites the bytes it read."""
    repo, path = _bash_wrote_breach(tmp_path, monkeypatch, capsys)
    age(path, 60)
    path.write_bytes(path.read_bytes())

    command = "sed -i 's/nothing/nothing/' calc/grade.py"
    assert hook(monkeypatch, capsys, bash_event(repo, command)) == (0, [])


@pytest.mark.parametrize("command", ["python -m pytest -q", "touch calc/grade.py"])
def test_a_bash_call_right_after_an_edit_repeats_nothing(command, tmp_path, monkeypatch, capsys):
    """The Edit drew its advisory 3 s ago and the next command runs the tests.
    The file's mtime is inside the window, and its bytes are the ones the
    Edit's advisory judged."""
    repo = measured(tmp_path)
    path = write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)
    assert hook(monkeypatch, capsys, edit_event(path))[0] == 2
    age(path, 3)

    assert hook(monkeypatch, capsys, bash_event(repo, command)) == (0, [])


# --- the controls -------------------------------------------------------------

def test_a_breach_a_command_wrote_just_now_is_advised(tmp_path, monkeypatch, capsys):
    """Control: nothing judged these bytes before."""
    repo = measured(tmp_path)
    write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)

    code, err = hook(monkeypatch, capsys, bash_event(repo))

    assert (code, err[0]) == (2, HEAD_LINE)


def test_new_bytes_after_the_advisory_are_advised_again(tmp_path, monkeypatch, capsys):
    """Control: the record names bytes, and these are new."""
    repo, path = _bash_wrote_breach(tmp_path, monkeypatch, capsys)
    write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH.replace("return n", "return -n"))

    assert hook(monkeypatch, capsys, bash_event(repo, "sed -i s/n/-n/ calc/grade.py"))[0] == 2


def test_a_file_edited_a_minute_ago_and_a_command_that_writes_nothing_is_silent(
        tmp_path, monkeypatch, capsys):
    """Control: outside the window, with no record at all, the window alone
    keeps the old edit quiet."""
    repo = measured(tmp_path)
    path = write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)
    age(path, 60)

    assert hook(monkeypatch, capsys, bash_event(repo, "git status")) == (0, [])


def test_git_missing_from_path_leaves_a_bash_call_silent(tmp_path, monkeypatch, capsys):
    """Exit 0 and silence is the contract."""
    repo = measured(tmp_path)
    write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)
    monkeypatch.setenv("PATH", str(tmp_path / "no-git-here"))

    assert hook(monkeypatch, capsys, bash_event(repo)) == (0, [])


def test_bytes_that_left_and_came_back_are_advised_again(tmp_path, monkeypatch, capsys):
    """Each judgement records the bytes it read, a silent one included: the
    breach, then an Edit back to clean, then a command writing the breach
    again. Only the last judged bytes count, so the return is new."""
    repo, path = _bash_wrote_breach(tmp_path, monkeypatch, capsys)
    write(repo, "calc/grade.py", CLEAN)
    assert hook(monkeypatch, capsys, edit_event(path)) == (0, [])
    write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)

    assert hook(monkeypatch, capsys, bash_event(repo, "git stash pop"))[0] == 2


# --- the documented miss: new bytes under an old mtime ------------------------

def _old_copy(tmp_path, text: str):
    source = tmp_path / "elsewhere.py"
    source.write_bytes(text.encode("utf-8"))
    age(source, 3600)
    return source


@pytest.mark.parametrize("arrive", ["long-command", "copy-keeps-mtime", "mv-older-file",
                                    "cp-p-over-tracked"])
def test_new_bytes_that_land_with_an_old_mtime_are_the_documented_miss(
        arrive, tmp_path, monkeypatch, capsys):
    """A write more than 12 s before the event, `shutil.copy2`, `mv`, `cp -p`
    and an unpacked archive keep an mtime outside the window, so the fallback
    never looks at the file. The commit gate judges it; the advisory does not.
    The window and the cap stay as documented, so this miss stays too."""
    repo = measured(tmp_path)
    target = repo / "calc" / ("new.py" if arrive == "mv-older-file" else "grade.py")
    text = CLEAN + "\n\n" + BREACH
    if arrive == "long-command":
        age(write(repo, "calc/grade.py", text), 20)
    elif arrive == "mv-older-file":
        os.replace(_old_copy(tmp_path, text), target)
    else:
        shutil.copy2(_old_copy(tmp_path, text), target)

    assert hook(monkeypatch, capsys, bash_event(repo, f"# {arrive}")) == (0, [])


# --- what the memory is keyed on ----------------------------------------------

def test_another_session_hears_the_advisory(tmp_path, monkeypatch, capsys):
    """The record is per session: a new session never heard it."""
    repo, path = _bash_wrote_breach(tmp_path, monkeypatch, capsys)
    _later_touch(path)

    code, _ = hook(monkeypatch, capsys, bash_event(repo, "touch calc/grade.py",
                                                   session=OTHER_SESSION))
    assert code == 2


@pytest.mark.parametrize("session", [None, "", 17, "../escape", "a/b", "a\\b", "x" * 129],
                         ids=["absent", "empty", "not-a-string", "dot-dot", "slash",
                              "backslash", "too-long"])
def test_a_payload_with_no_usable_session_id_gets_no_memory(session, tmp_path, monkeypatch,
                                                           capsys):
    """Nothing to key a record on: every fresh file is judged, as before this
    memory existed, and nothing is written anywhere."""
    repo = measured(tmp_path)
    path = write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)
    assert hook(monkeypatch, capsys, bash_event(repo, session=session))[0] == 2
    _later_touch(path)

    assert hook(monkeypatch, capsys, bash_event(repo, session=session))[0] == 2
    assert not (repo / ".git" / "crapkit").exists()


def test_the_memory_lives_under_the_git_directory(tmp_path, monkeypatch, capsys):
    """The working tree stays byte-identical: `git status` sees nothing new."""
    repo, _ = _bash_wrote_breach(tmp_path, monkeypatch, capsys)

    assert (repo / ".git" / "crapkit" / "claude-hook" / SESSION).is_dir()
    assert git(repo, "status", "--porcelain", "-uall") == " M calc/grade.py\n"


def test_a_linked_worktree_keeps_its_memory_in_its_own_git_directory(tmp_path, monkeypatch,
                                                                     capsys):
    """A linked worktree's `.git` is a file naming its git directory."""
    main = measured(tmp_path)
    wt = tmp_path / "wt"
    git(main, "worktree", "add", "-q", str(wt))
    path = write(wt, "calc/grade.py", CLEAN + "\n\n" + BREACH)
    assert hook(monkeypatch, capsys, bash_event(wt))[0] == 2
    _later_touch(path)

    assert hook(monkeypatch, capsys, bash_event(wt, "touch calc/grade.py")) == (0, [])
    assert (main / ".git" / "worktrees" / "wt" / "crapkit" / "claude-hook" / SESSION).is_dir()


def test_a_named_git_failure_is_not_remembered(tmp_path, monkeypatch, capsys):
    """git's error says nothing about the bytes. Once git reads the repo again,
    the next command that touches the file draws the real verdict."""
    repo = measured(tmp_path)
    path = write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)
    index = (repo / ".git" / "index").read_bytes()
    (repo / ".git" / "index").write_bytes(b"DIRC garbage")
    named = hook(monkeypatch, capsys, edit_event(path))
    (repo / ".git" / "index").write_bytes(index)
    path.touch()

    code, err = hook(monkeypatch, capsys, bash_event(repo, "touch calc/grade.py"))

    assert (named[0], "git could not report" in named[1][0]) == (2, True)
    assert (code, err[0]) == (2, HEAD_LINE)


def test_an_idle_session_is_pruned_when_a_new_one_starts(tmp_path, monkeypatch, capsys):
    """One directory per session would pile up under .git forever. A session
    idle past _MEMORY_DAYS goes when another starts; a recent one stays."""
    repo = measured(tmp_path)
    sessions = repo / ".git" / "crapkit" / "claude-hook"
    idle, recent = sessions / "idle-session", sessions / "recent-session"
    for directory in (idle, recent):
        directory.mkdir(parents=True)
        (directory / "record").write_text("0" * 64, encoding="ascii")
    past = time.time() - (claude_hook._MEMORY_DAYS + 1) * 86400
    os.utime(idle, (past, past))
    write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)

    hook(monkeypatch, capsys, bash_event(repo))

    assert sorted(p.name for p in sessions.iterdir()) == sorted(["recent-session", SESSION])


def test_a_memory_that_cannot_be_written_changes_no_verdict(tmp_path, monkeypatch, capsys):
    """A read-only or full disk loses the record, never the advisory."""
    repo = measured(tmp_path)
    (repo / ".git" / "crapkit").write_text("a file where the directory goes", encoding="utf-8")
    path = write(repo, "calc/grade.py", CLEAN + "\n\n" + BREACH)
    assert hook(monkeypatch, capsys, bash_event(repo))[0] == 2
    _later_touch(path)

    assert hook(monkeypatch, capsys, bash_event(repo, "touch calc/grade.py"))[0] == 2
