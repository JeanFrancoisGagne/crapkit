"""Which `crapkit` launchers a PATH holds, and which installer owns each.

The shell, a git hook, the plugin's hooks and an MCP client each start the bare
name `crapkit` from the PATH they inherit, and the first launcher on that PATH is
the crapkit they run. Two installs on one PATH run two versions from two
places, and which one a program gets depends on the order its PATH lists them.

A runner that builds an environment for one command (uvx, `uv run --with`,
pipx run) puts that environment first on the PATH it hands its child. The
plugin's hooks never inherit it, so the launchers counted here leave it out.

uv keeps each such environment in a bucket of its cache (archive-v0 for uvx
and `uv tool run`, builds-v0 for the environment `uv run --with` runs in, whose
`--with` layer sits in archive-v0 behind it on PATH). uv tags the cache root
with CACHEDIR.TAG wherever UV_CACHE_DIR puts it, so the tag marks the cache,
not the name of one bucket. pipx 1.17 on its uv backend hands `pipx run
crapkit ...` to `uv tool run`, so that environment is uv's too, and nothing in
it says pipx started it. Such an environment is named by the tool that built it.
"""
from __future__ import annotations

import os
from pathlib import PurePath

NAME = "crapkit"

# How each installer upgrades and reinstalls the install it owns, keyed on the
# directory it keeps crapkit's environment in, as it appears in the launcher's
# resolved path or in the interpreter path a Windows launcher embeds. An upgrade
# leaves a launcher whose environment lost its python broken (`pipx upgrade`
# says to reinstall; uv and pip see crapkit current); the reinstall repairs it.
_OWNERS = (("/uv/tools/crapkit/", {"upgrade": "uv tool upgrade crapkit",
                                   "reinstall": "uv tool install --force crapkit"}),
           ("/pipx/venvs/crapkit/", {"upgrade": "pipx upgrade crapkit",
                                     "reinstall": "pipx reinstall crapkit"}))
_PIP_FLAG = {"upgrade": "--upgrade", "reinstall": "--force-reinstall"}
_READ_LIMIT = 1 << 20  # a Windows launcher embeds its interpreter path near its end
_PYTHONS = ("python.exe",) if os.name == "nt" else ("python3", "python")
_INSTALL = {"uv": "`uv tool install crapkit`, or `pipx install crapkit` if you ran doctor "
                  "through `pipx run`",
            "pipx": "`pipx install crapkit`"}


def _in_uv_cache(prefix: str) -> bool:
    """Is `prefix` an environment in a bucket of uv's cache, CACHE/BUCKET/ENV,
    the tag on CACHE saying it is a cache? uv's tool and python directories
    carry no tag."""
    cache = os.path.dirname(os.path.dirname(os.path.abspath(prefix)))
    return os.path.isfile(os.path.join(cache, "CACHEDIR.TAG"))


def ephemeral_runner(prefix: str) -> str | None:
    """"uv" or "pipx" when `prefix` is an environment that tool built in its
    cache for a single command, else None. uv keeps those in any bucket of its
    cache (uvx, `uv tool run`, `uv run --with`, and `pipx run` on pipx's uv
    backend); pipx's pip backend under a cache directory of pipx's own."""
    if _in_uv_cache(prefix):
        return "uv"
    parts = {part.lower() for part in PurePath(prefix).parts}
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


def _lasting(entry: str) -> bool:
    """False for the bin directory of an environment a runner built for one
    command, which only that command's PATH lists."""
    return ephemeral_runner(os.path.dirname(os.path.abspath(entry))) is None


def _entries(path: str) -> list[str]:
    entries = (entry.strip('"') for entry in path.split(os.pathsep))
    return [entry for entry in entries if entry and _lasting(entry)]


def path_launchers(path: str) -> list[str]:
    """Every crapkit launcher on `path` that outlives the command running now,
    in PATH order, one per file: a directory listed twice, or linked to
    another, counts once."""
    first: dict[str, str] = {}
    for launcher in filter(None, map(_launcher_in, _entries(path))):
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


def _pip_line(python: str | None, quote, action: str) -> str:
    requirement = f"{_PIP_FLAG[action]} crapkit"
    if python is None:
        return f"python -m pip install {requirement}"
    return pip_install(python, requirement, quote(python))


def _owner_command(launcher: str, quote, action: str) -> str:
    resolved = os.path.realpath(launcher)
    head = _head(resolved)
    text = (resolved + "\n" + head.decode("latin-1")).replace("\\", "/").lower()
    owner = next((commands[action] for marker, commands in _OWNERS if marker in text), None)
    return owner or _pip_line(_interpreter(resolved, head), quote, action)


def upgrade_command(launcher: str, quote) -> str:
    """The command that upgrades the install owning this launcher: uv tool's,
    pipx's, or pip (uv's, in a venv uv made) for the interpreter the launcher
    starts. `quote` spells one word for the reader's shell."""
    return _owner_command(launcher, quote, "upgrade")


def reinstall_command(launcher: str, quote) -> str:
    """The command that reinstalls the install owning this launcher, the
    repair for one that answers no version: `uv tool install --force`, `pipx
    reinstall`, or pip's `--force-reinstall` for the interpreter it starts."""
    return _owner_command(launcher, quote, "reinstall")
