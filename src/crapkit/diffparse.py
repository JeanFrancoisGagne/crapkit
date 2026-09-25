"""Parse `git diff -U0` output into per-file changed line ranges. Pure.

Ranges are new-side. A pure deletion (zero new lines) still marks the line it
happened at, so a function shrunk by an edit is still a touched function.
Paths come from the +++ header (the new side survives renames) and decode
git's C-style quoting through the shared path decoder. A path whose bytes are
not UTF-8 keeps them as surrogates: no row is keyed on it, and the scope
assignment refuses it when a scope takes it (gitpaths, universe).

Hunk body lines are consumed by the counts the @@ header declares, never
pattern-matched: an added source line whose text starts with "++ " arrives as
"+++ <text>" and would otherwise read as a file header, repointing every later
hunk at a phantom path the gate never checks.
"""
from __future__ import annotations

import re

from .gitpaths import header_path

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
    """Point the parser at the file a `+++ ` header names; None for /dev/null.
    A name that is not UTF-8 keys its ranges in its surrogateescape spelling
    (gitpaths names it on stderr), so the scope assignment the gate runs next
    refuses it when a scope takes it instead of passing a file it never read."""
    target = line[4:].removesuffix("\t")
    if target == "/dev/null":
        return None
    path = header_path(target)
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


# --- a UTF-16 file's lines ------------------------------------------------------------
#
# git summarizes a UTF-16 file as binary, and its `--text` patch counts a line at
# every 0A byte. A code unit holding one that is not U+000A (上 is U+4E0A, 0A 4E in
# UTF-16LE; U+0A0A holds two) adds a line the text does not have, so each hunk
# below it lands late. These map git's lines onto the text lines the scorer counts
# (analyze.decode_source: CRLF, a lone CR and LF each end one line).

_BOM_LENGTH = 2


def utf16_line_spans(raw: bytes) -> list[tuple[int, int]]:
    """For each line git counts in a UTF-16 file that opens with its byte-order
    mark, the first and last text line it covers."""
    starts = [0] + [m.end() for m in re.finditer(b"\n", raw) if m.end() < len(raw)]
    ends = [start - 1 for start in starts[1:]] + [len(raw) - 1]
    lines = _unit_lines(_code_units(raw))
    return [(_line_at(lines, -(-(start - _BOM_LENGTH) // 2)), _line_at(lines, (end - _BOM_LENGTH) // 2))
            for start, end in zip(starts, ends)]


def _code_units(raw: bytes) -> list[int]:
    """The 16-bit code units after the byte-order mark; a last odd byte is one
    more unit, as the decoder reads it as one more character."""
    body = raw[_BOM_LENGTH:] + b"\0" * (len(raw) % 2)
    order = "little" if raw.startswith(b"\xff\xfe") else "big"
    return [int.from_bytes(body[i:i + 2], order) for i in range(0, len(body), 2)]


def _unit_lines(units: list[int]) -> list[int]:
    """The text line each code unit sits on."""
    lines, line = [], 1
    for i, unit in enumerate(units):
        lines.append(line)
        line += _ends_line(unit, units[i + 1] if i + 1 < len(units) else None)
    return lines


def _ends_line(unit: int, following: int | None) -> int:
    """1 when this code unit ends a text line: LF, or a CR no LF follows."""
    return int(unit == 0x0A or (unit == 0x0D and following != 0x0A))


def _line_at(lines: list[int], unit: int) -> int:
    """The line of a code unit, the nearest one when the index runs off either end."""
    if not lines:
        return 1
    return lines[min(max(unit, 0), len(lines) - 1)]


def text_line_ranges(ranges: list[tuple[int, int]], spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Ranges in git's lines, moved onto text lines by `utf16_line_spans`."""
    def span(line: int) -> tuple[int, int]:
        return spans[min(max(line, 1), len(spans)) - 1]
    return [(span(first)[0], span(last)[1]) for first, last in ranges]


def rendered_ranges(ranges_by_path: dict[str, list[tuple[int, int]]]) -> str:
    """A `-U0` patch whose hunks cover exactly these new-side ranges, which
    changed_ranges reads back unchanged. Each path is C-quoted."""
    return "".join(f"+++ {_quoted('b/' + path)}\n" + "".join(_hunk(first, last) for first, last in ranges)
                   for path, ranges in ranges_by_path.items())


def _hunk(first: int, last: int) -> str:
    count = last - first + 1
    return f"@@ -0,0 +{first},{count} @@\n" + "+\n" * count


def _quoted(path: str) -> str:
    """git's C quoting, every byte outside printable ASCII in octal, so a name
    with a newline, a quote or a byte that is not UTF-8 reads back as itself."""
    return '"' + "".join(_quoted_byte(b) for b in path.encode("utf-8", "surrogateescape")) + '"'


def _quoted_byte(b: int) -> str:
    return chr(b) if 0x20 <= b < 0x7F and b not in (0x22, 0x5C) else "\\" + format(b, "03o")
