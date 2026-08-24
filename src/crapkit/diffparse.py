"""Parse `git diff -U0` output into per-file changed line ranges. Pure.

Ranges are new-side. A pure deletion (zero new lines) still marks the line it
happened at, so a function shrunk by an edit is still a touched function.
Paths come from the +++ header (the new side survives renames) and decode
git's C-style quoting via the churn module's helper.

Hunk body lines are consumed by the counts the @@ header declares, never
pattern-matched: an added source line whose text starts with "++ " arrives as
"+++ <text>" and would otherwise read as a file header, repointing every later
hunk at a phantom path the gate never checks.
"""
from __future__ import annotations

import re

from .churn import _unquote_git_path

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


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
    target = line[4:].strip()
    if target == "/dev/null":
        return None
    target = _unquote_git_path(target)
    path = target[2:] if target.startswith("b/") else target
    path = path.replace("\\", "/")
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
    ranges: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    rem_old = rem_new = 0
    for line in diff_text.splitlines():
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
