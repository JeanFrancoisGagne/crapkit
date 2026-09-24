"""How bytes crapkit did not write become text, and OS text becomes bytes.

Stdlib only, so the advisory hook and the MCP stdio loop can import it. One
rule per kind of source, and each one lives here or in the module named:

- A file the repository owns and crapkit must read exactly (crapkit.toml, a
  portable baseline) is UTF-8 or refused with a sentence naming the byte:
  `repotext.repo_text`.
- The marks file, today's copy, every past revision and each side git hands the
  merge driver, goes through `marks_text`: UTF-16 when a byte-order mark says
  so, else UTF-8 with a BOM dropped, and each byte neither reads as U+FFFD. A
  command that rewrites the file asks `unreadable_byte` first and writes it
  back through `marks_bytes`.
- Text crapkit only reads, ranks or passes along (git's free text such as an
  author name or a commit message, a runner's output, an MCP frame, a
  package.json) goes through `lenient`: a UTF-8 BOM is dropped and each byte
  that is not UTF-8 reads as U+FFFD. git, Node and a browser read such bytes
  the same way, and one of them in an author name inside the churn window used
  to stop every command that reads churn.
- A source file crapkit rewrites (a mutant) goes through `source_text` and
  back through `source_bytes`, which return every byte it held. The scorer
  reads the same order of encodings through `analyze.decode_source`.
- Text the OS hands over (argv, the environment, a host name, a directory name)
  arrives as Python decodes it: a byte that is not UTF-8 is a lone surrogate on
  POSIX. `os_bytes` turns it back into the bytes the OS meant, for a hash or a
  lock key. `os_text` makes it text a store or a file can hold.

A path git names is another kind, and `gitpaths` owns it.
"""
from __future__ import annotations

import codecs
import os

_UTF16 = ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"))
_C1 = "crapkit-c1"


def lenient(data: bytes) -> str:
    """UTF-8 with a leading BOM dropped and each undecodable byte as U+FFFD."""
    return data.removeprefix(codecs.BOM_UTF8).decode("utf-8", "replace")


def utf16_marked(data: bytes) -> bool:
    """True when the bytes open with a UTF-16 byte-order mark: what Windows
    PowerShell 5.1's `Out-File` and `>` write, and what no UTF-8 reader reads."""
    return data.startswith(tuple(mark for mark, _ in _UTF16))


def marks_codec(data: bytes) -> str:
    """The encoding a marks file is read and written back in: UTF-16 when a
    byte-order mark says so, which is what a bare `Out-File` in PowerShell 5.1
    writes, else UTF-8."""
    for mark, codec in _UTF16:
        if data.startswith(mark):
            return codec
    return "utf-8"


def _unmarked(data: bytes, codec: str) -> bytes:
    """`data` without the byte-order mark that opens it, if any."""
    marks = [mark for mark, name in _UTF16 if name == codec] or [codecs.BOM_UTF8]
    return data.removeprefix(marks[0])


def marks_text(data: bytes) -> str:
    """A marks file, or one revision of it, as the rows it holds.

    One rule for today's file, its history and the merge driver's three sides,
    so a revision that read one way in `verify` never reads another way in
    `ratchet report`. A byte that is neither UTF-8 nor part of the UTF-16 the
    mark announced reads as U+FFFD: the file stays readable, and the name that
    held the byte keys no function."""
    codec = marks_codec(data)
    return _unmarked(data, codec).decode(codec, "replace")


def unreadable_byte(data: bytes) -> str | None:
    """The first byte `marks_text` read as U+FFFD, as `byte e9 at offset 57`
    counted in the file's own bytes; None when it read every byte."""
    codec = marks_codec(data)
    body = _unmarked(data, codec)
    try:
        body.decode(codec)
    except UnicodeDecodeError as exc:
        offset = exc.start + len(data) - len(body)
        return f"byte {data[offset]:02x} at offset {offset}"
    return None


def marks_bytes(text: str, codec: str) -> bytes:
    """`text` in the codec `marks_codec` read its file in: UTF-16 behind its
    byte-order mark, else UTF-8 with no BOM."""
    marks = [mark for mark, name in _UTF16 if name == codec] or [b""]
    return marks[0] + text.encode(codec)


def source_codec(raw: bytes) -> str:
    """The encoding a source file is read in: UTF-16 when a byte-order mark
    says so, else UTF-8 when every byte decodes, else cp1252, which Windows
    PowerShell 5.1 and a coding cookie on a Windows machine write."""
    for mark, codec in _UTF16:
        if raw.startswith(mark):
            return codec
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return "cp1252"
    return "utf-8"


def source_text(raw: bytes) -> str:
    """Source bytes as text that `source_bytes` turns back into exactly these
    bytes. A BOM stays as U+FEFF, and the five bytes cp1252 leaves undefined
    read as the C1 control of the same number."""
    return raw.decode(source_codec(raw), _C1)


def source_bytes(text: str, like: bytes) -> bytes:
    """`text` in the encoding `like` was read in."""
    return text.encode(source_codec(like), _C1)


def _c1(error: UnicodeError) -> tuple[str | bytes, int]:
    """cp1252 has no character for 0x81, 0x8d, 0x8f, 0x90 or 0x9d: each reads
    as U+0081 and the like, and writes back as its byte."""
    span = error.object[error.start:error.end]
    if isinstance(error, UnicodeDecodeError):
        return "".join(map(chr, span)), error.end
    return bytes(map(ord, span)), error.end


codecs.register_error(_C1, _c1)


def os_bytes(value: str) -> bytes:
    """The bytes an OS string stands for. A lone surrogate from a POSIX byte
    that is not UTF-8 goes back to that byte; one from broken UTF-16 on Windows
    is kept as its three bytes, so no value raises."""
    try:
        return os.fsencode(value)
    except UnicodeEncodeError:
        return value.encode("utf-8", "surrogatepass")


def os_text(value: str) -> str:
    """An OS string as text a store or a file can hold: UTF-8, with each byte
    that is not UTF-8 read as U+FFFD."""
    return os_bytes(value).decode("utf-8", "replace")
