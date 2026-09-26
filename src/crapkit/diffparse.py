"""Parse `git diff -U0` output into per-file changed line ranges. Pure but for
`worktree_ranges`, which reads the files a working-tree diff names.

Ranges are new-side. A pure deletion (zero new lines) still marks the line it
happened at, so a function shrunk by an edit is still a touched function.
Paths come from the +++ header (the new side survives renames) and decode
git's C-style quoting through the shared path decoder.

Hunk body lines are consumed by the counts the @@ header declares, never
pattern-matched: an added source line whose text starts with "++ " arrives as
"+++ <text>" and would otherwise read as a file header, repointing every later
hunk at a phantom path the gate never checks.

`changed_ranges` answers in git's line numbers, and git ends a line at LF only.
Function spans and coverage lines come from readers that also end a line at a
lone CR (analyze.decode_source, Python's compiler, coverage.py, ECMAScript), so
anything compared with them goes through `reader_ranges` first. `git_span` goes
the other way, for a function's span handed to git (`git log -L`).
"""
from __future__ import annotations

from bisect import bisect_right
import re
from pathlib import Path
from typing import Callable

from .gitpaths import unquote_path

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
# What ends a line for the reader: an LF, alone or after a CR, and a CR no LF follows.
_READER_LINE_END = re.compile(rb"\r(?!\n)|\n")
_LONE_CR = re.compile(rb"\r(?!\n)")


def _spend_body_line(line: str, rem_old: int, rem_new: int) -> tuple[int, int] | None:
    """Charge one line to the hunk body still owed, returning what remains.

    None means the line is diff structure, not body: either nothing is owed or
    the hunk ended early.
    """
    if rem_old <= 0 and rem_new <= 0:
        return None
    if line.startswith("\\"):  # "\ No newline at end of file"
        return rem_old, rem_new
    if line.startswith("-"):
        return rem_old - 1, rem_new
    if line.startswith("+"):
        return rem_old, rem_new - 1
    # -U0 hunks contain only +/- lines; anything else means the hunk ended
    # early (truncated or synthetic diff) - reparse it normally.
    return None


def _open_file(line: str, ranges: dict[str, list[tuple[int, int]]]) -> str | None:
    """Point the parser at the file a `+++ ` header names; None for /dev/null."""
    target = line[4:].removesuffix("\t")
    if target == "/dev/null":
        return None
    target = unquote_path(target)
    path = target[2:] if target.startswith("b/") else target
    ranges.setdefault(path, [])
    return path


def _new_side_range(start: int, count: int) -> tuple[int, int]:
    """New-side span a hunk covers."""
    if count == 0:  # pure deletion: mark the touch point
        return max(start, 1), max(start, 1)
    return start, start + count - 1


def _record_hunk(
    m: re.Match[str], current: str | None, ranges: dict[str, list[tuple[int, int]]]
) -> tuple[int, int]:
    """Land the hunk's range on the current file and return the body counts it owes.

    An omitted count in the @@ header means one line. A hunk with no current
    file still declares a body to consume.
    """
    rem_old = int(m.group(2)) if m.group(2) is not None else 1
    rem_new = int(m.group(4)) if m.group(4) is not None else 1
    if current is not None:
        ranges[current].append(_new_side_range(int(m.group(3)), rem_new))
    return rem_old, rem_new


def _files_with_hunks(
    ranges: dict[str, list[tuple[int, int]]],
) -> dict[str, list[tuple[int, int]]]:
    """Drop files a header introduced but no hunk ever touched."""
    return {p: r for p, r in ranges.items() if r}


def changed_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Each file's changed new-side ranges, in git's line numbers."""
    ranges: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    rem_old = rem_new = 0
    for line in diff_text.split("\n"):
        owed = _spend_body_line(line, rem_old, rem_new)
        if owed is not None:
            rem_old, rem_new = owed
            continue
        rem_old = rem_new = 0
        if line.startswith("+++ "):
            current = _open_file(line, ranges)
            continue
        m = _HUNK.match(line)
        if m:
            rem_old, rem_new = _record_hunk(m, current, ranges)
    return _files_with_hunks(ranges)


def _git_line_starts(raw: bytes) -> list[int]:
    """The reader line each git line starts on, git line 1 first, then one entry
    past the last git line: the reader line after the one holding the file's
    last byte. An LF that closes the file opens no git line.
    """
    starts, line = [1], 1
    for end in _READER_LINE_END.finditer(raw):
        line += 1
        if end.group() == b"\n":
            starts.append(line)
    if raw.endswith(b"\n"):
        return starts
    return starts + [line + (not raw.endswith(b"\r"))]


def _on_reader_lines(ranges: list[tuple[int, int]], raw: bytes | None) -> list[tuple[int, int]]:
    """One file's git ranges moved onto the reader lines its bytes hold.

    A git line past the end, which only bytes read after the diff was taken can
    produce, is read as the last git line.
    """
    if raw is None or not _LONE_CR.search(raw):
        return ranges
    starts = _git_line_starts(raw)
    last = len(starts) - 1
    return [(starts[min(lo, last) - 1], starts[min(hi, last)] - 1) for lo, hi in ranges]


def reader_ranges(
    ranges_by_path: dict[str, list[tuple[int, int]]],
    new_side: Callable[[str], bytes | None],
) -> dict[str, list[tuple[int, int]]]:
    """git's ranges on the lines a reader numbers, each file placed by its own bytes.

    `new_side(path)` answers the bytes the diff's new side holds for the path,
    or None where there are none. Each lone CR above a git line moves it one
    reader line down, and a git line holding lone CRs spans every reader line in
    it: a new CR-only file is one git line and every function in it.
    """
    return {path: _on_reader_lines(ranges, new_side(path))
            for path, ranges in ranges_by_path.items()}


def _file_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:  # deleted since the diff, or not a file
        return None


def worktree_ranges(diff_text: str, root: Path) -> dict[str, list[tuple[int, int]]]:
    """`changed_ranges` of a diff whose new side is the working tree under `root`,
    on the reader's lines. The diff's paths are root-relative (gitio runs every
    git with diff.relative)."""
    return reader_ranges(changed_ranges(diff_text), lambda rel: _file_bytes(root / rel))


def git_span(raw: bytes | None, start: int, end: int) -> tuple[int, int]:
    """A span of reader lines in git's numbers: the git lines that hold it.

    `raw` is the file at the revision git will number the span in, or None where
    there is none. A reader line past the end reads as the last git line.
    """
    if raw is None or not _LONE_CR.search(raw):
        return start, end
    starts = _git_line_starts(raw)[:-1]
    return bisect_right(starts, start), bisect_right(starts, end)
