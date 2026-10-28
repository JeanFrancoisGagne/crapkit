"""Where crapkit finds the programs it starts itself: git, taskkill, claude.

Handed a bare name, the OS searches for it, and that search reads the working
directory. Windows' CreateProcess looks in the current directory before PATH
unless the caller's environment sets NoDefaultCurrentDirectoryInExePath, which
ordinary setups do not, and shutil.which does the same: a repo holding a
`git.exe` at its root had crapkit run that file. On POSIX, execvp and
shutil.which read an empty or relative PATH entry (`PATH=:/usr/bin`) as that
directory. This module reads PATH's absolute entries and nothing else, so a
file planted in a repo is never run and never reported as found.

A lane's command is the user's own and runs the way their shell runs it:
lane_command.py finds its words, not this module.
"""
from __future__ import annotations

import errno
import os
from collections.abc import Mapping
from pathlib import PurePath

_WINDOWS = os.name == "nt"
# cmd.exe's own default, for an environment that sets no PATHEXT.
_PATHEXT = ".COM;.EXE;.BAT;.CMD"


def find(name: str, env: Mapping[str, str] | None = None) -> str | None:
    """The absolute path of the first file named `name` in an absolute PATH
    entry of `env` (os.environ by default), or None when no such entry holds
    one. On Windows a `name` that ends in none of PATHEXT's extensions is tried
    with each of them, in PATHEXT's order; one that ends in such an extension
    is looked up as written."""
    environment = os.environ if env is None else env
    names = _pathext_names(name, environment.get("PATHEXT") or _PATHEXT) if _WINDOWS else [name]
    return _first(names, search_path(environment))


def require(name: str) -> str:
    """The file a start of the bare `name` runs, found the way `find` finds one.

    CreateProcess adds `.exe` to a name with no extension and tries no other,
    so on Windows that is the one name looked for. A batch file would be wrong
    here anyway: Windows runs one through cmd.exe, which searches for argv[0]
    again, the current directory first, whatever file subprocess was told to
    start.

    When no absolute entry holds it, this raises the FileNotFoundError a start
    of the bare name raised, so each caller's answer to a missing program, and
    the message its user reads, stay as they were."""
    found = _first([_started_name(name)], search_path(os.environ))
    if found is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), name)
    return found


def search_path(env: Mapping[str, str] | None = None) -> list[str]:
    """PATH's absolute entries, in order. An empty entry, `.` and every other
    relative one name the working directory, so they are left out. An unset
    PATH reads as os.defpath, as subprocess reads it."""
    environment = os.environ if env is None else env
    entries = (entry.strip('"') for entry in environment.get("PATH", os.defpath).split(os.pathsep))
    return [entry for entry in entries if PurePath(entry).is_absolute()]


def _started_name(name: str) -> str:
    return name + ".exe" if _WINDOWS and not PurePath(name).suffix else name


def _pathext_names(name: str, pathext: str) -> list[str]:
    extensions = [extension for extension in pathext.split(";") if extension]
    if PurePath(name).suffix.upper() in {extension.upper() for extension in extensions}:
        return [name]
    return [name + extension.lower() for extension in extensions]


def _first(names: list[str], directories: list[str]) -> str | None:
    paths = (os.path.join(directory, name) for directory in directories for name in names)
    return next((path for path in paths if os.path.isfile(path) and os.access(path, os.X_OK)), None)
