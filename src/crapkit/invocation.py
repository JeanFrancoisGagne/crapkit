"""How to spell crapkit in a message crapkit prints. Pure.

Every next-step and every refusal names the command the reader runs next, and
they all used to spell it `crapkit`. That is the console script, and two
documented ways of running crapkit put no such name on PATH: `python -m crapkit`
from a source checkout (README), and `exec <venv>/Scripts/python -m crapkit
hook-precommit` from a git hook, which is spelled that way precisely because git
runs hooks outside the activated venv. In both, `init` finished by telling the
reader to run `crapkit coverage` and the shell answered 127.

The process already knows. `sys.argv[0]` is the console script when that is what
started it, and the package's own `__main__.py` when `python -m` did, so the
message can name the form that resolves where it is being read.

`sys.executable`, never bare `python`: on Windows a bare `python` reaches the
WindowsApps stub, a venv that has no crapkit, or the base interpreter a venv
wraps. The interpreter running this process is the one crapkit is installed in.

Not everything crapkit prints goes through here. The brief packet's `commands.*`
stay console-script strings (docs/agent-json.md, #37), and so do the crapkit.toml
template comments `init` writes into a consumer's repo: both are read somewhere
other than the process that produced them.
"""
from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

_CONSOLE_SCRIPT = "crapkit"

# A path segment cmd.exe, PowerShell and Git Bash all read as part of one bare
# word. cmd.exe ends a word at a space, `&`, `;`, `,` or `=`, and PowerShell at
# `(` or `;`; a segment holding anything outside this set goes in double quotes.
_BARE_SEGMENT = re.compile(r"[\w.:~+-]*")


def _self() -> str:
    """The spelling of crapkit that resolves in the environment this process is
    running in."""
    argv0 = sys.argv[0] if sys.argv else ""
    if Path(argv0).stem == _CONSOLE_SCRIPT:
        return _CONSOLE_SCRIPT
    return f"{_interpreter()} -m {_CONSOLE_SCRIPT}"


def _interpreter() -> str:
    """sys.executable as the first word of a line a reader pastes."""
    if os.name != "nt":
        return shell_path(sys.executable)
    return shell_path(_spaceless(sys.executable))


def shell_path(path: str) -> str:
    r"""`path` as one word that the shells a reader pastes into read back as `path`.

    POSIX quoting for sh. On Windows one line has to serve cmd.exe, PowerShell
    and Git Bash, and the obvious spellings each lose one of them. Git Bash
    reads a bare backslash as an escape, so `C:\wt\x` runs as `C:wtx`, exit
    127. PowerShell reads a double quote at the start of a line as a string, so
    `"C:\Program Files\...\python.exe" -m crapkit` stops at `-m`. Forward
    slashes open the file in all three, and a segment that needs quoting is
    quoted on its own, `C:/"Program Files"/...`, which keeps the first
    character bare.

    One case loses cmd.exe: a venv's python.exe whose path holds a space
    (`_spaceless`). docs/adr/0003 records that trade.

    Inside double quotes some shell still reads `%`, `!`, `$` and a backtick,
    so a directory name holding one of them is not safe here.
    """
    if os.name != "nt":
        return shlex.quote(path)
    return "/".join(map(_windows_segment, path.replace("\\", "/").split("/")))


def _windows_segment(segment: str) -> str:
    return segment if _BARE_SEGMENT.fullmatch(segment) else f'"{segment}"'


def _spaceless(path: str) -> str:
    """The same file spelled without a space, when Windows has such a spelling.

    A venv's python.exe is a launcher that ends its own name at the first space
    of the line cmd.exe hands it, unless that line opens with a double quote,
    and PowerShell reads a line that opens with one as a string. So no single
    line runs a venv interpreter whose path holds a space in both shells. The
    directories a link points at, or the 8.3 short name the volume keeps, can
    name the same file with no space at all. `path` itself when neither does,
    and then `shell_path` keeps PowerShell, pwsh and Git Bash and gives up
    cmd.exe, which ran the whole-path-quoted spelling crapkit printed up to
    0.8.0 (docs/adr/0003).
    """
    if " " not in path:
        return path
    return next((spelling for spelling in (_unlinked(path), _short_name(path))
                 if " " not in spelling), path)


def _unlinked(path: str) -> str:
    """`path` with the links its directories cross resolved. The file itself is
    left alone: a venv's python may be a link to the base interpreter, which has
    no crapkit. A mapped drive that resolves to a network share keeps `path`."""
    given = Path(path)
    resolved = given.parent.resolve() / given.name
    return str(resolved) if len(resolved.drive) == 2 else path


def _short_name(path: str) -> str:
    """The 8.3 name Windows keeps for `path`, or `path` where the volume keeps none."""
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(path, buffer, len(buffer))
    return buffer.value if 0 < length < len(buffer) else path
