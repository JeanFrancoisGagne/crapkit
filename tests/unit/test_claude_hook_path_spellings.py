r"""The advisory hook reads a payload's path the way the edit's own disk does.

`_judge_path` keyed the edit by the payload's text below the root, and
`_edited_path` and the Bash fallback took `/c/...` as a path on the current
drive. A model on Windows writes all of these: its Edit calls carry `C:\`,
`c:\`, `C:/` and `/c/` spellings, and the session cwd a Bash event reports is
`/c/Users/...`. On a case-insensitive disk `...\CALC\mod.py` opened the edited
file and matched no scope, so a breach went unadvised, and `...\calc\Mod.py`
read a tracked file as untracked and advised legacy debt the edit never
touched. `git ls-files` also read a new file's name as a glob, so a new
`calc/[ab]/mod.py` beside a tracked `calc/a/mod.py` read as tracked and
unchanged. Each test drives the whole subcommand in process with a real git
repo under tmp_path.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from cli_inproc_repo import commit_all, git
from crapkit.cli import main

from path_spellings import (linked_checkout, lower_drive, need, need_case_insensitive, only_posix,
                            only_windows)

_BRANCHES = "".join(f"    if n == {i}:\n        n += {i}\n" for i in range(1, 8))
BREACH = f"def sprawl(n):\n{_BRANCHES}    return n\n"  # ccn 8, over the ceiling of 6
CLEAN = "def tidy(n):\n    return n + 1\n"
TOML = ('[crapkit]\ntarget = 6\n\n'
        '[[scope]]\nname = "calc"\npaths = ["calc"]\nlanguages = ["python"]\n')


def _repo(root: Path, committed: dict[str, str]) -> Path:
    root.mkdir(parents=True)
    (root / "crapkit.toml").write_text(TOML, encoding="utf-8")
    for rel, text in committed.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    git(root, "init", "-q")
    commit_all(root, "init")
    return root


def _hook(monkeypatch, capsys, payload: dict) -> tuple[int, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = main(["claude-hook", "--protocol", "1"])
    return code, capsys.readouterr().err


def _edit(file_path: str, cwd: Path) -> dict:
    return {"hook_event_name": "PostToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": file_path}, "cwd": str(cwd)}


@pytest.fixture()
def breached(tmp_path: Path) -> Path:
    """calc/mod.py committed clean, then edited into a breach."""
    root = _repo(tmp_path / "repo", {"calc/mod.py": CLEAN})
    (root / "calc" / "mod.py").write_text(CLEAN + "\n\n" + BREACH, encoding="utf-8")
    return root.resolve()


def _msys(path: Path) -> str:
    posix = path.as_posix()
    return "/" + posix[0].lower() + posix[2:]


# id -> (need, spelling of calc/mod.py under the resolved root)
BREACH_SPELLINGS = {
    "absolute": ("", lambda root: str(root / "calc" / "mod.py")),
    "relative-to-cwd": ("", lambda root: "calc/mod.py"),
    "dot-dot-segment": ("", lambda root: str(root / "calc" / ".." / "calc" / "mod.py")),
    "forward-slashes": ("windows", lambda root: (root / "calc" / "mod.py").as_posix()),
    "lower-drive": ("windows", lambda root: str(root)[0].lower() + str(root)[1:] + "\\calc\\mod.py"),
    "msys": ("windows", lambda root: _msys(root / "calc" / "mod.py")),
    "dir-case": ("case", lambda root: str(root / "CALC" / "mod.py")),
    "file-case": ("case", lambda root: str(root / "calc" / "MOD.PY")),
    "upper-cased": ("windows case", lambda root: str(root / "calc" / "mod.py").upper()),
}


def _need(need: str, root: Path) -> None:
    if "windows" in need and os.name != "nt":
        pytest.skip("needs Windows path rules")
    if "case" in need:
        need_case_insensitive(root)


@pytest.mark.parametrize("which", BREACH_SPELLINGS)
def test_a_breach_is_advised_whatever_the_payload_spells(breached, monkeypatch, capsys, which):
    need, spell = BREACH_SPELLINGS[which]
    _need(need, breached)

    code, err = _hook(monkeypatch, capsys, _edit(spell(breached), breached))

    assert code == 2, err
    assert "calc/mod.py" in err and "sprawl" in err, err


# id -> (need, file_path, the event's cwd): a relative path is read against the
# cwd the event reports, in whatever spelling Claude Code wrote it.
RELATIVE_EDITS = {
    "up-from-a-subdirectory": ("", lambda root: "../calc/mod.py", lambda root: root / "calc"),
    "linked-checkout": ("", lambda root: "calc/mod.py", linked_checkout),
    "linked-checkout-absolute": ("", lambda root: str(linked_checkout(root) / "calc" / "mod.py"),
                                 lambda root: root),
    "lower-drive-cwd": ("windows", lambda root: "calc/mod.py", lower_drive),
    "backslash-lower-drive-cwd": ("windows", lambda root: "calc\\mod.py", lower_drive),
    "up-backslash-from-a-subdirectory": ("windows", lambda root: "..\\calc\\mod.py",
                                         lambda root: root / "calc"),
    "msys-cwd": ("windows", lambda root: "calc/mod.py", _msys),
    "dir-case-backslash": ("windows case", lambda root: "CALC\\mod.py", lambda root: root),
    "file-case-backslash": ("windows case", lambda root: "calc\\MOD.PY", lambda root: root),
}


@pytest.mark.parametrize("which", RELATIVE_EDITS)
def test_a_relative_edit_is_read_against_the_cwd_in_any_spelling(breached, monkeypatch, capsys,
                                                                 which):
    spec, file_path, cwd = RELATIVE_EDITS[which]
    need(spec, breached)

    code, err = _hook(monkeypatch, capsys, _edit(file_path(breached), cwd(breached)))

    assert code == 2, err
    assert "calc/mod.py" in err and "sprawl" in err, err


def _bash(cwd: str) -> dict:
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": cwd,
            "tool_input": {"command": "python - <<'PY'\nPY"}}


# id -> (need, the cwd a Bash event reports, under the resolved root)
BASH_CWDS = {
    "native": ("", str),
    "subdirectory": ("", lambda root: str(root / "calc")),
    "linked-checkout": ("", lambda root: str(linked_checkout(root))),
    "forward-slashes": ("windows", lambda root: root.as_posix()),
    "lower-drive": ("windows", lower_drive),
    "dir-case": ("windows case", lambda root: str(root.parent / root.name.upper())),
    "msys": ("windows", _msys),
}


@pytest.mark.parametrize("which", BASH_CWDS)
def test_a_bash_written_breach_is_advised_whatever_the_cwd_spells(breached, monkeypatch, capsys,
                                                                  which):
    """The opt-in Bash matcher reads the tree from the event's cwd, which is
    the session's own directory in the spelling Claude Code keeps."""
    spec, cwd = BASH_CWDS[which]
    need(spec, breached)

    code, err = _hook(monkeypatch, capsys, _bash(cwd(breached)))

    assert code == 2, err
    assert "calc/mod.py" in err, err


def test_a_clean_edit_beside_legacy_debt_stays_silent_in_another_case(tmp_path, monkeypatch,
                                                                      capsys):
    """`calc\\Mod.py` read the tracked calc/mod.py as untracked, so the whole
    file was judged and the committed debt the edit never touched was advised."""
    root = _repo(tmp_path / "repo", {"calc/mod.py": BREACH + "\n\n" + CLEAN}).resolve()
    need_case_insensitive(root)
    (root / "calc" / "mod.py").write_text(BREACH + "\n\n" + CLEAN.replace("+ 1", "+ 2"),
                                          encoding="utf-8")

    code, err = _hook(monkeypatch, capsys, _edit(str(root / "calc" / "Mod.py"), root))

    assert code == 0, err


@only_windows
def test_a_bash_event_whose_cwd_is_spelled_for_git_bash_still_reads_the_tree(breached,
                                                                              monkeypatch, capsys):
    """Claude Code on Windows reports a Bash call's cwd as `/c/Users/...`. Read
    as a Windows path it named no directory, so a heredoc-written breach was
    never advised while the session stood there."""
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": _msys(breached),
               "tool_input": {"command": "python - <<'PY'\nPY"}}

    code, err = _hook(monkeypatch, capsys, payload)

    assert code == 2, err
    assert "calc/mod.py" in err, err


def _new_file_beside(tmp_path: Path, tracked: str, new: str) -> Path:
    root = _repo(tmp_path / "repo", {tracked: CLEAN}).resolve()
    (root / new).parent.mkdir(parents=True, exist_ok=True)
    (root / new).write_text(BREACH, encoding="utf-8")
    return root


@pytest.mark.parametrize("tracked, new", [("calc/new_plain.py", "calc/plain.py"),
                                          ("calc/a/mod.py", "calc/[ab]/mod.py"),
                                          ("calc/c/mod.py", "calc/[ab]/mod.py")])
def test_a_new_file_named_like_a_glob_is_judged_in_full(tmp_path, monkeypatch, capsys, tracked,
                                                        new):
    """`git ls-files -- calc/[ab]/mod.py` lists the tracked calc/a/mod.py, so
    the new file read as tracked with no changed lines and its breach was
    never advised."""
    root = _new_file_beside(tmp_path, tracked, new)

    code, err = _hook(monkeypatch, capsys, _edit(str(root / new), root))

    assert code == 2, err


@only_posix
@pytest.mark.parametrize("tracked, new", [("calc/x/mod.py", "calc/*/mod.py"),
                                          ("calc/weird.py", "calc/we\\ird.py")])
def test_posix_judges_a_new_file_whose_name_git_would_read_as_a_pattern(tmp_path, monkeypatch,
                                                                       capsys, tracked, new):
    """`*` and a backslash are filename characters on POSIX; `we\\ird.py` read
    as a pathspec matched the tracked weird.py."""
    root = _new_file_beside(tmp_path, tracked, new)

    code, err = _hook(monkeypatch, capsys, _edit(str(root / new), root))

    assert code == 2, err
