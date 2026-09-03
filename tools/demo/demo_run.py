"""Run the five demo commands for real and capture what they printed.

Nothing here writes an image. It produces a transcript: one entry per step,
holding the lines the frames will type, the captured stdout and stderr in
emission order, the exit status, and how long the frame holds afterwards.

Two rules the frames depend on. Every command runs against THIS checkout
(`python -m crapkit` with `src/` on PYTHONPATH), never whatever `crapkit` a PATH
resolves, so the output describes the tree the generator was run in. And every
captured line goes through `redact` before it reaches a frame, so a machine path
or a wall clock cannot land in a committed image.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent / "src"
PAYLOAD = "posttooluse.json"
COLUMNS = 104  # a worklist row with its score and coverage runs to 101 characters
ROWS = 32

# The frames print the console-script spelling because that is what a reader
# installs; the argv runs the module so the demo cannot measure a different
# crapkit than the one in this tree. Same code, same output, two spellings.
_SHOWN_NAME = "crapkit"

_ABSOLUTE = (re.compile(r"[A-Za-z]:[\\/]"),
             re.compile(r"(?:^|[\s(=\"'])/[A-Za-z0-9_.-]+/"))
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?")
_DURATION = re.compile(r"\b\d+\.\d+ ?s\b")


def _crapkit(*args: str) -> tuple[str, ...]:
    return (sys.executable, "-m", "crapkit", *args)


def _shown(*args: str) -> str:
    return " ".join((_SHOWN_NAME, *args))


def heredoc() -> str:
    """The shell line the demo writes the breaching function with, body included."""
    body = (HERE / "steps" / "heredoc_body.txt").read_text(encoding="utf-8")
    return f"cat >> calc/grade.py <<'PY'\n{body}PY\n"


def steps() -> tuple[dict, ...]:
    """The demo, in order. `shown` is what the frame types, `argv` is what runs."""
    return (
        {"shown": (_shown("init"),), "argv": _crapkit("init"), "hold": 5.0},
        {"shown": (_shown("coverage"),), "argv": _crapkit("coverage"), "hold": 9.0},
        {"shown": (_shown("worklist", "--top", "5"),),
         "argv": _crapkit("worklist", "--top", "5"), "hold": 18.0},
        {"shown": tuple(heredoc().rstrip("\n").split("\n")),
         "argv": ("bash", "-c", heredoc()), "hold": 5.0},
        {"shown": (f"{_shown('claude-hook', '--protocol', '1')} < {PAYLOAD}",),
         "argv": _crapkit("claude-hook", "--protocol", "1"), "stdin": PAYLOAD,
         "hold": 13.0},
        {"shown": ("git add calc/grade.py",),
         "argv": ("git", "add", "calc/grade.py"), "hold": 2.5},
        {"shown": (_shown("hook-precommit"),), "argv": _crapkit("hook-precommit"),
         "hold": 17.0},
    )


def write_payload(repo: Path) -> None:
    """The PostToolUse event Claude Code sends after a Bash tool call, shaped
    like tests/goldens/claude_hook/15_bash_written_breach.json. The advisory
    reads the fresh files the working tree changed, because a shell write
    carries a command and never a file_path."""
    root = str(repo).replace("\\", "/")
    payload = {"session_id": "6f8a1d2e-0b3c-4d5e-8f90-1a2b3c4d5e6f",
               "transcript_path": f"{root}/.claude/transcript.jsonl", "cwd": root,
               "permission_mode": "acceptEdits", "hook_event_name": "PostToolUse",
               "tool_name": "Bash",
               "tool_input": {"command": heredoc(),
                              "description": "Append final_grade through the shell"},
               "tool_response": {"stdout": "", "stderr": "", "interrupted": False}}
    (repo / PAYLOAD).write_text(json.dumps(payload), encoding="utf-8", newline="\n")


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("CRAPKIT_OVERRIDE_REASON", None)
    return env


def redact(text: str, repo: Path) -> str:
    """Everything that would differ between two runs, gone. The repo path is
    replaced first and in every spelling: a Windows tool prints backslashes, a
    shell prints forward ones, and the resolved path is a third string again.

    The module run goes too. Every next step crapkit prints spells crapkit the
    way it was started (`invocation._self`), which under `python -m crapkit` is
    the interpreter's absolute path; the frame shows the console script a
    reader installs, the same command by another name.
    """
    for spelling in _module_spellings():
        text = text.replace(spelling, _SHOWN_NAME)
    for spelling in _spellings(repo):
        text = text.replace(spelling, ".")
    text = _TIMESTAMP.sub("<time>", text)
    return _DURATION.sub("<duration>", text)


def _module_spellings() -> tuple[str, ...]:
    """`python -m crapkit` as `invocation._self` prints it: the interpreter,
    quoted when its path holds a space, in both separator styles."""
    forms = {sys.executable, sys.executable.replace("\\", "/")}
    quoted = {f'"{f}"' if " " in f else f for f in forms}
    return tuple(sorted((f"{q} -m crapkit" for q in quoted), key=len, reverse=True))


def _spellings(repo: Path) -> tuple[str, ...]:
    forms = {str(repo), str(repo).replace("\\", "/"), str(repo.resolve()),
             str(repo.resolve()).replace("\\", "/")}
    return tuple(sorted(forms, key=len, reverse=True))


def absolute_paths(lines: list[str]) -> list[str]:
    """Any captured line still carrying a machine path. The generator refuses to
    write an image when this is not empty: a redaction that missed is a frame
    that names somebody's home directory forever."""
    return [line for line in lines if any(p.search(line) for p in _ABSOLUTE)]


def _capture(repo: Path, step: dict) -> tuple[list, int]:
    with _open_stdin(repo, step) as handle:
        done = subprocess.run(step["argv"], cwd=repo, env=_env(), stdin=handle,
                              capture_output=True, text=True, encoding="utf-8")
    lines = [("out", line) for line in _split(done.stdout)]
    return lines + [("err", line) for line in _split(done.stderr)], done.returncode


def _open_stdin(repo: Path, step: dict):
    name = step.get("stdin")
    if name:
        return (repo / name).open("rb")
    return contextlib.nullcontext(subprocess.DEVNULL)


def _split(text: str) -> list[str]:
    return text.replace("\r\n", "\n").rstrip("\n").split("\n") if text.strip() else []


def record(repo: Path) -> dict:
    """Run every step against `repo` and return the transcript."""
    write_payload(repo)
    entries = []
    for step in steps():
        output, code = _capture(repo, step)
        entries.append({"shown": list(step["shown"]), "hold": step["hold"],
                        "output": [[s, redact(t, repo)] for s, t in output],
                        "exit": code})
    return {"columns": COLUMNS, "rows": ROWS, "steps": entries}


def require_bash() -> str:
    """The demo's fourth step is a heredoc, which cmd.exe cannot read."""
    found = shutil.which("bash")
    if not found:
        raise SystemExit("bash not found on PATH: the heredoc step needs it "
                         "(Git Bash on Windows)")
    return found
