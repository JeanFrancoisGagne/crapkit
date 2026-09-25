"""Every rule for how bytes crapkit did not write become text, and OS text
becomes bytes. A reader names its kind here instead of spelling a decode
policy; the last paragraph lists the only exceptions.

Stdlib and `errors` only, on purpose. The advisory hook, whose module scope
must never import the snapshot store, `override`, a core module that imports no
CLI family, and the MCP stdio loop all read through this module. Each of them
once carried its own copy of a decode, and each copy was one more place where a
UTF-16 file was a traceback instead of a sentence.

- A file the repository owns and crapkit must read exactly (crapkit.toml, the
  marks file, a portable baseline) is UTF-8, a byte-order mark read past, or
  refused with a sentence naming the bytes: `repo_text`.
- JSON a repository or an installer wrote (package.json, a plugin's manifest and
  hooks file, Claude Code's installed_plugins.json) reads by the same rule and
  must hold one JSON object, or is refused naming the file: `repo_json`. A
  coverage artifact, read a chunk at a time, takes the same rule through
  `JsonStream`, and `json_kind` names the type a value arrived as.
- Text crapkit only reads, ranks or passes along (git's free text such as an
  author name or a commit message, a runner's output, an MCP frame) goes
  through `lenient`: a UTF-8 BOM is dropped and each byte that is not UTF-8
  reads as U+FFFD. git, Node and a browser read such bytes the same way, and
  one of them in an author name inside the churn window used to stop every
  command that reads churn. `lenient_lines` and `lenient_decoder` apply the
  same rule to a stream and to text inflated a chunk at a time.
- Output crapkit hands back whole (a directory git names, which must still
  open; a patch whose body bytes a mutant keeps; a .gitignore it appends to)
  goes through `exact_text`, which keeps each byte that is not UTF-8 as the
  lone surrogate Python gives a POSIX path. Text crapkit writes to a child's
  stdin goes through `child_input`.
- A pytest configuration file (pytest.ini, pyproject.toml, tox.ini, setup.cfg)
  reads through `pytest_config_text`, as pytest reads it: a BOM stays, so a
  file pytest refuses gives crapkit no testpaths either.
- A source file the scorer reads goes through `source_chars`, and one crapkit
  rewrites (a mutant) through `source_text` and back through `source_bytes`,
  which return every byte it held. Both take the same order of encodings;
  `analyze.decode_source` says why the order is fixed.
- Text the OS hands over (argv, the environment, a host name, a directory name)
  arrives as Python decodes it: a byte that is not UTF-8 is a lone surrogate on
  POSIX. `os_bytes` turns it back into the bytes the OS meant, for a hash or a
  lock key. `os_text` makes it text a store or a file can hold.

A path git names is another kind, and `gitpaths` owns it. The older sites that
still spell a decode policy (the advisory hook's reads of git's answer,
crapkit's own METADATA read, the console streams) are each named in
test_repo_text's guard with the seam that moves them, and a new one fails it.
"""
from __future__ import annotations

import codecs
import io
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from .errors import ConfigError

_UTF16 = ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"))
_C1 = "crapkit-c1"
_JSON_KINDS = {list: "an array", str: "a string", bool: "a boolean", type(None): "null"}


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
    return repo_bytes_text(path.read_bytes(), what)


def repo_bytes_text(data: bytes, what: str) -> str:
    """Decode already captured repository bytes under the same shell rules."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigError(_not_utf8(what, data, exc)) from None


def _not_utf8(what: str, data: bytes, exc: UnicodeDecodeError) -> str:
    """The refusal for a whole file. The decoder strips a BOM before it reads,
    so the offset it reports is moved back onto the file's own bytes."""
    offset = exc.start + (len(data) - len(exc.object))
    return _not_utf8_at(what, data[:2], offset, data[offset])


def _not_utf8_at(what: str, head: bytes, offset: int, byte: int) -> str:
    """The refusal, blaming UTF-16 when the first bytes are its mark and the
    offending byte otherwise."""
    if head in (b"\xff\xfe", b"\xfe\xff"):
        reason = f"first bytes {head.hex(' ')} = UTF-16, the PowerShell 5.1 Out-File default"
    else:
        reason = f"byte {byte:02x} at offset {offset}"
    return f"{what} is not UTF-8 ({reason}); save it as UTF-8"


class JsonStream:
    """JSON bytes crapkit did not write, decoded as they arrive by `repo_json`'s
    rule: a UTF-8 BOM read past, UTF-16 and a byte that is not UTF-8 refused
    naming `what` and the byte. A coverage artifact runs to hundreds of MB, so
    covstream feeds it a chunk at a time rather than decoding the whole file.

    The offset a refusal names is counted in the file's own bytes: the decoder
    reports it inside the bytes it still holds, which always end at the last
    byte fed."""

    def __init__(self, what: str) -> None:
        self._what = what
        self._decoder = codecs.getincrementaldecoder("utf-8-sig")()
        self._fed = 0
        self._head = b""

    def decode(self, data: bytes, final: bool = False) -> str:
        self._fed += len(data)
        self._head = (self._head + data)[:2]
        try:
            return self._decoder.decode(data, final)
        except UnicodeDecodeError as exc:
            offset = self._fed - len(exc.object) + exc.start
            raise ConfigError(_not_utf8_at(self._what, self._head, offset,
                                           exc.object[exc.start])) from None


def json_kind(value: object) -> str:
    """The JSON type a value arrived as, in the words a refusal prints: the
    type, not the value, since a long string or a list echoed back tells the
    reader less than the name of what it holds."""
    if isinstance(value, dict):
        return "an object"
    return _JSON_KINDS.get(type(value), "a number")


def repo_json(path: Path, what: str) -> dict:
    """The one JSON object a file holds, read by `repo_text`'s rule: a BOM read
    past, as npm and Claude Code read past it, and UTF-16 or a byte that is not
    UTF-8 refused by name.

    A file that does not parse, or parses to something other than an object, is
    refused too, naming `what` and the line where the parse stopped. init read
    such a package.json as an empty object, so a stray comma cost the js lane
    without a word. Every refusal is a ConfigError, so a caller that can go on
    without the file catches one type and prints its sentence.
    """
    try:
        value = json.loads(repo_text(path, what))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{what} is not valid JSON ({exc.msg} at line {exc.lineno} "
                          f"column {exc.colno}); fix that line") from None
    if not isinstance(value, dict):
        raise ConfigError(f"{what} holds {json_kind(value)}, not a JSON object; "
                          "save one object there")
    return value


def lenient(data: bytes) -> str:
    """UTF-8 with a leading BOM dropped and each undecodable byte as U+FFFD."""
    return data.decode("utf-8-sig", "replace")


def lenient_lines(stream: IO[bytes]) -> Iterator[str]:
    """`lenient` a line at a time, for output too long to hold: a BOM at the
    start dropped, each byte that is not UTF-8 as U+FFFD. A line ends at LF
    alone, so a CR inside an author name stays inside its line."""
    return io.TextIOWrapper(stream, encoding="utf-8-sig", errors="replace", newline="\n")


def lenient_decoder() -> codecs.IncrementalDecoder:
    """`lenient` for text that arrives a chunk at a time, such as inflated
    deflate, where a chunk can end inside a multi-byte character."""
    return codecs.getincrementaldecoder("utf-8-sig")("replace")


def exact_text(data: bytes) -> str:
    """Bytes as text that keeps every byte: UTF-8, each byte that is not UTF-8
    as the lone surrogate Python gives a POSIX path, so encoding it back with
    surrogateescape gives the same bytes."""
    return data.decode("utf-8", "surrogateescape")


def pytest_config_text(data: bytes) -> str:
    """A pytest configuration file as pytest reads it: UTF-8 with a leading BOM
    kept as U+FEFF. pytest refuses such a file (`unexpected line:
    '\\ufeff[pytest]'`), and configparser and tomllib refuse the same text, so
    crapkit takes no testpaths from a file pytest cannot run with. A byte that
    is not UTF-8 reads as U+FFFD."""
    return data.decode("utf-8", "replace")


def child_input(text: str) -> bytes:
    """Text crapkit writes to a child's stdin, as UTF-8 with each LF written as
    the OS line ending, as a text-mode pipe writes it. A lone surrogate an OS
    string carried in, a path or a reason in bytes that are not UTF-8, reads
    as U+FFFD instead of ending the command."""
    return os_text(text).replace("\n", os.linesep).encode("utf-8")


def utf16_marked(data: bytes) -> bool:
    """True when the bytes open with a UTF-16 byte-order mark: what Windows
    PowerShell 5.1's `Out-File` and `>` write, and what no UTF-8 reader reads."""
    return data.startswith(tuple(mark for mark, _ in _UTF16))


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


def source_chars(raw: bytes) -> str:
    """Source bytes as the scorer reads them: the characters alone, a BOM of
    either kind dropped, and each byte cp1252 leaves undefined read as U+FFFD."""
    if utf16_marked(raw):
        return source_text(raw).removeprefix("\ufeff")
    raw = raw.removeprefix(codecs.BOM_UTF8)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


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
