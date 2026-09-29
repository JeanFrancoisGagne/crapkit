"""Where a source file's lines end: at LF, CRLF and a lone CR.

That is how Python's compiler, coverage.py and analyze.decode_source read
source, so it is how every function span and coverage line crapkit records is
numbered. `str.splitlines` also ends a line at \\x0b, \\x0c, \\x1c, \\x1d, \\x1e,
\\x85, \\u2028 and \\u2029, and each form feed above a function moves it one line
lower than the span says. git's diff ends a line at LF only; diffparse maps its
numbers onto these. A JavaScript coverage producer ends one at LF only or at every
ECMAScript line terminator; istanbul_lines maps its numbers onto these.
"""
from __future__ import annotations

import re

# One line and its end, or the last line when no end follows it.
_LINE = re.compile(r".*?(?:\r\n|\r|\n)|.+", re.DOTALL)

# What ends a line for the reader, and under the two rules its inputs come numbered
# by. git and @vitest/coverage-v8's JavaScript positions end one at LF only;
# ECMAScript, Babel, and the source maps TypeScript positions come back through, at
# LF, CRLF, a lone CR, U+2028 and U+2029.
READER = re.compile(r"\r\n|\r|\n")
LF_ONLY = re.compile(r"\n")
ECMASCRIPT = re.compile(r"\r\n|[\r\n\u2028\u2029]")


def line_starts(text: str, ends: re.Pattern[str] = READER) -> list[int]:
    """The index each line of `text` starts at, line 1 first, when `ends` ends a line.
    A text that ends with a line end has one more line, an empty one."""
    return [0, *(end.end() for end in ends.finditer(text))]


def source_lines(text: str, keepends: bool = False) -> list[str]:
    """`text.splitlines(keepends)`, with only LF, CRLF and a lone CR ending a line."""
    lines = _LINE.findall(text)
    return lines if keepends else [line.rstrip("\r\n") for line in lines]


def line_end(line: str) -> str:
    """The end of one line `source_lines(keepends=True)` returned: "\\r\\n", "\\r",
    "\\n", or "" for a last line with no end."""
    return line[len(line.rstrip("\r\n")):]
