"""Changed new-side lines read by unidiff 1.0.1 from a unified diff. No crapkit.

- A hunk's changed new-side lines are the lines it adds: target_start through
  target_start + target_length - 1 (GNU diffutils, Detailed Unified format).
- A hunk that adds no line, a pure deletion, has no new-side line. crapkit
  marks the line the header names instead: the line before the deletion,
  where GNU diffutils says an empty hunk ends, and line 1 at the top of a file.
  touch() is that reading, a named transform (ruling H9).
- A file the diff deletes has no new side. That is a target of /dev/null,
  not unidiff's is_removed_file, which also reads a file emptied to no lines
  (target b/f.py, header +0,0) as removed: an oracle bug kept out here.

unidiff's own path reading is kept out of any check (an oracle bug): in 1.0.1
it decodes the octal escapes of a C-quoted name but keeps the quotes, leaves
\\" and \\\\ escaped, and turns \\t into a tab that then cuts the name as if a
timestamp followed. path() drops the quotes of a name with no escape left in
it and refuses any other.
"""
from __future__ import annotations

from unidiff import PatchSet


class UnreadablePath(ValueError):
    """unidiff did not decode this quoted path."""


def path(patched) -> str:
    """The target path without its b/ prefix, as git spells it on disk."""
    name = patched.path
    if name.startswith('"'):
        inner = name[1:-1]
        if "\\" in inner or not name.endswith('"'):
            raise UnreadablePath(name)
        return inner
    return name


def added(hunk) -> tuple[int, int] | None:
    """The hunk's new-side lines as (first, last); None for a pure deletion."""
    if not hunk.target_length:
        return None
    return hunk.target_start, hunk.target_start + hunk.target_length - 1


def touch(hunk) -> tuple[int, int]:
    """H9: a pure deletion marks the line its header names, at least line 1."""
    line = max(hunk.target_start, 1)
    return line, line


def _span(hunk) -> tuple[int, int]:
    return added(hunk) or touch(hunk)


def files(diff_text: str) -> list:
    """The diff's files with a new side, in diff order."""
    return [patched for patched in PatchSet(diff_text) if patched.target_file != "/dev/null"]


def spans(diff_text: str) -> list[list[tuple[int, int]]]:
    """Each file's hunk spans in diff order, H9 applied, files unnamed."""
    return [[_span(hunk) for hunk in patched] for patched in files(diff_text)]


def ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """{path: hunk spans} with H9 applied; a file with no hunk is left out."""
    found = {path(patched): [_span(hunk) for hunk in patched] for patched in files(diff_text)}
    return {name: hunks for name, hunks in found.items() if hunks}
