"""A measured git repo and one `crapkit claude-hook` call against it, in-process.

The claude-hook matrix tests drive the real ladder: real git, real lizard, and
the session memory on disk under the repo's git directory. Each call feeds one
PostToolUse payload through `crapkit.cli.main` and returns the exit code and
the stderr lines, which is everything protocol 1 ever says.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import time
from pathlib import Path

from crapkit.cli import main

TOML = ('[crapkit]\ntarget = 6\n\n'
        '[[scope]]\nname = "calc"\npaths = ["calc"]\nlanguages = ["python"]\n')
CLEAN = "def grade(n):\n    if n > 1:\n        return n + 1\n    return n\n"
_BRANCHES = "".join(f"    if n == {i}:\n        n += {i}\n" for i in range(1, 8))
# ccn 8, over the ceiling of 6. lizard names it `sprawl( n )`.
BREACH = f"def sprawl(n):\n{_BRANCHES}    return n\n"
SESSION = "6f8a1d2e-0b3c-4d5e-8f90-1a2b3c4d5e6f"
HEAD_LINE = ("crapkit advisory: 1 function(s) over ceiling 6 in calc/grade.py "
             "(the edit landed; nothing was blocked)")


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=repo, check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return done.stdout


def write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def age(path: Path, seconds: float) -> None:
    """Move the file's times `seconds` into the past, as time passing would."""
    then = time.time() - seconds
    os.utime(path, (then, then))


def measured(tmp_path: Path, *, commit: bool = True, toml: str = TOML) -> Path:
    """A repo on `main` holding crapkit.toml and a clean calc/grade.py,
    committed unless `commit` is False."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "crapkit.toml", toml)
    write(repo, "calc/grade.py", CLEAN)
    if commit:
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
    return repo


def edit_event(path: Path, session=SESSION) -> dict:
    event = {"hook_event_name": "PostToolUse", "tool_name": "Edit",
             "tool_input": {"file_path": str(path)}, "cwd": str(path.parent)}
    return _with_session(event, session)


def bash_event(repo: Path, command: str = "python gen.py", session=SESSION) -> dict:
    event = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
             "tool_input": {"command": command}, "cwd": str(repo)}
    return _with_session(event, session)


def _with_session(event: dict, session) -> dict:
    """Claude Code sends `session_id` on every event; None leaves it out."""
    return event if session is None else {"session_id": session, **event}


def hook(monkeypatch, capsys, payload: dict) -> tuple[int, list[str]]:
    """One protocol-1 call: the exit code and the stderr lines."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = main(["claude-hook", "--protocol", "1"])
    return code, capsys.readouterr().err.splitlines()
