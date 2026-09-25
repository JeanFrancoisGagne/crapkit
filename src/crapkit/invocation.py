"""How to spell crapkit in a message crapkit prints.

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

uvx starts the console script too, the README's route for a repo that is not
Python, and the same 127 followed: the launcher sits in an environment in uv's
cache, whose bin uvx puts first on the PATH of the process it starts and of no
other. A runner's cache is a directory tagged CACHEDIR.TAG (uv and pipx both tag
theirs), and uv names itself in the UV variable of every process it starts. With
both, the reader runs `uvx crapkit`. A cache some other runner keeps, such as
`pipx run`, gets the interpreter form, which resolves while the cache holds it.
uv also tags every environment it creates, installed tools included, so only a
tag ABOVE the environment makes it a cache.

Not everything crapkit prints goes through `_self`. The brief packet's
`commands.*` stay console-script strings (docs/agent-json.md, #37), because they
are read somewhere other than the process that produced them; they take
`console_script`, so a packet a uvx run built says `uvx crapkit`, which resolves
in any shell on a machine with uv. The crapkit.toml template comments `init`
writes into a consumer's repo keep `crapkit`: every clone reads them, and each
clone's own route decides its spelling.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_CONSOLE_SCRIPT = "crapkit"
_CACHE_TAG = "CACHEDIR.TAG"


def _self() -> str:
    """The spelling of crapkit that resolves in the environment this process is
    running in."""
    argv0 = sys.argv[0] if sys.argv else ""
    if Path(argv0).stem != _CONSOLE_SCRIPT:
        return _module_form()
    if runs_from_cache() and not os.environ.get("UV"):
        return _module_form()
    return console_script()


def console_script() -> str:
    """The console script as a reader outside this process runs it: `uvx
    crapkit` when uvx started this process, since uvx puts no `crapkit` on
    PATH, and `crapkit` otherwise."""
    uvx = runs_from_cache() and bool(os.environ.get("UV"))
    return f"uvx {_CONSOLE_SCRIPT}" if uvx else _CONSOLE_SCRIPT


def _module_form() -> str:
    return f"{_quoted(sys.executable)} -m {_CONSOLE_SCRIPT}"


def runs_from_cache() -> bool:
    """Whether this interpreter's environment lies inside a runner's cache: a
    directory above it carries CACHEDIR.TAG. The environment's own tag does not
    count, because uv writes one into every environment it creates."""
    return any((parent / _CACHE_TAG).is_file() for parent in Path(sys.prefix).parents)


def path_without_own_cache() -> str | None:
    """PATH as a process this one did not start reads it, for `shutil.which`.

    uvx puts its cached environment's bin first on this process's PATH alone,
    so a lookup here finds the launcher uvx started, which the reader's shell
    and the plugin's hooks never see. Those entries are dropped. None when this
    process runs from no cache: which() then reads the process PATH as it is."""
    if not runs_from_cache():
        return None
    own = Path(sys.prefix)
    entries = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(entry for entry in entries if not Path(entry).is_relative_to(own))


def _quoted(interpreter: str) -> str:
    r"""`C:\Program Files\Python311\python.exe` is an ordinary Windows install,
    and unquoted it reaches cmd.exe as `C:\Program` plus two arguments. Double
    quotes are the one form cmd, PowerShell, bash and zsh all read."""
    return f'"{interpreter}"' if " " in interpreter else interpreter
