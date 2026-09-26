"""Every rule for how bytes crapkit did not write become text, and OS text
becomes bytes. No other module spells a decode policy: each reader names its
kind here, and tests/unit/test_decode_guard.py fails on one that does not.

Stdlib and `errors` only, on purpose. The advisory hook, whose module scope
must never import the snapshot store, `lanes` and `override`, core modules that
import no CLI family, and the MCP stdio loop all read through this module. Each
of them once carried its own copy of a decode, and each copy was one more place
where a UTF-16 file was a traceback instead of a sentence.

- A file the repository owns and crapkit must read exactly (crapkit.toml, a
  portable baseline, each package.json init reads) is UTF-8, a byte-order mark
  read past, or refused with a sentence naming the byte: `repo_text`.
- JSON a repository wrote (each package.json init reads) reads by the same rule
  and must hold one JSON object, or is refused naming the file: `repo_json`. A
  coverage artifact, read a chunk at a time, takes the same rule through
  `JsonStream`, and `json_kind` names the type a value arrived as, for every
  refusal that prints one.
- The marks file, today's copy, every past revision and each side git hands the
  merge driver, goes through `marks_text`: UTF-16 when a byte-order mark says
  so, else UTF-8 with a BOM dropped, and each byte neither reads as U+FFFD. A
  command that rewrites the file asks `unreadable_byte` first and writes it
  back through `marks_bytes`.
- Text crapkit only reads, ranks or passes along (git's free text such as an
  author name or a commit message, a runner's output, an MCP frame, a
  .gitignore's lines, which git reads past a BOM) goes through `lenient`, or
  `lenient_lines` and `lenient_decoder` when it arrives in pieces: a UTF-8 BOM
  is dropped and each byte that is not UTF-8 reads as U+FFFD. git, Node and a
  browser read such bytes the same way, and one of them in an author name
  inside the churn window used to stop every command that reads churn. A
  child's stdin is written by `child_input`. The Action's comment builder
  reads the files its steps wrote this way too, and cuts the comment for
  GitHub's byte limit with `utf8_head`.
- A file whose own reader decodes UTF-8 and knows no byte-order mark (a
  plugin's manifest, hooks file and installed_plugins.json, which Claude Code
  reads with Node; a pytest.ini, tox.ini, setup.cfg or pyproject.toml, which
  pytest reads) goes through `plain_utf8`: U+FFFD for each byte that is not
  UTF-8, and a BOM kept, which JSON.parse and pytest then refuse, and so does
  crapkit. What a child printed, which a refusal quotes, reads the same way, as
  a UTF-8 text-mode pipe reads it (`plaintext.printed_text`).
- A patch or a path name git hands over, which crapkit must hand back byte for
  byte, goes through `escaped`: each byte that is not UTF-8 is a lone
  surrogate, and encoding with surrogateescape gives the byte back. `shown`
  prints such a name through `backslashed`, each such byte as `\\xNN`.
- A source file the scorer reads goes through `source_chars`, and one crapkit
  rewrites (a mutant) through `source_text` and back through `source_bytes`,
  which return every byte it held. Both take the same order of encodings;
  `analyze.decode_source` says why the order is fixed.
- Text the OS hands over (argv, the environment, a host name, a directory name)
  arrives as Python decodes it: a byte that is not UTF-8 is a lone surrogate on
  POSIX. `os_bytes` turns it back into the bytes the OS meant, for a hash or a
  lock key. `os_text` makes it text a store or a file can hold. A lane's child
  under a POSIX locale that is not UTF-8 names files in that locale's codec:
  `utf8_spelling` reads such a name back as the UTF-8 name it spells.

`gitpaths` frames a path git names (C quoting, NUL and tab records) and decodes
it through `escaped`.
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
# How every message names the UTF-16 a mark announces. PowerShell 5.1's
# `Out-File` and `>` write the little-endian mark; nothing it does by default
# writes the big-endian one.
_UTF16_CAUSES = {codecs.BOM_UTF16_LE: "the PowerShell 5.1 Out-File default",
                 codecs.BOM_UTF16_BE: "big-endian"}
_C1 = "crapkit-c1"
_JSON_KINDS = {list: "an array", str: "a string", bool: "a boolean", type(None): "null"}


# --- a file the repository owns ---------------------------------------------


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
    refusal prints: `crapkit.toml`, a baseline's path, a package.json's path.
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
    if utf16_marked(head):
        reason = f"first bytes {head.hex(' ')} = UTF-16, {utf16_cause(head)}"
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
    past, as npm reads past it, and UTF-16 or a byte that is not UTF-8 refused
    by name.

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


# --- the marks file -----------------------------------------------------------


def utf16_marked(data: bytes) -> bool:
    """True when the bytes open with a UTF-16 byte-order mark, which no UTF-8
    reader reads: ff fe is what Windows PowerShell 5.1's `Out-File` and `>`
    write, and fe ff is big-endian."""
    return data.startswith(tuple(mark for mark, _ in _UTF16))


def utf16_cause(data: bytes) -> str:
    """The words a message names a UTF-16 mark with, for bytes `utf16_marked`
    accepts: Out-File's default for ff fe, big-endian for fe ff."""
    return _UTF16_CAUSES[data[:2]]


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


# --- text crapkit only reads, ranks or passes along ------------------------------


def lenient(data: bytes) -> str:
    """UTF-8 with a leading BOM dropped and each undecodable byte as U+FFFD."""
    return data.decode("utf-8-sig", "replace")


def lenient_lines(stream: IO[bytes]) -> Iterator[str]:
    """`lenient` a line at a time, for output too long to hold: a BOM at the
    start dropped, each byte that is not UTF-8 as U+FFFD. A line ends at LF
    alone: universal newlines also ended one at a CR inside an author name,
    which cut a log header off its dates."""
    return io.TextIOWrapper(stream, encoding="utf-8-sig", errors="replace", newline="\n")


def lenient_decoder() -> codecs.IncrementalDecoder:
    """`lenient` for text that arrives a chunk at a time, such as inflated
    deflate, where a chunk can end inside a multi-byte character."""
    return codecs.getincrementaldecoder("utf-8-sig")("replace")


def child_input(text: str) -> bytes:
    """Text crapkit writes to a child's stdin (the alert command's line and
    digest), as UTF-8 with LF line ends on every OS. A text-mode pipe wrote CR
    LF on Windows, so an alert log fed by `cat >>` held both. A lone surrogate
    an OS string carried in, a path or a reason in bytes that are not UTF-8,
    reads as U+FFFD instead of ending the command."""
    return os_text(text).encode("utf-8")


def utf8_head(text: str, size: int) -> str:
    """The longest start of `text` whose UTF-8 bytes fit in `size`, for a
    receiver that counts bytes (GitHub takes a comment of 65,536). A character
    the cut would split is left out whole, never sent as U+FFFD."""
    return text.encode("utf-8")[:size].decode("utf-8", "ignore")


def plain_utf8(data: bytes) -> str:
    """Bytes as a UTF-8 reader that knows no byte-order mark reads them: each
    byte that is not UTF-8 as U+FFFD, and a BOM kept as the character U+FEFF.

    For a file whose own reader works that way, so crapkit refuses what it
    refuses: Claude Code reads a plugin's JSON with Node's `readFileSync(path,
    "utf8")`, and JSON.parse refuses the mark as `claude plugin validate`
    2.1.238 does; pytest 8.3 refuses a pytest.ini or pyproject.toml that opens
    with one (`unexpected line: '\\ufeff[pytest]'`), and configparser and
    tomllib refuse the same text, so crapkit reads no testpaths from it
    either."""
    return data.decode("utf-8", "replace")


# --- a patch or a name crapkit hands back byte for byte -----------------------------


def escaped(data: bytes) -> str:
    """UTF-8 with each byte that is not UTF-8 as a lone surrogate, so encoding
    the text with surrogateescape gives these bytes back: git's own patch and
    path bytes, matched against a scope and handed back to git, and a
    directory git names, which must still open."""
    return data.decode("utf-8", "surrogateescape")


def backslashed(data: bytes) -> str:
    """UTF-8 for printing, each byte that is not UTF-8 as `\\xNN`, so a name
    that cannot be read still names its file on any console."""
    return data.decode("utf-8", "backslashreplace")


# --- source files --------------------------------------------------------------


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


# The five bytes cp1252 leaves undefined, each as the letter U+01NN, so an
# identifier holding one stays one token (analyze.decode_source says why).
_UNDEFINED_AS_LETTERS = {byte: 0x100 + byte for byte in (0x81, 0x8D, 0x8F, 0x90, 0x9D)}


def source_chars(raw: bytes) -> str:
    """Source bytes as the scorer reads them: the characters alone, a BOM of
    either kind dropped, and each byte cp1252 leaves undefined as a letter."""
    if utf16_marked(raw):
        return source_text(raw).removeprefix("\ufeff")
    raw = raw.removeprefix(codecs.BOM_UTF8)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return source_text(raw).translate(_UNDEFINED_AS_LETTERS)


def _c1(error: UnicodeError) -> tuple[str | bytes, int]:
    """cp1252 has no character for 0x81, 0x8d, 0x8f, 0x90 or 0x9d: each reads
    as U+0081 and the like, and writes back as its byte."""
    span = error.object[error.start:error.end]
    if isinstance(error, UnicodeDecodeError):
        return "".join(map(chr, span)), error.end
    return bytes(map(ord, span)), error.end


codecs.register_error(_C1, _c1)


# --- text the OS hands over ----------------------------------------------------


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


def utf8_spelling(name: str, codec: str) -> str | None:
    """The UTF-8 name whose bytes a child under `codec` spelled as `name`, or
    None when those bytes are not UTF-8."""
    try:
        return name.encode(codec, "surrogateescape").decode("utf-8")
    except UnicodeError:
        return None
