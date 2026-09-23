"""Coverage artifacts read off the file instead of out of a string.

Reading an entire artifact with `read_bytes()` plus its UTF-8 decode puts two
copies of a 150 MB artifact on the heap before a function is attributed. This
module owns the JSON walk and refills a window from a handle. The format
modules project each decoded file into coverage, missing lines or contexts.

Peak becomes O(chunk + largest member) rather than O(artifact): 322.6 -> 52.1 MB
on a 150 MB istanbul artifact, for byte-identical output and the same sha256.

Both shapes are split the same way. An istanbul artifact IS the {path: coverage}
object, so its members are files. A coverage.py report wraps them one level down
in "files", so the walk descends into that member and hands the rest back whole.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import math
import re
from pathlib import Path
from typing import IO, Iterator

from .errors import ToolError

# Sized so most file members fit inside one window. A member that straddles
# the window's end is still framed token by token in Python (_ValueFrame)
# before C decodes it, whatever its size: in crapkit's own report with
# contexts, 9 of 81 members (half its bytes) took that route at 1 MB and 2 at
# 4 MB, and parsing it fell from 1.03 s to 0.26 s (warm medians of 5), for
# about 12 MB more peak heap on a 112 MB artifact. Callers that pass their own
# chunk keep it.
CHUNK = 4 << 20

_WS = r"[ \t\r\n]*"
_MEMBER = r'("(?:[^"\\]|\\.)*")' + _WS + ':' + _WS
_FIRST_MEMBER = re.compile(_WS + _MEMBER, re.DOTALL)
_NEXT_MEMBER = re.compile(_WS + ',' + _WS + _MEMBER, re.DOTALL)
_OPEN_RE = re.compile(_WS + r"\{")
_CLOSE_RE = re.compile(_WS + r"\}" + _WS + r"\Z")


def _finite_number(token: str) -> float:
    number = float(token)
    if not math.isfinite(number):
        raise ToolError(f"unparseable coverage artifact: non-finite JSON number {token}")
    return number


_DECODER = json.JSONDecoder(parse_float=_finite_number, parse_constant=_finite_number)

# The inner close: one object ending inside a larger document, with no claim
# about what follows. _CLOSE_RE anchors at the end of the text and is the outer
# document's business.
_CLOSE_INNER = re.compile(_WS + r"\}")


class _Window:
    """A sliding decoded window over a byte stream, plus the sha256 of the bytes
    that went past. Offsets stay valid across a refill because refilling only
    appends; only drop() ever moves them, and it says so."""

    def __init__(self, handle: IO[bytes], chunk: int = CHUNK):
        self._handle = handle
        self._chunk = max(chunk, 1)
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self.hasher = hashlib.sha256()
        self.buf = ""
        self.pos = 0
        self.eof = False

    def refill(self) -> bool:
        """Pull one more chunk into the window. False once the stream is spent."""
        if self.eof:
            return False
        raw = self._handle.read(self._chunk)
        if not raw:
            self.eof = True
            self.buf += self._decoder.decode(b"", True)
            return False
        self.hasher.update(raw)
        self.buf += self._decoder.decode(raw)
        return True

    def drop(self, i: int) -> None:
        """Consume through offset `i`. Compaction is amortized: slicing the
        window on every member copies the whole tail each time, so the offset
        moves and the copy happens only once the consumed prefix is a chunk."""
        self.pos = i
        if self.pos >= self._chunk:
            self.buf = self.buf[self.pos:]
            self.pos = 0


# --- window-driven splitting ----------------------------------------------

def _usable(w: _Window, member) -> bool:
    """A member header the window can be trusted on. One that runs to the very
    edge is not trustworthy mid-stream: the key string, or the whitespace after
    the colon, may continue in bytes not read yet."""
    return member is not None and (member.end() < len(w.buf) or w.eof)


def _next_member(w: _Window, first: bool):
    """The next member header, or None once the object closed or the stream ran
    out. Refills only while the window can neither produce a header nor prove
    the object ended, so a closing brace does not drag the rest of the file in."""
    pattern = _FIRST_MEMBER if first else _NEXT_MEMBER
    while True:
        member = pattern.match(w.buf, w.pos)
        if _usable(w, member):
            return member
        if _CLOSE_INNER.match(w.buf, w.pos) is not None:
            return None
        if not w.refill():
            return None


_STRUCTURAL = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[{}\[\]"]', re.DOTALL)
_STRING_TOKEN = re.compile(r'["\\]')
_SCALAR_END = re.compile(r'[ \t\r\n,}\]]')


class _ValueFrame:
    """Advance through each chunk once; JSONDecoder still judges the grammar."""

    def __init__(self, text: str, start: int):
        first = text[start:start + 1]
        self.depth = int(first in ("{", "["))
        self.quoted = first == '"'
        self.scalar = first not in ('{', '[', '"')
        self.pos = start if self.scalar else start + 1

    def _string(self, token: str) -> bool:
        if token == "\\":
            self.pos += 1  # also skips the escaped character in the next refill
            return False
        self.quoted = False
        return self.depth == 0

    def _container(self, token: str) -> bool:
        if token.startswith('"'):
            self.quoted = len(token) == 1
        elif token in "{[":
            self.depth += 1
        else:
            self.depth -= 1
        return self.depth == 0

    def _scalar(self, text: str) -> bool:
        end = _SCALAR_END.search(text, self.pos)
        self.pos = len(text)
        return end is not None

    def _token(self, text: str) -> str | None:
        pattern = _STRING_TOKEN if self.quoted else _STRUCTURAL
        token = pattern.search(text, self.pos)
        if token is None:
            self.pos = max(self.pos, len(text))
            return None
        self.pos = token.end()
        return token.group()

    def advance(self, text: str) -> bool:
        if self.scalar:
            return self._scalar(text)
        while True:
            token = self._token(text)
            if token is None:
                return False
            consume = self._string if self.quoted else self._container
            if consume(token):
                return True


def _value_ended(w: _Window, end: int) -> bool:
    if end == len(w.buf):
        return w.eof or w.buf[end - 1] in '"}]'
    return w.buf[end] in " \t\r\n,}]"


def _available_value(w: _Window, start: int):
    """Keep the C decoder fast path when the current window holds the value."""
    try:
        value, end = _DECODER.raw_decode(w.buf, start)
    except ValueError:
        return None
    return (value, end) if _value_ended(w, end) else None


def _decode_value(w: _Window, start: int):
    """Try the current window once; frame incomplete values before retrying."""
    whole = _available_value(w, start)
    if whole is not None:
        return whole
    frame = _ValueFrame(w.buf, start)
    while not frame.advance(w.buf) and w.refill():
        pass
    return _DECODER.raw_decode(w.buf, start)


def _enter_object(w: _Window, what: str) -> None:
    while _OPEN_RE.match(w.buf, w.pos) is None and w.refill():
        pass
    opening = _OPEN_RE.match(w.buf, w.pos)
    if opening is None:
        raise ValueError(f"{what} is not a JSON object")
    w.drop(opening.end())


def _expect_document_end(w: _Window) -> None:
    while w.refill():
        pass
    if _CLOSE_RE.match(w.buf, w.pos) is None:
        raise ValueError(f"unexpected content at {w.buf[w.pos:w.pos + 80]!r}")


def _take_member(w: _Window, member) -> tuple[str, object]:
    """Decode one member's value and step the window past it."""
    key, start = member.group(1), member.end()
    value, end = _decode_value(w, start)
    w.drop(end)
    return json.loads(key), value


def split_window(w: _Window) -> Iterator[tuple[str, object]]:
    """(key, value) per member of the outer object, one value live at a time.
    The same pairs in the same order as a whole-document JSON decode."""
    _enter_object(w, "istanbul artifact")
    first = True
    while True:
        member = _next_member(w, first)
        if member is None:
            _expect_document_end(w)
            return
        yield _take_member(w, member)
        first = False


# --- coverage.py: the same walk, one level down ---------------------------

def _leave_object(w: _Window, what: str) -> None:
    close = _CLOSE_INNER.match(w.buf, w.pos)
    if close is None:
        raise ValueError(f"unterminated {what}")
    w.drop(close.end())


def _walk_nested(w: _Window, start: int) -> Iterator[tuple[str, object, str]]:
    """The members of the object at `start`, one at a time."""
    w.pos = start
    _enter_object(w, "coverage.py report: 'files'")
    first = True
    while True:
        member = _next_member(w, first)
        if member is None:
            _leave_object(w, "coverage.py report: 'files' object")
            return
        key, value = _take_member(w, member)
        yield key, value, "sub"
        first = False


def walk_report(w: _Window, target: str) -> Iterator[tuple[str, object, str]]:
    """(key, value, kind) per top-level member. kind is "member" for an ordinary
    decoded value and "sub" for one member of the `target` object, so meta and
    totals arrive whole and "files" arrives one file at a time."""
    _enter_object(w, "coverage.py report")
    first = True
    while True:
        member = _next_member(w, first)
        if member is None:
            # A walk that just stops at the first unreadable byte reports zero
            # dark lines, which is indistinguishable from a fully covered repo.
            _expect_document_end(w)
            return
        first = False
        if json.loads(member.group(1)) == target:
            yield from _walk_nested(w, member.end())
            continue
        key, value = _take_member(w, member)
        yield key, value, "member"


# --- public readers --------------------------------------------------------

def _guarded(work, message: str):
    """Run a walk, reporting any parse failure the way the whole-document
    parsers do. A ToolError the walk raised itself is already the right error
    and keeps its own wording."""
    try:
        return work()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"{message}: {exc}") from exc


def read_walk(path: Path | str, walk, message: str, chunk: int = CHUNK):
    """`walk` over the artifact's window, plus the sha256 of the bytes it read.

    The adapters' one door into this module: each format hands in the walk that
    projects its members, and gets back what the walk built and the digest of
    the artifact's own bytes, which costs no second read."""
    with open(path, "rb") as handle:
        w = _Window(handle, chunk)
        result = _guarded(lambda: walk(w), message)
    return result, w.hasher.hexdigest()
