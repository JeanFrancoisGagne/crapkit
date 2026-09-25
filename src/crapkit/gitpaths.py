"""Decode Git record framing without changing a path's spelling.

A path is UTF-8 text here, the one spelling every row, mark and key is joined
on. git names a file in the bytes its name was created with, and a Linux
checkout can hold a Latin-1 name that has no UTF-8 spelling at all. Such a file
is left out of every answer, and stderr names it once per command with the fix,
so one legacy file name under docs/ no longer ends `init`, `inventory` or the
pre-commit gate with a traceback.

Left out is not forgotten. A whole-tree listing keeps the raw names it dropped
(`PathList.left_out`), and a diff header keeps its name as the surrogateescape
spelling, the str the OS reads back as the same bytes. The scope assignment
(universe.scan_files) reads both, and refuses a name a scope takes.
"""
from __future__ import annotations

import sys

PATH_FORMAT = "root-relative-exact"
_SIMPLE_ESCAPES = {"n": 10, "t": 9, "r": 13, '"': 34, "\\": 92,
                   "a": 7, "b": 8, "f": 12, "v": 11}
_NAMED = 5
_left_out: set[bytes] = set()


class PathList(list):
    """The paths a `-z` listing named, in git's order, and the raw names it left
    out because they are not UTF-8."""

    def __init__(self, paths=(), left_out: tuple[bytes, ...] = ()) -> None:
        super().__init__(paths)
        self.left_out = left_out


def reset_left_out() -> None:
    """Start a new command: each name it leaves out is named again."""
    _left_out.clear()


def readable(path: str) -> bool:
    """Whether a path has the UTF-8 spelling crapkit keys on. A surrogateescape
    spelling of a name that is not UTF-8 has none."""
    if path.isascii():
        return True
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def spelled(raw: bytes) -> str:
    """A name in bytes that are not UTF-8 as the str the OS reads back as those
    bytes: it matches a scope's prefix and extension, and is never keyed."""
    return raw.decode("utf-8", "surrogateescape")


def shown(path: str) -> str:
    r"""A path for a message: each byte that is not UTF-8 as `\xNN`. A lone
    surrogate outside the escape range, which only a Windows command line hands
    over, shows as the three bytes of broken UTF-16 it stands for."""
    try:
        raw = path.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        raw = path.encode("utf-8", "surrogatepass")
    return raw.decode("utf-8", "backslashreplace")


def repo_path(raw: bytes) -> str | None:
    """The path git named in `raw`, or None when those bytes are not UTF-8."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        _name_left_out(raw)
        return None


def _name_left_out(raw: bytes) -> None:
    """One stderr line per name, the first five of them, then one line more."""
    if raw in _left_out:
        return
    _left_out.add(raw)
    if len(_left_out) <= _NAMED:
        print(f"crapkit: left out {shown(spelled(raw))}: git names it in bytes "
              "that are not UTF-8, and crapkit reads every path as UTF-8; rename it "
              "(git mv) to have it read", file=sys.stderr)
    elif len(_left_out) == _NAMED + 1:
        print("crapkit: left out more names that are not UTF-8 than the five above", file=sys.stderr)


def nul_records(out: bytes) -> list[str | None]:
    """`-z` records in git's order, None where a name is not UTF-8, so a reader
    that pairs records (`--name-status`) keeps its place."""
    return [repo_path(record) for record in out.split(b"\0") if record]


def nul_paths(out: bytes) -> PathList:
    """`-z` path records decoded without newline conversion, quoting or
    trimming, each one that is not UTF-8 left out and kept in `left_out`."""
    listed, left_out = PathList(), []
    for record in filter(None, out.split(b"\0")):
        path = repo_path(record)
        if path is None:
            left_out.append(record)
        else:
            listed.append(path)
    listed.left_out = tuple(left_out)
    return listed


def split_record(record: bytes, fields: int) -> tuple[str, str | None]:
    """A `-z` record whose path follows `fields` tab-separated ASCII fields
    (`ls-files -s`, `diff --numstat`): those fields, then the path or None."""
    *meta, raw = record.split(b"\t", fields)
    return b"\t".join(meta).decode("ascii"), repo_path(raw)


def header_path(target: str) -> str:
    """The path a `+++ ` header names, `b/` prefix dropped, from a patch read
    with surrogateescape, by unquote_path's rule."""
    raw = _path_bytes(target)
    return _named(raw[2:] if raw.startswith(b"b/") else raw)


def _named(raw: bytes) -> str:
    """A name as a path. One that is not UTF-8 is named on stderr as left out
    and comes back spelled with surrogates, so the scope assignment can refuse
    it when a scope takes it and no reader keys it."""
    path = repo_path(raw)
    return spelled(raw) if path is None else path


def _escape_at(body: str, i: int) -> tuple[bytes, int]:
    nxt = body[i + 1] if i + 1 < len(body) else ""
    if nxt.isdigit():
        return bytes([int(body[i + 1:i + 4], 8)]), i + 4
    if nxt in _SIMPLE_ESCAPES:
        return bytes([_SIMPLE_ESCAPES[nxt]]), i + 2
    return nxt.encode("utf-8"), i + 2


def _quoted(line: str) -> bool:
    return len(line) >= 2 and line.startswith('"') and line.endswith('"')


def unquote_path(line: str) -> str:
    """Decode a C-quoted or plain git path line; git already supplies directory
    slashes. A name that is not UTF-8 follows header_path's one rule instead of
    raising."""
    return _named(_path_bytes(line))


def _path_bytes(line: str) -> bytes:
    """The bytes a git path line stands for: C quoting undone, and each byte a
    surrogateescape decode kept as a surrogate put back."""
    if not _quoted(line):
        return line.encode("utf-8", "surrogateescape")
    body, out, i = line[1:-1], bytearray(), 0
    while i < len(body):
        chunk, i = _path_character(body, i)
        out += chunk
    return bytes(out)


def _path_character(body: str, i: int) -> tuple[bytes, int]:
    if body[i] == "\\":
        return _escape_at(body, i)
    return body[i].encode("utf-8", "surrogateescape"), i + 1


def history_line(raw: str) -> str:
    """Remove a log record terminator, retaining spaces and Unicode separators."""
    return raw.removesuffix("\n").removesuffix("\r")
