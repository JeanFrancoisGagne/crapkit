r"""How a path from outside git becomes the path git spells.

git names every tracked file root-relative, with `/` between directories and in
the letter case its index holds. Every other source spells the same file its own
way. A shell hands over `src\a.py`, or `/c/repo/src/a.py` from Git Bash; a
crapkit.toml holds what a person typed on whichever OS they use; a Claude Code
payload carries `SRC\A.PY` when the model typed it that way; a runner writes the
checkout root the way its own working directory spelled it. Each reader used to
fold one of those spellings and compare the rest as text, which on a
case-insensitive disk, or with a config shared between two OSes, named no file
or another file.

Four rules, and every reader goes through them:

- `native`: on Windows, the drive spelling of an MSYS (`/c/...`), WSL
  (`/mnt/c/...`), extended-length (`\\?\C:\...`) or local admin share
  (`\\localhost\C$\...`) path.
- `file_separators`: a path a file carries (crapkit.toml, a coverage report, a
  JUnit report) with `/` between directories. Such a file travels between OSes,
  so its backslash separates directories on every OS, except that on POSIX a
  tree holding a file by that literal name keeps it.
- `disk_spelling`: a root-relative path in the letter case its directories list,
  where the filesystem opened it in another case.
- `inside`: an absolute path relative to the root, decided by the file it names,
  so a symlink, a junction, a lower-case drive letter or a UNC alias of a local
  drive still lands in this checkout.

Stdlib only: the advisory hook imports this on every edit.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_WINDOWS = os.name == "nt"

# `/c/...` (MSYS, Git Bash) and `/mnt/c/...` (WSL): one letter names a drive.
_POSIX_DRIVE = re.compile(r"/(?:mnt/)?([A-Za-z])(?=/|$)")
# `\\?\C:\...` and `\\?\UNC\host\share\...`: the same path without the prefix.
_EXTENDED = re.compile(r"[\\/]{2}\?[\\/](UNC[\\/])?", re.IGNORECASE)
# `\\host\C$\...`: a drive's administrative share.
_ADMIN_SHARE = re.compile(r"[\\/]{2}([^\\/]+)[\\/]([A-Za-z])\$(?=[\\/]|$)")


def native(raw: str, windows: bool = _WINDOWS) -> str:
    r"""The path as this OS opens it. On Windows, `/c/x` and `/mnt/c/x` read as
    `C:/x` unless the current drive holds a literal `\c\x`, `\\?\C:\x` as
    `C:\x`, and `\\localhost\C$\x` as `C:\x`. cmd.exe cannot start a command in
    a UNC directory at all, so a checkout reached through this machine's own
    admin share has to come back to its drive. Every path on POSIX is as given."""
    if not windows:
        return raw
    return _local_share(_drive_spelling(_unextended(raw)))


def _unextended(raw: str) -> str:
    match = _EXTENDED.match(raw)
    if match is None:
        return raw
    return ("\\\\" if match.group(1) else "") + raw[match.end():]


def _drive_spelling(raw: str) -> str:
    match = _POSIX_DRIVE.match(raw)
    if match is None or os.path.lexists(raw):
        return raw
    return f"{match.group(1).upper()}:" + (raw[match.end():] or "/")


def _local_share(raw: str) -> str:
    match = _ADMIN_SHARE.match(raw)
    if match is None or match.group(1).lower() not in _this_host():
        return raw
    return f"{match.group(2).upper()}:" + (raw[match.end():] or "\\")


def _this_host() -> set[str]:
    """The names a UNC path gives this machine. socket loads only here, for a
    path that already looks like a share."""
    import socket

    return {"localhost", "127.0.0.1", "::1", socket.gethostname().lower()}


def file_separators(raw: str, root: str | os.PathLike | None = None,
                    windows: bool = _WINDOWS) -> str:
    r"""A path read out of a file, with `/` between directories.

    crapkit.toml, a coverage report and a JUnit report are written on one OS
    and read on another: a lane committed from Windows says `.crapkit\cov.json`,
    and a Linux CI job has to open `.crapkit/cov.json`. On POSIX a backslash is
    also a legal filename character, so where `root` holds a file by the
    literal name, the name is kept."""
    if "\\" not in raw or _literal(raw, root, windows):
        return raw
    return raw.replace("\\", "/")


def _literal(raw: str, root: str | os.PathLike | None, windows: bool) -> bool:
    return not windows and root is not None and os.path.lexists(os.path.join(root, raw))


def disk_spelling(root: str | os.PathLike, rel: str) -> str:
    """`rel`, a `/`-separated path under `root`, in the letter case each
    directory lists its entries.

    A case-insensitive filesystem (Windows, macOS) opens `SRC/a.py` for
    `src/a.py`, and git, the ratchet file and every scope prefix compare the
    text. Each component the filesystem opened in another case takes the one
    listed spelling. A component that names nothing, or names something only
    in the case given, ends the walk and the rest stays as written, so on a
    case-sensitive disk `Src` stays `Src`."""
    parts = rel.split("/")
    folder = Path(root)
    for i, part in enumerate(parts):
        listed = _listed(folder, part)
        if listed is None:
            break
        parts[i] = listed
        folder = folder / listed
    return "/".join(parts)


def _listed(folder: Path, part: str) -> str | None:
    """The entry of `folder` that `part` opens, or None when there is none to
    name: `part` itself when listed so, else the one entry that differs only in
    case, when the filesystem opens `part` too."""
    if part in ("", ".", ".."):
        return None
    names = _names(folder)
    return part if part in names else _other_case(folder, part, names)


def _other_case(folder: Path, part: str, names: set[str]) -> str | None:
    same = [name for name in names if name.casefold() == part.casefold()]
    if len(same) != 1 or not os.path.lexists(folder / part):
        return None
    return same[0]


def _names(folder: Path) -> set[str]:
    try:
        return set(os.listdir(folder))
    except OSError:
        return set()


def inside(path: str | os.PathLike, root: str | os.PathLike) -> str | None:
    """`path`, an absolute path, as the root-relative path git spells, or None
    when it names nothing under `root`.

    Text says too little here. `c:\\repo`, `C:\\REPO`, a junction or symlink to
    the checkout and `\\\\localhost\\C$\\repo` all name one directory, so each
    side is resolved, and when the two still differ, each directory above
    `path` is asked whether it is the root itself."""
    resolved, top = Path(path).resolve(), Path(root).resolve()
    rel = _relative(resolved, top)
    if rel is None:
        rel = _relative_by_identity(resolved, top)
    return None if rel is None else disk_spelling(top, rel)


def _relative(path: Path, top: Path) -> str | None:
    try:
        return path.relative_to(top).as_posix()
    except ValueError:
        return None


def _relative_by_identity(path: Path, top: Path) -> str | None:
    for parent in path.parents:
        if _same_file(parent, top):
            return path.relative_to(parent).as_posix()
    return None


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def folds_case(root: str | os.PathLike) -> bool:
    """Does the filesystem at `root` open a name in another letter case? Asked
    of the root's own name, the one entry sure to exist."""
    path = Path(root).resolve()
    other = path.with_name(path.name.swapcase())
    return other.name != path.name and _same_file(other, path)


def is_unc(path: str | os.PathLike) -> bool:
    r"""Is this a network path (`\\host\share\...`)? cmd.exe refuses to stand in
    one and starts the command in C:\Windows instead."""
    return str(path).replace("/", "\\").startswith("\\\\")
