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

One entry per source of a path, since each source brings its own extra input:

- typed (`typed`, `typed_path`): a path a person or agent typed, a CLI or MCP
  argument, `--repo`, the working directory, a hook payload's `file_path` or
  `cwd`. It takes the directory the user stands in.
- declared (`declared`): a path crapkit.toml holds, read as its key's kind.
- reported (`Reported`): a path a runner wrote into its report, with a
  per-folder cache for a report of thousands of keys. It records each key it
  leaves unplaced and why (`Unplaced`).
- fragment (`fragment`, `fragments`): a piece of a path to match.

They share four rules. `native` gives, on Windows, the drive spelling of an
MSYS (`/c/...`), WSL (`/mnt/c/...`), extended-length (`\\?\C:\...`) or local
admin share (`\\localhost\C$\...`) path. `file_separators` puts `/` between
directories of a path a file carries: such a file travels between OSes, so its
backslash separates directories on every OS, and a tracked name that holds one
is unsupported. `disk_spelling` gives a root-relative path the letter case its
directories list, and `tracked_spelling` the case git tracks it in when the
listing names it otherwise. And `place`, the one placing rule, answers whether an
absolute path is in this checkout by the file it names, so a symlink, a
junction, a lower-case drive letter or a UNC alias of a local drive still lands
in it, and says why when it does not: another tree, or a name this platform
cannot open. `inside` is its answer as the path or None. The reported entry
and the coverage.py reader ask it once a folder (`Placing`), and lanes'
wrong-tree check reads the reasons they recorded.

Stdlib only at import: the advisory hook imports this on every edit. Only a
name whose case the listing changes asks git, and imports gitio then.
"""
from __future__ import annotations

import enum
import functools
import os
import posixpath
import re
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

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
    if rooted(path):
        return inside(path, root)
    if stand is not None and _below(stand, root):
        return inside(os.path.join(stand, path), root)
    return tracked_spelling(root, posixpath.normpath(path))


def rooted(path: str | os.PathLike) -> bool:
    """Does a typed path name its place from a root, not from the directory it
    is read against? A rooted path with no drive (`/tmp/a.py` on Windows)
    counts too: it names the current drive's root, not a place under the repo.
    `typed` asks it of a file argument, and the writer flags of an output path."""
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


class Unplaced(enum.Enum):
    """Why the one placing rule left an absolute path unplaced."""

    ANOTHER_TREE = "another-tree"
    """The path resolves, and names a place outside this checkout."""
    UNOPENABLE = "unopenable"
    """This platform cannot open the name at all: it has no root or drive this
    OS reads (`C:/repo/a.ts` on POSIX), or holds a NUL, which no OS's names can
    hold, or a code point POSIX's filesystem encoding has no bytes for."""
    KEPT_ABSOLUTE = "kept-absolute"
    """The path lands in this checkout, and its format keeps an absolute key
    as written: coverage.py's reader never rebases one (relative_files is the
    runner's own switch)."""


# `C:/...`: a drive's root, which POSIX reads as a relative name.
_DRIVE_ROOT = re.compile(r"[A-Za-z]:/")


class Reported:
    r"""The reported entry: a path a runner wrote into its report (a coverage
    key, a JUnit file attribute or classname), as git spells the file it names.

    A runner names a file the way it was started, so git's `web/app.test.ts`
    arrives as `./web/app.test.ts`, `WEB\app.test.ts` or
    `C:\repo\web\app.test.ts`. The report travels between OSes, so `\`
    separates directories; a leading `./` goes; a key that starts with the root
    as crapkit spells it loses the root as text, the common case and the cheap
    one; any other absolute key is placed by the one placing rule; and a
    root-relative key takes the letter case git tracks the file in. A key that
    names nothing under the root comes back folded. A report names thousands of
    files in a few hundred folders, so each folder is listed and placed once.

    `unplaced` holds each absolute key the placing rule left unplaced, spelled
    as the call returned it, with its `Unplaced` reason. A drive-rooted key
    (`C:/repo/a.ts`) counts as absolute on every OS: on POSIX it is UNOPENABLE.
    So does a key from `/` (`absolute`)."""

    def __init__(self, root: str | os.PathLike) -> None:
        self._root = Path(root)
        self._prefix = file_separators(str(root)).rstrip("/") + "/"
        self._placing = Placing(root)
        self._listing = functools.cache(entries)
        self._tracked: dict[str, str] | None = None
        self.unplaced: dict[str, Unplaced] = {}

    def __call__(self, raw: str) -> str:
        key = file_separators(raw)
        if key.startswith(self._prefix):
            return self.relative(key[len(self._prefix):])
        return self._placed(key) if absolute(key) else self.relative(key)

    def relative(self, key: str) -> str:
        """`key`, a root-relative key with `/` between directories, with no
        leading `./` and in the letter case git tracks the file in. A reader
        that refuses absolute keys (coverage.py's) asks this step alone.

        The directories' listing answers when it spells the key as written,
        the common case, which asks git nothing. When it lists another case,
        git's index decides: after a case-only rename made without `git mv`
        the disk lists `App.ts` and git still tracks `app.ts`. A name git does
        not track takes the listed case."""
        return tracked_spelling(self._root, key.removeprefix("./"), self._listing,
                                self._tracked_by_fold)

    def _tracked_by_fold(self) -> dict[str, str]:
        """The tracked names by case fold, read once per report, the first
        time a key's case differs from the listing's."""
        if self._tracked is None:
            self._tracked = tracked_by_fold(self._root)
        return self._tracked

    def _placed(self, key: str) -> str:
        rel = self._placing.placed(key)
        if isinstance(rel, Unplaced):
            self.unplaced[key] = rel
            return key
        return self.relative(rel)


def absolute(key: str) -> bool:
    """Is `key`, a reported path with `/` between directories, a path from a
    root or a drive? The shape alone, on every OS and every Python: 3.13 on
    Windows stopped calling `/x` absolute, and a key that read as relative
    there reached a root scope (`.`) from another tree."""
    return key.startswith("/") or bool(_DRIVE_ROOT.match(key))


def tracked_spelling(root: str | os.PathLike, rel: str,
                     listing: Callable[[Path], set[str]] | None = None,
                     tracked: Callable[[], dict[str, str]] | None = None) -> str:
    """`rel`, a `/`-separated path under `root`, in the letter case git tracks
    the file in.

    The directories' listing answers when it spells `rel` as written, the
    common case, which asks git nothing. When it lists another case, git's
    index decides: after a case-only rename made without `git mv` the disk
    lists `App.ts` while git still tracks `app.ts`, and the listed name joined
    no tracked file, no stored row and no measured key. A name git does not
    track takes the listed case. `tracked` hands over a reader's own copy of
    `tracked_by_fold`, so a report asks git once."""
    listed = disk_spelling(root, rel, listing)
    if listed == rel:
        return listed
    folded = tracked() if tracked is not None else tracked_by_fold(root)
    return folded.get(rel.casefold(), listed)


def tracked_by_fold(root: str | os.PathLike) -> dict[str, str]:
    """Each file git tracks under `root`, by its case-folded name. A fold two
    tracked names share is left out, and so is everything when git cannot list
    them: the listing's case then stands."""
    from .errors import GitError
    from .gitio import ls_files

    try:
        names = ls_files(Path(root))
    except GitError:
        return {}
    folded: dict[str, list[str]] = {}
    for name in names:
        folded.setdefault(name.casefold(), []).append(name)
    return {fold: same[0] for fold, same in folded.items() if len(same) == 1}


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


def place(path: str | os.PathLike, root: str | os.PathLike) -> str | Unplaced:
    """`path`, an absolute path, as the root-relative path git spells, or why
    it names nothing under `root`. The one placing rule: the istanbul reader's
    rebase, lanes' wrong-tree check and every typed or declared absolute path
    ask it.

    Text says too little here. `c:\\repo`, `C:\\REPO`, a junction or symlink to
    the checkout and `\\\\localhost\\C$\\repo` all name one directory, so each
    side is resolved, and when the two still differ, each directory above
    `path` is asked whether it is the root itself. A path that resolves
    elsewhere is ANOTHER_TREE. A path with no root or drive this OS reads
    (`C:/repo/a.ts` on POSIX) and a name this platform cannot express are
    UNOPENABLE: resolved against the working directory, the first could land
    in the checkout crapkit stands in, and Python 3.11 on Windows resolves a
    name cut short at its NUL."""
    try:
        resolved, top = _anchored(path).resolve(), Path(root).resolve()
    except (OSError, ValueError):
        return Unplaced.UNOPENABLE
    rel = _relative(resolved, top)
    if rel is None:
        rel = _relative_by_identity(resolved, top)
    return Unplaced.ANOTHER_TREE if rel is None else disk_spelling(top, rel)


def inside(path: str | os.PathLike, root: str | os.PathLike) -> str | None:
    """`place` for a caller that needs no reason (the typed and declared
    entries): the root-relative path, or None."""
    return _path_or_none(place(path, root))


def _path_or_none(placed: str | Unplaced) -> str | None:
    return None if isinstance(placed, Unplaced) else placed


def _anchored(path: str | os.PathLike) -> Path:
    named = Path(path)
    if not named.anchor:
        raise ValueError(f"{path} names no root")
    if not _nameable(str(named)[len(named.anchor):]):
        raise ValueError(f"{path} holds a character no name on this OS can")
    return named


def _nameable(tail: str) -> bool:
    r"""Can this OS hold every name in `tail`, a path below its anchor? Every
    OS refuses NUL, and POSIX a code point its filesystem encoding has no bytes
    for. Nothing else is refused: a typed `src\*.ts` or `src\app.ts:10` holds a
    character Windows keeps out of a name, and still names a place under the
    root, as it did before the placing rule gave reasons."""
    try:
        return b"\0" not in os.fsencode(tail)
    except UnicodeError:
        return False


class Placing:
    """The one placing rule, asked of every absolute path one report names. A
    report names thousands of files in a few hundred folders, so each folder is
    placed once, and a path comes back relative to the root with its own name
    as the report wrote it. `placed` asks `place` and answers the reason a
    folder is not in the checkout; the call asks `inside` and answers None."""

    def __init__(self, root: str | os.PathLike) -> None:
        self._root = Path(root)
        self._paths: dict[str, str | None] = {}
        self._reasons: dict[str, str | Unplaced] = {}

    def __call__(self, path: str) -> str | None:
        return self._each_folder(self._paths, inside, path)

    def placed(self, path: str) -> str | Unplaced:
        return self._each_folder(self._reasons, place, path)

    def _each_folder(self, folders: dict, ask: Callable, path: str) -> str | Unplaced | None:
        folder, _, name = file_separators(path).rpartition("/")
        if folder not in folders:
            folders[folder] = ask(folder + "/", self._root)
        base = folders[folder]
        if base is None or isinstance(base, Unplaced):
            return base
        return posixpath.normpath(posixpath.join(base, name))


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


# --- the declared entry: a path crapkit.toml holds ----------------------------

class Refused(ValueError):
    """A declared path that can never become the path git spells. The text
    follows the value it refuses and says why, in crapkit.toml's terms."""


def declared(raw: str, kind: str, root: str | os.PathLike | None = None) -> str:
    r"""A path crapkit.toml holds, as git spells it, read the way its key's
    `kind` says. The file is committed and read on every OS, so `\` separates
    directories on every OS and a leading `./` names nothing.

    - "file" (a lane's cwd, artifact or results_artifact, the ratchet_file, a
      launcher's venv): a path the OS opens under the root.
    - "scope" (scope `paths`): a directory git lists, with no trailing or
      leading `/`. One that climbs above the root, names a drive or is empty is
      refused; so is one written from `/` that names nothing under the root,
      with its relative spelling when it lands in this checkout.
    - "prefix" (`path_prefix`): spelled as a scope path, and `.` is none.
    - "input" (lane `inputs`): a literal path under the root; one that climbs
      out, names a drive or holds a glob is refused.
    - "glob" (`[exclude] globs`): spelled as a scope path, and a trailing `/`
      names the directory's contents, as in .gitignore.

    Given the root, a scope, prefix or input path takes the letter case its
    directories list: `paths = ["Src"]` claimed nothing where git names `src`.
    Raises Refused."""
    return _DECLARED[kind](raw, root)


def _undotted(path: str) -> str:
    while path.startswith("./"):
        path = path[2:]
    return path


def _unrooted(raw: str) -> str:
    return _undotted(file_separators(raw).rstrip("/")).lstrip("/")


def _on_disk(root: str | os.PathLike | None, path: str) -> str:
    return disk_spelling(root, path) if root is not None and path else path


def _declared_file(raw: str, root: str | os.PathLike | None) -> str:
    return _undotted(file_separators(raw))


_NEVER_MATCHES = ("can never match a tracked file - scope paths are repo-relative, with no "
                  "drive and no `..` (docs/configuration.md)")


def _declared_scope(raw: str, root: str | os.PathLike | None) -> str:
    path = _unrooted(raw)
    if path == "" or ".." in path.split("/") or ":" in path:
        raise Refused(_NEVER_MATCHES)
    _refuse_absolute(raw, path, root)
    return _on_disk(root, path)


def _refuse_absolute(raw: str, path: str, root: str | os.PathLike | None) -> None:
    """`/web` is the root's web/; `/home/dev/repo/web` or `/c/repo/web` is a
    directory spelled absolutely, and folded it named nothing under the root,
    so the scope scored zero files. When the root-relative reading names
    nothing either, the path is refused."""
    rooted = file_separators(raw).startswith("/")
    if root is None or not rooted or os.path.lexists(os.path.join(root, path)):
        return
    raise Refused(f"names nothing under the root as {path!r}; scope paths are "
                  f"repo-relative{_relative_hint(raw, root)} (docs/configuration.md)")


def _relative_hint(raw: str, root: str | os.PathLike) -> str:
    """`: write 'web'` when the absolute spelling lands in this checkout. A
    network share is never asked: resolving one can wait on the network."""
    absolute = native(raw)
    rel = None if _unc(absolute) else inside(absolute, root)
    return f": write {rel!r}" if rel else ""


def _declared_prefix(raw: str, root: str | os.PathLike | None) -> str:
    prefix = _unrooted(raw)
    return "" if prefix == "." else _on_disk(root, prefix)


_DRIVE_PATH = re.compile(r"[A-Za-z]:")


def _declared_input(raw: str, root: str | os.PathLike | None) -> str:
    """git reads inputs as literal pathspecs from the root, and its diff never
    reports a change above the root: a `../shared` input would be trusted
    forever, and `src/*.ts` would match no file at all."""
    if _outside_root(file_separators(raw)):
        raise Refused("is not a path inside the root; list paths relative to crapkit.toml, "
                      "without '..'")
    if "*" in raw or "?" in raw:
        raise Refused("is a glob; inputs are literal paths from the root, so list the "
                      "directory or file itself")
    return _on_disk(root, _unrooted(raw) or ".")


def _outside_root(path: str) -> bool:
    return (not path or path.startswith("/") or bool(_DRIVE_PATH.match(path))
            or ".." in path.split("/"))


def _declared_glob(raw: str, root: str | os.PathLike | None) -> str:
    glob = _unrooted(raw)
    return f"{glob}/**" if glob and file_separators(raw).endswith("/") else glob


_DECLARED: dict[str, Callable[[str, str | os.PathLike | None], str]] = {
    "file": _declared_file, "scope": _declared_scope, "prefix": _declared_prefix,
    "input": _declared_input, "glob": _declared_glob,
}


# --- the fragment entry: a piece of a path to match ---------------------------

class Fragment(NamedTuple):
    r"""A piece of a path a person typed to match against the paths git spells
    (`next-item --exclude`). `typed` stays as given, for a caller that matches
    a function name too; `path` is the path side: on Windows `pkg\legacy` is
    `pkg/legacy`, a leading `./` names nothing a path holds, and where the disk
    folds case the match folds too, so `PKG/Legacy` is `pkg/legacy`. Each of
    those was compared as text with git's spelling, and the directory the
    caller excluded came back as the next item."""
    typed: str
    path: str
    folds: bool

    def within(self, path: str) -> bool:
        return self.path in (path.casefold() if self.folds else path)


def fragment(raw: str, folds: bool) -> Fragment:
    path = (file_separators(raw) if _WINDOWS else raw).removeprefix("./")
    return Fragment(raw, path.casefold() if folds else path, folds)


def fragments(raws: list[str], root: str | os.PathLike) -> list[Fragment]:
    """Each of `raws` as a Fragment, asking once, and only when there is a
    fragment to read, whether the disk under `root` folds case."""
    folds = _folds_case(root) if raws else False
    return [fragment(raw, folds) for raw in raws]


def _folds_case(root: str | os.PathLike) -> bool:
    """Does the filesystem at `root` open a name in another letter case? Asked
    of the root's own name, the one entry sure to exist."""
    path = Path(root).resolve()
    other = path.with_name(path.name.swapcase())
    return other.name != path.name and _same_file(other, path)
