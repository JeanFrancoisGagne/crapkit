"""An istanbul record's positions, moved onto the lines the reader numbers.

Producers number a file's lines by their own rule. @vitest/coverage-v8 ends a
JavaScript line at LF only. A TypeScript file's positions come back through its
source map, and Babel (which @vitest/coverage-istanbul, jest and nyc instrument
with) numbers the source itself; both end a line at every ECMAScript line
terminator: LF, CRLF, a lone CR, U+2028 and U+2029. The reader ends one at LF,
CRLF and a lone CR. All three agree on a file with no lone CR, U+2028 or U+2029,
which is nearly every file, and that file's record passes through untouched.

A record does not say which rule numbered it, so the file's text decides: each
named function's name has to sit on the line its declaration names. The rule
under which more names do wins, and a tie leaves the record as it came. A column
counts UTF-16 code units, as JavaScript does, and places a position within a
line the reader splits at a lone CR: a CR-only file is one line to V8.
"""
from __future__ import annotations

from bisect import bisect_right
import os
from pathlib import Path
import re

from .sourcelines import ECMASCRIPT, LF_ONLY, line_starts

_DISPUTED = (" ".encode(), " ".encode())
_NAME = re.compile(r"[A-Za-z_$][\w$]*")
# The positions attribution reads: a function's declaration start and body end,
# a branch's start, a statement's start.
_POSITIONS = (("fnMap", ("decl", "start")), ("fnMap", ("loc", "end")),
              ("branchMap", ("loc", "start")), ("statementMap", ("start",)))


def _disputed(raw: bytes) -> bool:
    """Whether the rules can number these bytes differently: a lone CR, U+2028 or
    U+2029 is in them. Each test is a byte search, and the cheap one goes first:
    over 29,615 records this took 0.34 s, counting every CR took 0.93 s."""
    lone_cr = b"\r" in raw and raw.count(b"\r") != raw.count(b"\r\n")
    return lone_cr or (b"\xe2\x80" in raw and any(mark in raw for mark in _DISPUTED))


def _code_points(segment: str, units: int) -> int:
    """How many characters of `segment` its first `units` UTF-16 code units hold,
    counting on past its end at one unit a character."""
    index = 0
    while units > 0 and index < len(segment):
        units -= 2 if ord(segment[index]) > 0xFFFF else 1
        index += 1
    return index + max(units, 0)


class _Numbering:
    """One text's lines under one rule, and the reader's line for a position on them."""

    def __init__(self, text: str, starts: list[int], reader: list[int]) -> None:
        self.text, self.starts, self.reader = text, starts, reader

    def line(self, number: int) -> str:
        first, end = self._bounds(number)
        return self.text[first:end]

    def reader_line(self, number: int, column: object) -> int:
        """The reader's line for (line, column); no column means the line's end."""
        first, end = self._bounds(number)
        last = max(first, end - 1)
        at = first + _code_points(self.text[first:end], column) if type(column) is int else last
        return bisect_right(self.reader, min(at, last))

    def _bounds(self, number: int) -> tuple[int, int]:
        """Where line `number` starts, and where its line end stops; a number off
        either end reads as the nearest line."""
        index = min(max(number, 1), len(self.starts)) - 1
        end = self.starts[index + 1] if index + 1 < len(self.starts) else len(self.text)
        return self.starts[index], end


def _at(member: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(member, dict):
            return None
        member = member.get(key)
    return member


def _members(cov: dict, group: str) -> list:
    found = cov.get(group)
    return list(found.values()) if isinstance(found, dict) else []


def _spellable(name: object) -> str | None:
    """The word a function's name puts in its source: `Class.method` spells `method`,
    an anonymous function spells nothing."""
    word = name.rsplit(".", 1)[-1] if isinstance(name, str) else ""
    return word if _NAME.fullmatch(word) else None


def _declared(cov: dict) -> list[tuple[str, int]]:
    """(word, line) for each function whose name its declaration line should spell."""
    pairs = [(_spellable(_at(fn, "name")), _at(fn, "decl", "start", "line"))
             for fn in _members(cov, "fnMap")]
    return [(word, line) for word, line in pairs if word and type(line) is int]


def _spelled(numbering: _Numbering, declared: list[tuple[str, int]]) -> int:
    """How many declarations sit on a line that spells their name, under this rule."""
    return sum(re.search(rf"(?<![\w$]){re.escape(word)}(?![\w$])", numbering.line(line))
               is not None for word, line in declared)


def _producer_numbering(cov: dict, text: str) -> _Numbering | None:
    """The rule that numbered this record, when it is not the reader's own."""
    reader = line_starts(text)
    lf, ecma = (_Numbering(text, line_starts(text, rule), reader) for rule in (LF_ONLY, ECMASCRIPT))
    declared = _declared(cov)
    lf_hits, ecma_hits = _spelled(lf, declared), _spelled(ecma, declared)
    if lf_hits == ecma_hits:
        return None
    chosen = lf if lf_hits > ecma_hits else ecma
    return None if chosen.starts == reader else chosen


def _renumber(cov: dict, numbering: _Numbering) -> None:
    for group, path in _POSITIONS:
        for member in _members(cov, group):
            position = _at(member, *path)
            if isinstance(position, dict) and type(position.get("line")) is int:
                position["line"] = numbering.reader_line(position["line"], position.get("column"))


def _source_bytes(path: Path) -> bytes | None:
    """The file's bytes, or None when it is gone, is no file, or is a key no path
    can spell. os.read, because every measured file is read once more on every
    parse: over 29,615 records Path.read_bytes took 2.58 s and this 1.37 s."""
    try:
        handle = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except (OSError, ValueError):
        return None
    try:
        return _read_all(handle)
    except OSError:  # a directory opens on POSIX and refuses the read
        return None
    finally:
        os.close(handle)


def _read_all(handle: int) -> bytes:
    """Every byte: one read of the file's size and one more byte, which comes back
    short and ends it, unless the file grew since the stat. os.read allocates the
    whole request up front, so a second read of a fixed megabyte to find the end
    cost 10 s over 26,617 files."""
    chunks, want = [], os.fstat(handle).st_size + 1
    while chunk := os.read(handle, want):
        chunks.append(chunk)
        if len(chunk) < want:
            break
        want = 1 << 16
    return b"".join(chunks)


def _disputed_text(cov: dict, source: Path) -> str | None:
    """The source's text, when the record names a function to decide by and the
    rules can number the source differently."""
    raw = _source_bytes(source) if _members(cov, "fnMap") else None
    return raw.decode("utf-8", "replace") if raw is not None and _disputed(raw) else None


def on_reader_lines(cov: object, source: Path) -> object:
    """`cov`, the record of the file at `source`, with every position attribution
    reads moved onto the reader's lines. In place. A record that names no function
    has no name to decide by and costs no read; it comes back as it came, as does
    one whose source cannot be read or holds nothing the rules disagree on."""
    text = _disputed_text(cov, source) if isinstance(cov, dict) else None
    numbering = _producer_numbering(cov, text) if text is not None else None
    if numbering is not None:
        _renumber(cov, numbering)
    return cov
