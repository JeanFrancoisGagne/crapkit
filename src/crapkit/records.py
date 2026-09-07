"""Lossless rows for portable TSV exports and isolated Git patch records.

Ordinary rows keep their original bytes. A row containing delimiters carries
its own version marker and JSON string fields, so it can be decoded without
the file header. Legacy raw rows never interpreted backslashes as escapes.
"""
from __future__ import annotations

from functools import lru_cache
import json
import re

_PREFIX = "@crapkit-record-"
_MARKER = _PREFIX + "v1"
_LINE_BREAKS = re.compile(r"[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def record_lines(text: str):
    """Only LF and CRLF frame physical rows; Unicode separators are data."""
    return (line.removesuffix("\r") for line in text.split("\n"))


def _requires_encoding(line: str, count: int) -> bool:
    """Scan formatted text in C instead of revisiting every field in Python."""
    return line.startswith("#") or line.count("\t") != count - 1 or bool(_LINE_BREAKS.search(line))


@lru_cache(maxsize=8)
def _template(count: int) -> str:
    return "\t".join(["%s"] * count)


def encode_record(fields) -> str:
    """One row without its terminating newline."""
    line = _template(len(fields)) % tuple(fields)
    if _requires_encoding(line, len(fields)):
        return _MARKER + "\t" + json.dumps(list(map(str, fields)), ensure_ascii=True, separators=(",", ":"))
    return line


def decode_record(line: str) -> list[str]:
    """Read ordinary fields or a self-described row; reject unknown encodings."""
    parts = line.split("\t")
    if len(parts) != 2 or not parts[0].startswith(_PREFIX):
        return parts
    if parts[0] != _MARKER:
        raise ValueError(f"unsupported portable record encoding {parts[0]!r}")
    return _string_fields(parts[1])


def _string_fields(text: str) -> list[str]:
    values = json.loads(text)
    if not isinstance(values, list) or any(type(value) is not str for value in values):
        raise ValueError("portable record must contain a JSON array of string fields")
    return values
