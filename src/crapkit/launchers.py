"""Which `crapkit` launchers a PATH holds, and which installer owns each.

The shell, a git hook, the plugin's hooks and an MCP client each start the bare
name `crapkit` from the PATH they inherit, and the first launcher on that PATH is
the crapkit they run. Two installs on one PATH run two versions from two
places, and which one a program gets depends on the order its PATH lists them.

A runner that builds an environment for one command (uvx, pipx run) puts that
environment first on the PATH it hands its child. The plugin's hooks never
inherit it, so a caller asking what those hooks start leaves it out.
"""
from __future__ import annotations

import os
from pathlib import PurePath

NAME = "crapkit"

# How each installer upgrades the install it owns, keyed on the directory it
# keeps crapkit's environment in, as it appears in the launcher's resolved path
# or in the interpreter path a Windows launcher embeds.
_OWNERS = (("/uv/tools/crapkit/", "uv tool upgrade crapkit"),
           ("/pipx/venvs/crapkit/", "pipx upgrade crapkit"))
_READ_LIMIT = 1 << 20  # a Windows launcher embeds its interpreter path near its end
_PYTHONS = ("python.exe",) if os.name == "nt" else ("python3", "python")
_INSTALL = {"uvx": "uv tool install crapkit", "pipx run": "pipx install crapkit"}


def ephemeral_runner(prefix: str) -> str | None:
    """`uvx` or `pipx run` when `prefix` is an environment one of them built in
    its cache for a single command, else None. uv keeps those under its cache's
    archive-v0 bucket; pipx under a cache directory of its own."""
    parts = {part.lower() for part in PurePath(prefix).parts}
    if "archive-v0" in parts:
        return "uvx"
    return "pipx run" if "pipx" in parts and parts & {".cache", "cache"} else None


def install_line(runner: str) -> str:
    """The command that keeps crapkit on PATH for a user who ran it through
    `runner`."""
    return _INSTALL[runner]


def _names() -> tuple[str, ...]:
    if os.name != "nt":
        return (NAME,)
    return tuple(NAME + ext.lower()
                 for ext in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep) if ext)


def _launcher_in(directory: str) -> str | None:
    paths = (os.path.join(directory, name) for name in _names())
    return next((p for p in paths if os.path.isfile(p) and os.access(p, os.X_OK)), None)


def _key(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _outside(entry: str, skip: str | None) -> bool:
    if not skip:
        return True
    held, under = _key(entry), _key(skip)
    return held != under and not held.startswith(under.rstrip(os.sep) + os.sep)


def _entries(path: str, skip: str | None) -> list[str]:
    entries = (entry.strip('"') for entry in path.split(os.pathsep))
    return [entry for entry in entries if entry and _outside(entry, skip)]


def path_launchers(path: str, skip: str | None = None) -> list[str]:
    """Every crapkit launcher on `path`, in PATH order, one per file: a
    directory listed twice, or linked to another, counts once. Entries under
    `skip` are left out."""
    first: dict[str, str] = {}
    for launcher in filter(None, map(_launcher_in, _entries(path, skip))):
        first.setdefault(_key(launcher), launcher)
    return list(first.values())


def _head(path: str) -> bytes:
    try:
        with open(path, "rb") as launcher:
            return launcher.read(_READ_LIMIT)
    except OSError:
        return b""


def _shebang_python(head: bytes) -> str | None:
    """The interpreter a script's first line names, when it names a python."""
    line = head.split(b"\n", 1)[0]
    words = line[2:].decode("utf-8", "replace").split() if line.startswith(b"#!") else []
    word = words[0] if words else ""
    return word if os.path.basename(word).startswith("python") else None


def _nearby_pythons(resolved: str) -> list[str]:
    here = os.path.dirname(resolved)
    return [os.path.join(d, name) for d in (here, os.path.dirname(here)) for name in _PYTHONS]


def _interpreter(resolved: str, head: bytes) -> str | None:
    """The python behind a launcher: its shebang's, else one beside it (a venv's
    bin or Scripts), else one a level up (a Windows install's Scripts)."""
    candidates = [_shebang_python(head) or "", *_nearby_pythons(resolved)]
    return next((path for path in candidates if path and os.path.isfile(path)), None)


def upgrade_command(launcher: str, quote) -> str:
    """The command that upgrades the install owning this launcher: uv tool's,
    pipx's, or pip run by the interpreter the launcher starts. `quote` spells
    one word for the reader's shell."""
    resolved = os.path.realpath(launcher)
    head = _head(resolved)
    text = (resolved + "\n" + head.decode("latin-1")).replace("\\", "/").lower()
    owner = next((command for marker, command in _OWNERS if marker in text), None)
    if owner:
        return owner
    python = _interpreter(resolved, head)
    return f"{quote(python) if python else 'python'} -m pip install --upgrade crapkit"
