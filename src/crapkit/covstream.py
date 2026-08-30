"""Coverage artifacts read off the file instead of out of a string.

The whole-document parsers take `text`, so the caller must already hold the
artifact: `read_bytes()` plus its UTF-8 decode put two copies of a 150 MB
artifact on the heap before a single function is attributed. The splitter in
coverage_istanbul already decodes one member at a time; this module gives it a
window that refills from a handle instead of a string that holds everything.

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
import re
from pathlib import Path
from typing import IO, Iterator

from .coverage_istanbul import (_CLOSE_RE, _DECODER, _MEMBER_RE, _OPEN_RE,
                                FnCoverage, _dead_lines, _file_coverage, _rel_path)
from .errors import ToolError

CHUNK = 1 << 20

# The inner close: one object ending inside a larger document, with no claim
# about what follows. _CLOSE_RE anchors at the end of the text and is the outer
# document's business.
_CLOSE_INNER = re.compile(r"\s*\}")


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


def _next_member(w: _Window):
    """The next member header, or None once the object closed or the stream ran
    out. Refills only while the window can neither produce a header nor prove
    the object ended, so a closing brace does not drag the rest of the file in."""
    while True:
        member = _MEMBER_RE.match(w.buf, w.pos)
        if _usable(w, member):
            return member
        if _CLOSE_INNER.match(w.buf, w.pos) is not None:
            return None
        if not w.refill():
            return None


def _whole_value(w: _Window, start: int):
    """The decoded value and its end offset, or None while the window may still
    be hiding more of it. A value ending exactly at the edge is not whole: a
    bare number would otherwise decode as its own truncated prefix."""
    try:
        value, end = _DECODER.raw_decode(w.buf, start)
    except ValueError:
        return None
    return (value, end) if end < len(w.buf) or w.eof else None


def _decode_value(w: _Window, start: int):
    """raw_decode at `start`, growing the window until the value is whole."""
    while True:
        whole = _whole_value(w, start)
        if whole is not None:
            return whole
        if not w.refill():
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
    The same pairs in the same order as coverage_istanbul.split_top_level."""
    _enter_object(w, "istanbul artifact")
    while True:
        member = _next_member(w)
        if member is None:
            _expect_document_end(w)
            return
        yield _take_member(w, member)


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
    while True:
        member = _next_member(w)
        if member is None:
            _leave_object(w, "coverage.py report: 'files' object")
            return
        key, value = _take_member(w, member)
        yield key, value, "sub"


def walk_report(w: _Window, target: str) -> Iterator[tuple[str, object, str]]:
    """(key, value, kind) per top-level member. kind is "member" for an ordinary
    decoded value and "sub" for one member of the `target` object, so meta and
    totals arrive whole and "files" arrives one file at a time."""
    _enter_object(w, "coverage.py report")
    while True:
        member = _next_member(w)
        if member is None:
            # A walk that just stops at the first unreadable byte reports zero
            # dark lines, which is indistinguishable from a fully covered repo.
            _expect_document_end(w)
            return
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
_NO_BRANCH = "coverage.py report lacks branch data — run the lane with branch coverage on"


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
    Same result as parse_istanbul(path.read_text(), ...), same digest as
    sha256(path.read_bytes()), without either whole copy ever existing."""
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
    them separately decoded every member twice — 20.83 s across 13 lanes on a
    31,459-file tree, against 13.07 s merged. Decoding is the whole cost;
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


def _prefix(path_prefix: str) -> str:
    return (path_prefix.rstrip("/") + "/") if path_prefix else ""


def _meta_has_branch(key: str, value: object) -> bool:
    if key != "meta" or not isinstance(value, dict):
        return False
    return bool(value.get("branch_coverage"))


def _require_branch(seen: bool) -> None:
    if not seen:
        raise ToolError(_NO_BRANCH)


def _add_file(out: dict, prefix: str, raw_path: str, data: dict, to_functions):
    """Record one file's functions, or hand back the error it raised.

    The error is CARRIED, not thrown: the whole-document parser saw the report
    before it read a single file, so it always refused a report with no branch
    data first. Members are not ordered — json.dump(sort_keys=True) writes
    "files" ahead of "meta" — so streaming only keeps that precedence by
    finishing the walk before it decides which complaint wins.
    """
    try:
        out[prefix + raw_path.replace("\\", "/")] = to_functions(raw_path, data)
        return None
    except Exception as exc:
        return exc


def _coveragepy_functions(w: _Window, prefix: str) -> dict:
    """path -> function coverage, refusing a report with no branch data."""
    from .coverage_py import _file_functions

    out: dict = {}
    branch, failure = False, None
    for key, value, kind in walk_report(w, "files"):
        if kind == "member":
            branch = branch or _meta_has_branch(key, value)
            continue
        failure = failure or _add_file(out, prefix, key, value, _file_functions)
    _require_branch(branch)
    if failure is not None:
        raise failure
    return out


def parse_coveragepy_file(path: Path | str, *, path_prefix: str, chunk: int = CHUNK
                          ) -> tuple[dict[str, list[FnCoverage]], str]:
    """Per-file function coverage plus the sha256 of the report's own bytes."""
    w, handle = _window(path, chunk)
    with handle:
        per_file = _guarded(lambda: _coveragepy_functions(w, _prefix(path_prefix)),
                            _BAD_REPORT)
    return per_file, w.hasher.hexdigest()


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
        return _guarded(lambda: _coveragepy_missing(w, _prefix(path_prefix)), _BAD_REPORT)
