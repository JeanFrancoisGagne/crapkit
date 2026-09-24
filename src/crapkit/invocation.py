"""How to spell crapkit in a message crapkit prints.

Every next-step and every refusal names the command the reader runs next, and
they all used to spell it `crapkit`. That is the console script, and two
documented ways of running crapkit put no such name on PATH: `python -m crapkit`
from a source checkout (README), and `exec <venv>/Scripts/python -m crapkit
hook-precommit` from a git hook, which is spelled that way precisely because git
runs hooks outside the activated venv. In both, `init` finished by telling the
reader to run `crapkit coverage` and the shell answered 127.

So the message names `crapkit` only when PATH resolves it to a console script
of the interpreter running this process, which is the crapkit that wrote the
message. Otherwise it names that interpreter: `sys.executable`, never bare
`python`, since on Windows a bare `python` reaches the WindowsApps stub, a venv
that has no crapkit, or the base interpreter a venv wraps.

The interpreter is spelled with forward slashes. An agent's Bash tool on
Windows is Git Bash, which drops every backslash of `C:\\venv\\Scripts\\python.exe`
and answers 127; `C:/venv/Scripts/python.exe` runs in Git Bash, cmd.exe and
PowerShell alike. The MCP server's children run as `python -m crapkit`, so
every next step an agent reads from a tool result goes through here.

Not everything crapkit prints goes through here. The brief packet's `commands.*`
stay console-script strings (docs/agent-json.md, #37), and so do the crapkit.toml
template comments `init` writes into a consumer's repo: both are read somewhere
other than the process that produced them.
"""
from __future__ import annotations

import os
import shutil
import sys
import sysconfig
from pathlib import Path

_CONSOLE_SCRIPT = "crapkit"


def _self() -> str:
    """The spelling of crapkit that resolves in the environment this process is
    running in, read from any shell."""
    if _runs_here(shutil.which(_CONSOLE_SCRIPT)):
        return _CONSOLE_SCRIPT
    return f"{_quoted(_forward(sys.executable))} -m {_CONSOLE_SCRIPT}"


def _runs_here(found: str | None) -> bool:
    """PATH's `crapkit` is a console script this interpreter installed. A
    symlink (pipx, uv tool) counts where it points."""
    return found is not None and Path(found).resolve().parent in _scripts_dirs()


def _scripts_dirs() -> set[Path]:
    """Where installs into this interpreter put console scripts: its own
    environment, and the user scheme `pip install --user` writes to."""
    schemes = {sysconfig.get_default_scheme(), sysconfig.get_preferred_scheme("user")}
    return {Path(sysconfig.get_path("scripts", scheme)).resolve() for scheme in schemes}


def _forward(path: str, sep: str = os.sep) -> str:
    return path.replace(sep, "/")


def _quoted(interpreter: str) -> str:
    r"""`C:/Program Files/Python311/python.exe` is an ordinary Windows install,
    and unquoted it reaches cmd.exe as `C:/Program` plus two arguments. Double
    quotes are the one form cmd, bash and zsh all read."""
    return f'"{interpreter}"' if " " in interpreter else interpreter
