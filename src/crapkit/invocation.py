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

import sys
from pathlib import Path

_CONSOLE_SCRIPT = "crapkit"


def _self() -> str:
    """The spelling of crapkit that resolves in the environment this process is
    running in."""
    argv0 = sys.argv[0] if sys.argv else ""
    if Path(argv0).stem == _CONSOLE_SCRIPT:
        return _CONSOLE_SCRIPT
    return f"{_quoted(sys.executable)} -m {_CONSOLE_SCRIPT}"


def _quoted(interpreter: str) -> str:
    r"""`C:\Program Files\Python311\python.exe` is an ordinary Windows install,
    and unquoted it reaches cmd.exe as `C:\Program` plus two arguments. Double
    quotes are the one form cmd, PowerShell, bash and zsh all read."""
    return f'"{interpreter}"' if " " in interpreter else interpreter
