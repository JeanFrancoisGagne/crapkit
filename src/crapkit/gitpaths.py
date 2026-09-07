"""Decode Git record framing without changing a path's spelling."""
from __future__ import annotations

PATH_FORMAT = "root-relative-exact"
_SIMPLE_ESCAPES = {"n": 10, "t": 9, "r": 13, '"': 34, "\\": 92,
                   "a": 7, "b": 8, "f": 12, "v": 11}


def _escape_at(body: str, i: int) -> tuple[bytes, int]:
    nxt = body[i + 1] if i + 1 < len(body) else ""
    if nxt.isdigit():
        return bytes([int(body[i + 1:i + 4], 8)]), i + 4
    if nxt in _SIMPLE_ESCAPES:
        return bytes([_SIMPLE_ESCAPES[nxt]]), i + 2
    return nxt.encode("utf-8"), i + 2


def unquote_path(line: str) -> str:
    """Decode C-quoted Git paths; Git already supplies directory slashes."""
    if len(line) < 2 or not line.startswith('"') or not line.endswith('"'):
        # Source patch bodies can carry opaque bytes; a path cannot.
        line.encode("utf-8")
        return line
    body, out, i = line[1:-1], bytearray(), 0
    while i < len(body):
        chunk, i = _path_character(body, i)
        out += chunk
    return out.decode("utf-8")


def _path_character(body: str, i: int) -> tuple[bytes, int]:
    if body[i] == "\\":
        return _escape_at(body, i)
    return body[i].encode("utf-8"), i + 1


def history_line(raw: str) -> str:
    """Remove a log record terminator, retaining spaces and Unicode separators."""
    return raw.removesuffix("\n").removesuffix("\r")
