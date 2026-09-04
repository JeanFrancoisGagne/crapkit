"""The one reader for the text files a repository owns: crapkit.toml, the marks
file, a portable baseline.

Stdlib and `errors` only, on purpose. Two callers cannot reach `cli._shared`,
where this first lived: the advisory hook, whose module scope must never import
the snapshot store, and `override`, a core module that imports no CLI family.
Each of them carried its own copy of the decode, and each copy was one more
place where a UTF-16 file was a traceback instead of the sentence below.
"""
from __future__ import annotations

from pathlib import Path

from .errors import ConfigError


def repo_text(path: Path, what: str) -> str:
    """The text of a file the repository owns, read the way the shells that
    write it write it.

    utf-8-sig, because PowerShell 5.1's `Out-File -Encoding utf8` puts a BOM in
    front, which tomllib read as `Invalid statement (at line 1, column 1)` and
    the marks reader as a stamp line that was not a stamp followed by a header
    with one field. A bare `Out-File` writes UTF-16 LE instead; that one cannot
    be read as the same file, so it is refused as a configuration error naming
    the bytes and the fix, where before it was a UnicodeDecodeError traceback at
    exit 1, a code the exit table does not define. `what` is the name the
    refusal prints: `crapkit.toml`, the marks file as configured, a merge side
    as git spelled it.
    """
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigError(_not_utf8(what, data, exc)) from None


def _not_utf8(what: str, data: bytes, exc: UnicodeDecodeError) -> str:
    """The refusal, blaming UTF-16 when the first bytes are its mark and the
    offending byte otherwise. The decoder strips a BOM before it reads, so the
    offset it reports is moved back onto the file's own bytes."""
    head = data[:2]
    if head in (b"\xff\xfe", b"\xfe\xff"):
        reason = f"first bytes {head.hex(' ')} = UTF-16, the PowerShell 5.1 Out-File default"
    else:
        offset = exc.start + (len(data) - len(exc.object))
        reason = f"byte {data[offset]:02x} at offset {offset}"
    return f"{what} is not UTF-8 ({reason}); save it as UTF-8"
