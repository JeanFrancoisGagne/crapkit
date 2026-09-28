"""How to spell crapkit in a message crapkit prints.

Every next-step and every refusal names the command the reader runs next, and
they all used to spell it `crapkit`. That is the console script, and two
documented ways of running crapkit put no such name on PATH: `python -m crapkit`
from a source checkout (README), and `exec python -m crapkit hook-precommit`,
the line README's git hook falls back to when the hook's PATH holds neither
`crapkit` nor `uvx`. In both, `init` finished by telling the reader to run `crapkit
coverage` and the shell answered 127.

So the message names `crapkit` only when PATH resolves it to a console script
of the interpreter running this process, which is the crapkit that wrote the
message. Otherwise it names that interpreter: `sys.executable`, never bare
`python`, since on Windows a bare `python` reaches the WindowsApps stub, a venv
that has no crapkit, or the base interpreter a venv wraps.

pipx and uv tool put the console script in a bin dir of their own. On POSIX
that is a symlink into the tool's environment, which counts where it points.
On Windows both copy the launcher there byte for byte. A launcher holds the
path of the interpreter it starts, so a copy whose bytes equal the launcher in
this interpreter's scripts dir starts this interpreter, and counts too.

The interpreter is spelled with forward slashes. An agent's Bash tool on
Windows is Git Bash, which drops every backslash of `C:\\venv\\Scripts\\python.exe`
and answers 127; `C:/venv/Scripts/python.exe` runs in Git Bash, cmd.exe and
PowerShell alike. A path that holds a space is quoted, and PowerShell reads a
line that opens with a quoted string as an expression: there the reader types
`& ` first (README). The MCP server's children run as `python -m crapkit`, so
every next step an agent reads from a tool result goes through here.

uvx starts the console script too, the README's route for a repo that is not
Python, and the same 127 followed: the launcher sits in an environment in uv's
cache, whose bin uvx puts first on the PATH of the process it starts and of no
other. A runner's cache is a directory tagged CACHEDIR.TAG (uv and pipx both tag
theirs), and uv names itself in the UV variable of every process it starts. With
both, the reader runs `uvx crapkit`. A cache some other runner keeps, such as
`pipx run`, gets the interpreter form, which resolves while the cache holds it.
uv also tags every environment it creates, installed tools included, so only a
tag ABOVE the environment makes it a cache. The cache is asked first: inside the
process uvx starts, PATH's `crapkit` is the cached launcher, a console script of
this very interpreter, which no other shell has on its PATH.

Not everything crapkit prints goes through `_self`. The brief packet's
`commands.*` stay console-script strings (docs/agent-json.md, #37), because they
are read somewhere other than the process that produced them; they take
`console_script`, so a packet a uvx run built says `uvx crapkit`, which resolves
in any shell on a machine with uv. The crapkit.toml template comments `init`
writes into a consumer's repo keep `crapkit`: every clone reads them, and each
clone's own route decides its spelling.

A path the reader typed, on the command line or in crapkit.toml, is quoted back
by `quoted_path` as typed. repr doubled every backslash, so `crapkit .\\mini`
answered about `'.\\\\mini'`, a token nobody typed.
"""
from __future__ import annotations

import os
import shutil
import sys
import sysconfig
from pathlib import Path

_CONSOLE_SCRIPT = "crapkit"
_CACHE_TAG = "CACHEDIR.TAG"


def _self() -> str:
    """The spelling of crapkit that resolves in the environment this process is
    running in, read from any shell."""
    if runs_from_cache():
        return console_script() if os.environ.get("UV") else _module_form()
    if _runs_here(shutil.which(_CONSOLE_SCRIPT)):
        return _CONSOLE_SCRIPT
    return _module_form()


def console_script() -> str:
    """The console script as a reader outside this process runs it: `uvx
    crapkit` when uvx started this process, since uvx puts no `crapkit` on
    PATH, and `crapkit` otherwise."""
    uvx = runs_from_cache() and bool(os.environ.get("UV"))
    return f"uvx {_CONSOLE_SCRIPT}" if uvx else _CONSOLE_SCRIPT


def _module_form() -> str:
    return f"{_quoted(_forward(sys.executable))} -m {_CONSOLE_SCRIPT}"


def _runs_here(found: str | None) -> bool:
    """PATH's `crapkit` is a console script this interpreter installed. A
    symlink (pipx and uv tool on POSIX) counts where it points, and a copy
    (the same two on Windows) counts when its bytes are the launcher's."""
    if found is None:
        return False
    launcher = Path(found).resolve()
    return launcher.parent in _scripts_dirs() or _copied_here(launcher)


def _copied_here(launcher: Path) -> bool:
    return any(_same_bytes(launcher, scripts / launcher.name) for scripts in _scripts_dirs())


def _same_bytes(one: Path, other: Path) -> bool:
    """Whether two files hold the same bytes. A file that cannot be read, or
    is not there, proves nothing."""
    try:
        return one.stat().st_size == other.stat().st_size and one.read_bytes() == other.read_bytes()
    except OSError:
        return False


def _scripts_dirs() -> set[Path]:
    """Where installs into this interpreter put console scripts: its own
    environment, and the user scheme `pip install --user` writes to."""
    schemes = {sysconfig.get_default_scheme(), sysconfig.get_preferred_scheme("user")}
    return {Path(sysconfig.get_path("scripts", scheme)).resolve() for scheme in schemes}


def _forward(path: str, sep: str = os.sep) -> str:
    return path.replace(sep, "/")


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
    r"""`C:/Program Files/Python311/python.exe` is an ordinary Windows install,
    and unquoted it reaches cmd.exe as `C:/Program` plus two arguments. Double
    quotes are the one form cmd, bash and zsh all read. PowerShell runs the
    quoted line only with `& ` in front, and no spelling runs unchanged in
    all four."""
    return f'"{interpreter}"' if " " in interpreter else interpreter


def quoted_path(value: str | os.PathLike) -> str:
    r"""A path the reader typed, in single quotes and spelled as typed: `.\mini`
    reads `'.\mini'`, where repr printed `'.\\mini'`. A character that would
    break the line (a control character, a lone surrogate) is still escaped the
    way repr escapes it."""
    return "'" + "".join(map(_shown, os.fspath(value))) + "'"


def _shown(char: str) -> str:
    return char if char.isprintable() else repr(char)[1:-1]
