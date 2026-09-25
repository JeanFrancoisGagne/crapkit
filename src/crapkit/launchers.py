"""Which `crapkit` launchers a PATH holds, and which installer owns each.

The shell, a git hook, the plugin's hooks and an MCP client each start the bare
name `crapkit` from the PATH they inherit, and the first launcher on that PATH is
the crapkit they run. Two installs on one PATH run two versions from two
places, and which one a program gets depends on the order its PATH lists them.

A runner that builds an environment for one command (uvx, pipx run) puts that
environment first on the PATH it hands its child. The plugin's hooks never
inherit it, so a caller asking what those hooks start leaves it out.

pipx 1.17 on its uv backend hands `pipx run crapkit ...` to `uv tool run`, so
that environment is uv's, in uv's cache like uvx's, and nothing in it says pipx
started it. Such an environment is named by the tool that built it.
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
_INSTALL = {"uv": "`uv tool install crapkit`, or `pipx install crapkit` if you ran doctor "
                  "through `pipx run`",
            "pipx": "`pipx install crapkit`"}


def ephemeral_runner(prefix: str) -> str | None:
    """"uv" or "pipx" when `prefix` is an environment that tool built in its
    cache for a single command, else None. uv keeps those under its cache's
    archive-v0 bucket (uvx, `uv tool run`, and `pipx run` on pipx's uv
    backend); pipx's pip backend under a cache directory of pipx's own."""
    parts = {part.lower() for part in PurePath(prefix).parts}
    if "archive-v0" in parts:
        return "uv"
    return "pipx" if "pipx" in parts and parts & {".cache", "cache"} else None


def install_line(builder: str) -> str:
    """The install that keeps crapkit on PATH for a user whose command ran in
    an environment `builder` made, each command in backticks. A uv-built one
    may have come through `pipx run`, so it names pipx's install too."""
    return _INSTALL[builder]


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


def _uv_made(python: str) -> bool:
    """Was this interpreter's venv made by uv? uv writes a `uv = VERSION` line
    into pyvenv.cfg and installs no pip, so `python -m pip` fails there."""
    cfg = os.path.join(os.path.dirname(os.path.dirname(python)), "pyvenv.cfg")
    try:
        with open(cfg, encoding="utf-8", errors="replace") as text:
            return any(line.split("=")[0].strip() == "uv" for line in text)
    except OSError:
        return False


def pip_install(python: str, requirement: str, spelled: str | None = None) -> str:
    """The command that installs `requirement` into `python`'s environment:
    uv's pip for a venv uv made, which holds no pip, else that python's pip.
    `spelled` is how the command names the interpreter (quoted, or the word a
    lane wrote), `python` itself when omitted."""
    word = spelled or python
    if _uv_made(python):
        return f"uv pip install --python {word} {requirement}"
    return f"{word} -m pip install {requirement}"


def _pip_line(python: str | None, quote) -> str:
    if python is None:
        return "python -m pip install --upgrade crapkit"
    return pip_install(python, "--upgrade crapkit", quote(python))


def upgrade_command(launcher: str, quote) -> str:
    """The command that upgrades the install owning this launcher: uv tool's,
    pipx's, or pip (uv's, in a venv uv made) for the interpreter the launcher
    starts. `quote` spells one word for the reader's shell."""
    resolved = os.path.realpath(launcher)
    head = _head(resolved)
    text = (resolved + "\n" + head.decode("latin-1")).replace("\\", "/").lower()
    owner = next((command for marker, command in _OWNERS if marker in text), None)
    return owner or _pip_line(_interpreter(resolved, head), quote)
