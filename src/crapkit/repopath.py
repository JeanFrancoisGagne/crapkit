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
  so its backslash separates directories on every OS, and a tracked name that
  holds a backslash is unsupported.
- `disk_spelling`: a root-relative path in the letter case its directories list,
  where the filesystem opened it in another case.
- `inside`: an absolute path relative to the root, decided by the file it names,
  so a symlink, a junction, a lower-case drive letter or a UNC alias of a local
  drive still lands in this checkout.

Stdlib only: the advisory hook imports this on every edit.
"""
from __future__ import annotations

import functools
import os
import posixpath
import re
from collections.abc import Callable
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


# --- the typed entry: a path a person or agent typed ---------------------------

def typed_path(raw: str, stand: str | os.PathLike = "") -> Path:
    r"""A place a person or agent typed (`--repo`, the working directory, a
    hook payload's `cwd` or `file_path`) as this OS opens it, through `native`.
    Git Bash and Claude Code on Windows spell a directory `/c/Users/...`, WSL
    `/mnt/c/...`, and a session can stand in `\\localhost\C$\...`; read as
    written, each named no directory or one cmd.exe cannot start a lane in. A
    relative path is read against `stand`, itself typed, when one is given."""
    path = Path(native(raw))
    if path.is_absolute() or not stand:
        return path
    return Path(native(os.fspath(stand))) / path


def typed(raw: str, root: str | os.PathLike,
          stand: str | os.PathLike | None = None) -> str | None:
    r"""A file a person or agent named (a CLI or MCP file argument), as git
    spells it, or None when it names nothing under `root`.

    `src/a.py`, `./src/a.py` and the absolute path tab completion returns name
    one file, and so do, on Windows, `src\a.py`, Git Bash's `/c/repo/src/a.py`,
    WSL's `/mnt/c/...` and the admin share `\\localhost\C$\...`. On POSIX a
    backslash is a literal filename character. An absolute path is placed by
    the one placing rule. `stand` is where the user stands: below the root, a
    relative argument is read from there (ADR 0002), so `grade.py` typed in
    web/src names web/src/grade.py; at the root, above it or with no `stand`,
    it is root-relative. On a case-insensitive disk the result takes the case
    its directories list: `SRC\a.py` is `src/a.py`, where the typed case matched
    no scope, no ratchet key and no stored row, and `rescore --gate` judged
    nothing and passed."""
    path = file_separators(native(raw)) if _WINDOWS else raw
    if _rooted(path):
        return inside(path, root)
    if stand is not None and _below(stand, root):
        return inside(os.path.join(stand, path), root)
    return disk_spelling(root, posixpath.normpath(path))


def _rooted(path: str) -> bool:
    """A rooted path with no drive (`/tmp/a.py` on Windows) counts too: it names
    the current drive's root, not a place under the repo."""
    named = Path(path)
    return named.is_absolute() or bool(named.root)


def _below(stand: str | os.PathLike, root: str | os.PathLike) -> bool:
    return Path(root).resolve() in Path(stand).resolve().parents


def on_a_share(path: str | os.PathLike, windows: bool = _WINDOWS) -> bool:
    r"""Is `path` a network path (`\\host\share\...`) on the one OS whose shell
    cannot start a command in one? cmd.exe refuses to stand in one and starts
    the command in C:\Windows instead; sh starts a command anywhere."""
    return windows and _unc(path)


def _unc(path: str | os.PathLike) -> bool:
    return str(path).replace("/", "\\").startswith("\\\\")


def file_separators(raw: str) -> str:
    r"""A path read out of a file, with `/` between directories.

    crapkit.toml, a coverage report and a JUnit report are written on one OS
    and read on another: a lane committed from Windows says `.crapkit\cov.json`,
    and a Linux CI job has to open `.crapkit/cov.json`. The text cannot tell a
    Windows separator from a POSIX filename character, so a backslash separates
    directories on every OS, and a tracked name that holds one is unsupported
    (doctor names it)."""
    return raw.replace("\\", "/")


class Reported:
    r"""The reported entry: a path a runner wrote into its report (a coverage
    key, a JUnit file attribute or classname), as git spells the file it names.

    A runner names a file the way it was started, so git's `web/app.test.ts`
    arrives as `./web/app.test.ts`, `WEB\app.test.ts` or
    `C:\repo\web\app.test.ts`. The report travels between OSes, so `\`
    separates directories; a leading `./` goes; a key that starts with the root
    as crapkit spells it loses the root as text, the common case and the cheap
    one; any other absolute key is placed by the one placing rule; and a
    root-relative key takes the letter case its directories list. A key that
    names nothing under the root comes back folded. A report names thousands of
    files in a few hundred folders, so each folder is listed and placed once."""

    def __init__(self, root: str | os.PathLike) -> None:
        self._root = Path(root)
        self._prefix = file_separators(str(root)).rstrip("/") + "/"
        self._placing = Placing(root)
        self._listing = functools.cache(entries)

    def __call__(self, raw: str) -> str:
        key = file_separators(raw)
        if key.startswith(self._prefix):
            return self.relative(key[len(self._prefix):])
        return self._placed(key) if os.path.isabs(key) else self.relative(key)

    def relative(self, key: str) -> str:
        """`key`, a root-relative key with `/` between directories, with no
        leading `./` and in the letter case its directories list. A reader that
        refuses absolute keys (coverage.py's) asks this step alone."""
        return disk_spelling(self._root, key.removeprefix("./"), self._listing)

    def _placed(self, key: str) -> str:
        rel = self._placing(key)
        return key if rel is None else self.relative(rel)


def disk_spelling(root: str | os.PathLike, rel: str,
                  listing: Callable[[Path], set[str]] | None = None) -> str:
    """`rel`, a `/`-separated path under `root`, in the letter case each
    directory lists its entries.

    A case-insensitive filesystem (Windows, macOS) opens `SRC/a.py` for
    `src/a.py`, and git, the ratchet file and every scope prefix compare the
    text. Each component the filesystem opened in another case takes the one
    listed spelling. A component that names nothing, or names something only
    in the case given, ends the walk and the rest stays as written, so on a
    case-sensitive disk `Src` stays `Src`. `listing` lists a folder; a reader
    spelling every key of a report passes a cached `entries`, so it lists each
    folder once."""
    parts = rel.split("/")
    folder = Path(root)
    for i, part in enumerate(parts):
        listed = _listed(folder, part, listing or entries)
        if listed is None:
            break
        parts[i] = listed
        folder = folder / listed
    return "/".join(parts)


def _listed(folder: Path, part: str, listing: Callable[[Path], set[str]]) -> str | None:
    """The entry of `folder` that `part` opens, or None when there is none to
    name: `part` itself when listed so, else the one entry that differs only in
    case, when the filesystem opens `part` too."""
    if part in ("", ".", ".."):
        return None
    names = listing(folder)
    return part if part in names else _other_case(folder, part, names)


def _other_case(folder: Path, part: str, names: set[str]) -> str | None:
    same = [name for name in names if name.casefold() == part.casefold()]
    if len(same) != 1 or not os.path.lexists(folder / part):
        return None
    return same[0]


def entries(folder: Path) -> set[str]:
    """The names `folder` lists, or none when it cannot be listed."""
    try:
        return set(os.listdir(folder))
    except OSError:
        return set()


def inside(path: str | os.PathLike, root: str | os.PathLike) -> str | None:
    """`path`, an absolute path, as the root-relative path git spells, or None
    when it names nothing under `root`. The one placing rule: the istanbul
    reader's rebase, lanes' wrong-tree check and every typed or declared
    absolute path ask it.

    Text says too little here. `c:\\repo`, `C:\\REPO`, a junction or symlink to
    the checkout and `\\\\localhost\\C$\\repo` all name one directory, so each
    side is resolved, and when the two still differ, each directory above
    `path` is asked whether it is the root itself. A path with no root or drive
    this OS reads (`C:/repo/a.ts` on POSIX) and a name this platform cannot
    express land nowhere: resolved against the working directory, the first
    could land in the checkout crapkit stands in."""
    try:
        resolved, top = _anchored(path).resolve(), Path(root).resolve()
    except (OSError, ValueError):
        return None
    rel = _relative(resolved, top)
    if rel is None:
        rel = _relative_by_identity(resolved, top)
    return None if rel is None else disk_spelling(top, rel)


def _anchored(path: str | os.PathLike) -> Path:
    named = Path(path)
    if not named.anchor:
        raise ValueError(f"{path} names no root")
    return named


class Placing:
    """`inside`, asked of every absolute path one report names. A report
    names thousands of files in a few hundred folders, so each folder is placed
    once, and a path comes back relative to the root with its own name as the
    report wrote it, or None when its folder is not in the checkout."""

    def __init__(self, root: str | os.PathLike) -> None:
        self._root = Path(root)
        self._folders: dict[str, str | None] = {}

    def __call__(self, path: str) -> str | None:
        folder, _, name = file_separators(path).rpartition("/")
        if folder not in self._folders:
            self._folders[folder] = inside(folder + "/", self._root)
        base = self._folders[folder]
        return None if base is None else posixpath.normpath(posixpath.join(base, name))


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
