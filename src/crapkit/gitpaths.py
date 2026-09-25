"""Decode Git record framing without changing a path's spelling.

A path is UTF-8 text here, the one spelling every row, mark and key is joined
on. git names a file in the bytes its name was created with, and a Linux
checkout can hold a Latin-1 name that has no UTF-8 spelling at all. Such a name
comes back as a value like any other: its surrogateescape spelling, the str the
OS reads back as the same bytes (Q17's travel rule). `readable` tells it apart,
and each reader decides what the name means to it. The scope assignment
(universe.scan_files) refuses one a scope takes and lists the rest as left out,
lane reuse counts one as a change, and churn skips it. Nothing here prints.
"""
from __future__ import annotations

from .repotext import backslashed, escaped

PATH_FORMAT = "root-relative-exact"
_SIMPLE_ESCAPES = {"n": 10, "t": 9, "r": 13, '"': 34, "\\": 92,
                   "a": 7, "b": 8, "f": 12, "v": 11}


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


def shown(path: str) -> str:
    r"""A path for a message: each byte that is not UTF-8 as `\xNN`. A lone
    surrogate outside the escape range, which only a Windows command line hands
    over, shows as the three bytes of broken UTF-16 it stands for."""
    try:
        raw = path.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        raw = path.encode("utf-8", "surrogatepass")
    return backslashed(raw)


def repo_path(raw: bytes) -> str:
    """The path git named in `raw`. Bytes that are not UTF-8 come back in their
    surrogateescape spelling, which matches a scope's prefix and extension,
    opens the same file, and is never keyed."""
    return escaped(raw)


def nul_paths(out: bytes) -> list[str]:
    """`-z` records in git's order, decoded without newline conversion, quoting
    or trimming."""
    return [repo_path(record) for record in out.split(b"\0") if record]


def split_record(record: bytes, fields: int) -> tuple[str, str]:
    """A `-z` record whose path follows `fields` tab-separated ASCII fields
    (`ls-files -s`, `diff --numstat`): those fields, then the path."""
    *meta, raw = record.split(b"\t", fields)
    return b"\t".join(meta).decode("ascii"), repo_path(raw)


def header_path(target: str) -> str:
    """The path a `+++ ` header names, `b/` prefix dropped, from a patch read
    with surrogateescape, by unquote_path's rule."""
    raw = _path_bytes(target)
    return repo_path(raw[2:] if raw.startswith(b"b/") else raw)


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
    slashes. A name that is not UTF-8 comes back as header_path's does."""
    return repo_path(_path_bytes(line))


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
