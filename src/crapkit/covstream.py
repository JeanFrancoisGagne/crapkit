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

from .coverage_istanbul import (FnCoverage, _dead_lines, _file_coverage, _rel_path)
from .errors import ToolError

CHUNK = 1 << 20

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

def _window(path: Path | str, chunk: int) -> tuple[_Window, IO[bytes]]:
    handle = open(path, "rb")
    return _Window(handle, chunk), handle


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


_BAD_ISTANBUL = "unparseable istanbul artifact"
_BAD_REPORT = "unparseable coverage.py report"


def _istanbul_map(w: _Window, repo_root: str, per_file) -> dict:
    out = {}
    for abs_path, cov in split_window(w):
        out[_rel_path(abs_path, repo_root)] = per_file(cov)
    return out


def _require_files(per_file: dict) -> None:
    """A zero-file artifact scores as full coverage if it is let through, so
    both istanbul readers refuse one in the same words."""
    if not per_file:
        raise ToolError(
            "istanbul artifact is empty (zero files) — the coverage run measured nothing")


def parse_istanbul_file(path: Path | str, *, repo_root: str, chunk: int = CHUNK
                        ) -> tuple[dict[str, list[FnCoverage]], str]:
    """Per-file function coverage plus the sha256 of the artifact's own bytes.
    The whole-document records and sha256(path.read_bytes()) digest, without
    either whole copy ever existing."""
    w, handle = _window(path, chunk)
    with handle:
        per_file = _guarded(lambda: _istanbul_map(w, repo_root, _file_coverage),
                            _BAD_ISTANBUL)
    _require_files(per_file)
    return per_file, w.hasher.hexdigest()


def _istanbul_both(w: _Window, repo_root: str) -> tuple[dict, dict]:
    per_file, dead = {}, {}
    for abs_path, cov in split_window(w):
        rel = _rel_path(abs_path, repo_root)
        per_file[rel] = _file_coverage(cov)
        dead[rel] = _dead_lines(cov)
    return per_file, dead


def parse_istanbul_both_file(path: Path | str, *, repo_root: str, chunk: int = CHUNK
                             ) -> tuple[dict[str, list[FnCoverage]], dict[str, set[int]], str]:
    """Function coverage AND dead lines from ONE walk, plus the same digest.

    verify asks both questions of every istanbul artifact: the lane wants
    function coverage, diff coverage wants the lines no statement ran. Asking
    them separately decoded every member twice — 12.85 s over 13 lanes of a
    31,459-file tree, against 7.80 s merged. Decoding is the whole cost;
    _dead_lines over an already decoded file is near free.

    The digest is this window's, so it hashes the same bytes parse_istanbul_file
    hashes and no recorded artifact_sha256 moves.
    """
    w, handle = _window(path, chunk)
    with handle:
        per_file, dead = _guarded(lambda: _istanbul_both(w, repo_root), _BAD_ISTANBUL)
    _require_files(per_file)
    return per_file, dead, w.hasher.hexdigest()


def parse_istanbul_missing_file(path: Path | str, *, repo_root: str,
                                chunk: int = CHUNK) -> dict[str, set[int]]:
    """Per measured file, the lines whose statement never ran."""
    w, handle = _window(path, chunk)
    with handle:
        return _guarded(lambda: _istanbul_map(w, repo_root, _dead_lines), _BAD_ISTANBUL)


def lane_prefix(path_prefix: str) -> str:
    """The lane's `path_prefix` as it is actually glued onto a measured path.

    Public because the lane layer has to take it back OFF: it judges whether a
    measured path escaped the checkout, and that question is about the path the
    runner wrote, not about the key crapkit built out of it. Spelling the join
    twice let `backend/` turn `/other/checkout/a.py` into something that reads
    relative, and the refusal went silent on every lane that sets the knob."""
    return (path_prefix.rstrip("/") + "/") if path_prefix else ""


def _meta_has_branch(key: str, value: object) -> bool:
    if key != "meta" or not isinstance(value, dict):
        return False
    return bool(value.get("branch_coverage"))


class _Files:
    """What the walk learned about the report's files, decided at the end.

    Both verdicts wait for the whole walk. Members are not ordered —
    json.dump(sort_keys=True) writes "files" ahead of "meta" — so a reader that
    judges at the first file refuses a report whose branch flag it has not read
    yet, and the whole-document parser has always seen meta first.
    """

    def __init__(self) -> None:
        self.per_file: dict[str, list[FnCoverage]] = {}
        self.dead: dict[str, set[int]] = {}
        self.regionless: list[str] = []
        self.total = 0

    def add(self, prefix: str, raw_path: str, data: dict) -> None:
        from .coverage_py import _file_functions, has_regions

        self.total += 1
        path = prefix + raw_path.replace("\\", "/")
        self.dead[path] = set(data.get("missing_lines", ()))
        if not has_regions(data):
            self.regionless.append(raw_path)
            return
        self.per_file[path] = _file_functions(data)


def _coveragepy_both(w: _Window, prefix: str, label: str) -> tuple[dict, dict]:
    """path -> function coverage, salvaging the same way the whole-document
    parser does: a statement-based downgrade with no branch data, and files with
    no regions skipped rather than fatal."""
    from .coverage_py import judge_branch, judge_regions

    files, branch = _Files(), False
    for key, value, kind in walk_report(w, "files"):
        if kind == "member":
            branch = branch or _meta_has_branch(key, value)
            continue
        files.add(prefix, key, value)
    # Same order as the whole-document reader: regions decide first, so the two
    # cannot answer one report differently.
    judge_regions(files.regionless, files.total, label)
    judge_branch(branch, files.per_file, label)
    return files.per_file, files.dead


def parse_coveragepy_file(path: Path | str, *, path_prefix: str, chunk: int = CHUNK,
                          label: str = "") -> tuple[dict[str, list[FnCoverage]], str]:
    """Per-file function coverage plus the sha256 of the report's own bytes."""
    per_file, _, digest = parse_coveragepy_both_file(
        path, path_prefix=path_prefix, chunk=chunk, label=label)
    return per_file, digest


def parse_coveragepy_both_file(path: Path | str, *, path_prefix: str, chunk: int = CHUNK,
                              label: str = "") -> tuple[dict, dict, str]:
    """Function coverage, missing lines, and byte digest from one report walk."""
    w, handle = _window(path, chunk)
    with handle:
        per_file, dead = _guarded(lambda: _coveragepy_both(w, lane_prefix(path_prefix), label),
                                  _BAD_REPORT)
    return per_file, dead, w.hasher.hexdigest()


def _coveragepy_missing(w: _Window, prefix: str) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for key, value, kind in walk_report(w, "files"):
        if kind == "sub":
            out[prefix + key.replace("\\", "/")] = set(value.get("missing_lines", ()))
    return out


def parse_coveragepy_missing_file(path: Path | str, *, path_prefix: str,
                                  chunk: int = CHUNK) -> dict[str, set[int]]:
    """Per measured file, the lines coverage.py reports as never run."""
    w, handle = _window(path, chunk)
    with handle:
        return _guarded(lambda: _coveragepy_missing(w, lane_prefix(path_prefix)), _BAD_REPORT)


def _coveragepy_contexts(w: _Window, prefix: str, source_path: str) -> dict:
    from .coverage_py import _line_contexts

    selected = {}
    for key, value, kind in walk_report(w, "files"):
        if kind == "sub" and prefix + key.replace("\\", "/") == source_path:
            selected = _line_contexts(value.get("contexts", {}))
    return selected


def parse_coveragepy_contexts_file(path: Path | str, *, path_prefix: str,
                                  source_path: str, chunk: int = CHUNK) -> dict[int, list[str]]:
    """One repository path's line contexts, after validating the whole report."""
    w, handle = _window(path, chunk)
    with handle:
        return _guarded(lambda: _coveragepy_contexts(w, lane_prefix(path_prefix), source_path),
                        _BAD_REPORT)
