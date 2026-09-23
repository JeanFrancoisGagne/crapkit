"""How a configured lane's command reads: the step that runs pytest, and the
python that heads it.

lanes.py names that python in the hint after a lane fails for want of
pytest-cov, and doctor asks that python whether pytest-cov imports. Both read
the command here, so the hint and the probe cannot name different words.

The command line itself is read by `config.shell_words` and
`config.shell_segments`, the way the shell that runs it reads it.
"""
from __future__ import annotations

import re
from pathlib import PurePath

from .config import shell_segments, shell_words


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
