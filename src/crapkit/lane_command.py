"""How a configured lane starts, and how its command reads.

A lane command runs under the shell, from `root / cwd`, with its `[lane.env]`
pairs merged over the process environment. lanes.py starts the lane and its
flake retest from `launch_spec`, and doctor resolves words and starts its
probes from the same spec: a word looked up from another directory, or on
another PATH, answers for a child the lane never starts.

The command line itself is read by `config.shell_words` and
`config.shell_segments`, the way the shell that runs it reads it. What this
module adds is the step that runs pytest and the python heading it: lanes.py
names that python in the hint after a lane fails for want of pytest-cov, and
doctor asks it whether pytest-cov imports, so the two cannot name different
words.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path, PurePath
from typing import NamedTuple

from .config import shell_segments, shell_words

_WINDOWS = os.name == "nt"


def _names_path(key: str, windows: bool) -> bool:
    """Is this the env key the child reads its PATH from? The merge is a plain
    dict update, so on POSIX a lane declaring `Path` adds a second variable and
    leaves `PATH` alone. Only on Windows, where the env block is one
    case-insensitive namespace, does `Path` carry the value the child reads."""
    return key == "PATH" or (windows and key.upper() == "PATH")


class LaunchSpec(NamedTuple):
    """Where a lane's command starts, and what it merges over the environment.

    Hashable on purpose: a probe that asks this child a question keys its memo
    on the spec, so two lanes share an answer only when their children start
    the same way.
    """
    cwd: Path
    env: tuple[tuple[str, str], ...] = ()

    def child_env(self, extra: dict[str, str] | None = None) -> dict[str, str] | None:
        """The environment the child sees: the process environment, the lane's
        pairs over it, then `extra`. None when nothing is added, so the child
        inherits the process environment unchanged."""
        added = {**dict(self.env), **(extra or {})}
        return {**os.environ, **added} if added else None

    def popen_kwargs(self, extra: dict[str, str] | None = None) -> dict:
        """The cwd and env to start the child with, as `procs.run_bounded` takes them."""
        return {"cwd": self.cwd, "env": self.child_env(extra)}

    def path(self, windows: bool = _WINDOWS) -> str | None:
        """The PATH the lane declares for its child, or None when it declares
        none and the process PATH is the whole answer. A lane that ships its
        own toolchain through `[lane.env] PATH` runs a runner this process
        cannot see on its own PATH."""
        return next((value for key, value in self.env if _names_path(key, windows)), None)

    def resolve(self, word: str) -> str | None:
        r"""Where the child's shell finds this word, or None when it finds nothing.

        A word carrying a separator is a path, and the shell reads it from the
        directory the lane runs in. which() reads it against whatever
        directory this process started in, so the `.venv\Scripts\python.exe`
        init writes resolved from the repo root and from nowhere else. A bare
        name stays PATH's question: the lane's PATH when it declares one.
        """
        if os.sep in word or "/" in word:
            candidate = self.cwd / word
            return str(candidate) if candidate.is_file() else None
        return shutil.which(word, path=self.path())


def launch_spec(root: Path, lane) -> LaunchSpec:
    """How `lane` starts under `root`: the directory lanes.py hands the shell,
    and the `[lane.env]` pairs it merges. A config Lane and the LaneSpec init
    writes both carry the two fields."""
    return LaunchSpec(root / lane.cwd if lane.cwd else root, tuple(lane.env))


def first_word(command: str) -> str:
    """The word the shell will try to start, read the way that shell reads the
    line: a quoted interpreter path stays one word, where a whitespace split
    would break it at its space. "" when the line holds no word at all."""
    words = shell_words(command)
    return words[0] if words else ""


def pytest_step(command: str) -> list[str]:
    """The one command on the line that runs pytest, or nothing when none does.
    A lane chains steps (`coverage run -m pytest --cov=pylib && coverage json`),
    and only the step holding pytest says anything about pytest-cov."""
    for segment in shell_segments(command):
        if any(token.endswith("pytest") for token in segment):
            return segment
    return []


# The names a python answers to, matched against the last segment of the word:
# `python`, `python3`, `py`, `python3.12`, `C:/Program Files/py/python.exe`.
# `-c "import pytest_cov"` and `-m pip` are a python's flags and nobody else's:
# `coverage -c` takes a config file and rejects the code.
_PYTHON_NAME = re.compile(r"py(thon3?(\.\d+)?)?(\.exe)?$", re.IGNORECASE)


def is_python(word: str) -> bool:
    """Does this word name a python interpreter? A bare name or a path, with or
    without a version suffix."""
    return _PYTHON_NAME.fullmatch(PurePath(word).name) is not None


def pytest_python(command: str) -> str | None:
    """The python heading the step that runs pytest, or None when no python does.

    The head of that step, not the command's first word: `cd web && python -m
    pytest --cov` runs pytest with `python`. And it has to be a python.
    `coverage run -m pytest` names no interpreter at all, and a bare `pytest
    --cov` starts with pytest. An environment manager heads its step for the
    same reason: `uv run` and its siblings create or sync the project
    environment before running anything, so nothing may be asked of it, and
    `uv -m pip install pytest-cov` is not a command.
    """
    step = pytest_step(command)
    return step[0] if step and is_python(step[0]) else None


def pytest_head(command: str) -> str:
    """The word in front of pytest: the manager or tool the lane runs pytest
    through when no python does. A lane that chains steps runs pytest after
    `&&`, so this is the step's head, not the command's first word; the first
    word only when no step names pytest at all."""
    step = pytest_step(command)
    return step[0] if step else first_word(command)
