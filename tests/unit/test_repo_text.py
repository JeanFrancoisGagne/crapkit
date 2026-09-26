"""One reader for the text files the repository owns: crapkit.toml, the marks
file, a portable baseline.

PowerShell 5.1 writes those files two ways crapkit did not read: `Out-File
-Encoding utf8` puts a BOM in front, and a bare `Out-File` writes UTF-16 LE. The
strict UTF-8 reads turned the first into `does not parse` / `line 1 has 1
fields` and the second into a raw UnicodeDecodeError traceback at exit 1, a code
crapkit's exit table does not define.

The reader lives in a core module, `crapkit.repotext`, because two of its
callers cannot import `cli._shared`: the advisory hook (its module scope opens
the snapshot store) and `override` (core never imports the CLI). The CLI name
is the same function, not a copy.
"""
import codecs
import fnmatch
import io
import json
import os
import re
from pathlib import Path

import pytest

import crapkit
from crapkit import repotext
from crapkit.cli import _shared
from crapkit.cli._shared import repo_text
from crapkit.errors import ConfigError

TEXT = "[crapkit]\ntarget = 6\n"
SRC = Path(crapkit.__file__).resolve().parent
# The BOM rule in every spelling a reader has used: the codec name, the
# byte-order mark as bytes, and the mark as a character, escaped or literal. A
# lenient reader that dropped the mark with removeprefix passed a guard that
# looked for the codec name alone.
_DECODE = re.compile("[\"']utf[-_]8[-_]sig[\"']|BOM_UTF8|\\\\ufeff|\ufeff")


def test_the_cli_name_is_the_core_reader_not_a_copy():
    assert _shared.repo_text is repotext.repo_text


def test_the_utf8_sig_decode_is_written_once():
    """One module, zero inline copies. A second decode is a second place where
    a UTF-16 file is a traceback instead of the sentence: the hook's config
    read, the override's marks read and the merge driver each had one, and
    the lenient reader for git's text, doctor's plugin read and the scorer's
    source read each later dropped the mark by hand."""
    homes = sorted(p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
                   if _DECODE.search(p.read_text(encoding="utf-8")))

    assert homes == ["repotext.py"], homes


def test_plain_utf8_reads_as_written(tmp_path):
    path = tmp_path / "crapkit.toml"
    path.write_bytes(TEXT.encode("utf-8"))

    assert repo_text(path, "crapkit.toml") == TEXT


def test_a_bom_is_dropped_so_the_file_reads_as_the_same_text(tmp_path):
    path = tmp_path / "crapkit.toml"
    path.write_bytes(TEXT.encode("utf-8-sig"))

    assert repo_text(path, "crapkit.toml") == TEXT


@pytest.mark.parametrize("encoding, head", [("utf-16-le", "ff fe"), ("utf-16-be", "fe ff")])
def test_utf16_is_a_configuration_error_naming_the_bytes_and_the_fix(tmp_path, encoding, head):
    """The mark spelled out, because Python's `utf-16` codec writes the host's
    byte order and `utf-16-be` writes no mark at all."""
    path = tmp_path / "crapkit-ratchet.tsv"
    path.write_bytes(bytes.fromhex(head) + TEXT.encode(encoding))

    with pytest.raises(ConfigError) as refused:
        repo_text(path, "crapkit-ratchet.tsv")

    assert str(refused.value) == (
        f"crapkit-ratchet.tsv is not UTF-8 (first bytes {head} = UTF-16, the PowerShell 5.1 "
        "Out-File default); save it as UTF-8")
    assert refused.value.exit_code == 3


def test_a_stray_byte_is_named_by_its_offset(tmp_path):
    """A latin-1 save is not UTF-16, so the message says where the read broke
    instead of blaming a BOM that is not there."""
    path = tmp_path / "crapkit.toml"
    path.write_bytes(b"[crapkit]\n# caf\xe9\ntarget = 6\n")

    with pytest.raises(ConfigError) as refused:
        repo_text(path, "crapkit.toml")

    assert str(refused.value) == \
        "crapkit.toml is not UTF-8 (byte e9 at offset 15); save it as UTF-8"


# --- the JSON kind ----------------------------------------------------------------

PACKAGE = {"name": "demo", "scripts": {"test": "vitest run"}}


@pytest.mark.parametrize("data", [json.dumps(PACKAGE).encode(),
                                  codecs.BOM_UTF8 + json.dumps(PACKAGE).encode(),
                                  json.dumps(PACKAGE, indent=2).replace("\n", "\r\n").encode(),
                                  json.dumps({**PACKAGE, "author": "René"},
                                             ensure_ascii=False).encode()],
                         ids=["plain", "utf8-bom", "crlf", "utf8-accent"])
def test_a_json_object_reads_as_npm_reads_it(tmp_path, data):
    path = tmp_path / "package.json"
    path.write_bytes(data)

    assert {k: v for k, v in repotext.repo_json(path, "package.json").items()
            if k in PACKAGE} == PACKAGE


@pytest.mark.parametrize("data, sentence", [
    (b"\xff\xfe" + json.dumps(PACKAGE).encode("utf-16-le"),
     "package.json is not UTF-8 (first bytes ff fe = UTF-16, the PowerShell 5.1 Out-File "
     "default); save it as UTF-8"),
    (b'{"author": "Ren\xe9"}', "package.json is not UTF-8 (byte e9 at offset 15); save it as UTF-8"),
    (b'{ not json', "package.json is not valid JSON (Expecting property name enclosed in double "
                    "quotes at line 1 column 3); fix that line"),
    # json words this one differently from Python 3.13 on, so the parser's own
    # words are left out; the file, the line, the column and the fix are not.
    (b'{"scripts": {"test": "x"},}\n', "package.json is not valid JSON (* at line 1 column 2?); "
                                       "fix that line"),
    (b"", "package.json is not valid JSON (Expecting value at line 1 column 1); fix that line"),
    (b"[]", "package.json holds an array, not a JSON object; save one object there"),
    (b'"demo"', "package.json holds a string, not a JSON object; save one object there"),
    (b"null", "package.json holds null, not a JSON object; save one object there"),
    (b"7", "package.json holds a number, not a JSON object; save one object there"),
    (b"true", "package.json holds a boolean, not a JSON object; save one object there"),
], ids=["utf16", "latin1-byte", "not-json", "trailing-comma", "empty-file", "an-array",
        "a-string", "null", "a-number", "true"])
def test_json_that_is_not_one_readable_object_is_refused_naming_the_file(tmp_path, data,
                                                                          sentence):
    """Each refusal names the file and the fix, at exit 3. package.json read
    each of these as an empty object, and UTF-16 as no file at all."""
    path = tmp_path / "package.json"
    path.write_bytes(data)

    with pytest.raises(ConfigError) as refused:
        repotext.repo_json(path, "package.json")

    assert fnmatch.fnmatchcase(str(refused.value), sentence), str(refused.value)
    assert refused.value.exit_code == 3


# --- the kinds a stream, a chunked read, a child's stdin and a path take -------

def test_lenient_lines_drops_a_leading_bom_and_ends_a_line_at_lf_alone():
    """git's log streamed a line at a time reads as `lenient` reads it whole:
    the mark dropped, a stray byte as U+FFFD, and a CR inside an author name
    left inside its line."""
    stream = io.BytesIO(codecs.BOM_UTF8 + b"Ren\xe9\rX\nnext\n")

    lines = list(repotext.lenient_lines(stream))

    assert lines == ["Ren\ufffd\rX\n", "next\n"]
    assert "".join(lines) == repotext.lenient(codecs.BOM_UTF8 + b"Ren\xe9\rX\nnext\n")


@pytest.mark.parametrize("cut", range(1, 12))
def test_lenient_decoder_reads_a_character_a_chunk_cut_in_two(cut):
    data = codecs.BOM_UTF8 + "\u6e21\u8fba\n".encode("utf-8") + b"caf\xe9\n"
    decoder = repotext.lenient_decoder()

    text = decoder.decode(data[:cut]) + decoder.decode(data[cut:], final=True)

    assert text == repotext.lenient(data) == "\u6e21\u8fba\ncaf\ufffd\n"


@pytest.mark.parametrize("data", [b"src/caf\xe9.py", "src/caf\u00e9.py".encode("utf-8"),
                                  b"\xff\xfe", b""],
                         ids=["latin-1-byte", "utf-8-accent", "utf-16-mark", "empty"])
def test_escaped_gives_back_every_byte(data):
    assert repotext.escaped(data).encode("utf-8", "surrogateescape") == data


@pytest.mark.parametrize("text, sent", [("caf\u00e9", "caf\u00e9".encode("utf-8")),
                                        ("a\nb\n", b"a" + os.linesep.encode() + b"b"
                                         + os.linesep.encode()), ("", b"")],
                         ids=["accent", "lf-as-the-os-line-ending", "empty"])
def test_child_input_is_the_text_as_utf8_as_a_text_mode_pipe_wrote_it(text, sent):
    """0.8.0 handed the alert text to a text-mode pipe, which wrote CRLF on
    Windows. The bytes are the same now that crapkit encodes them itself."""
    assert repotext.child_input(text) == sent


def test_child_input_reads_a_lone_surrogate_as_a_replacement_character():
    """An override reason or a function name in bytes that are not UTF-8
    reaches the alert command as U+FFFD, never as an encode error."""
    sent = repotext.child_input("caf\udce9")

    assert sent.startswith(b"caf") and "\ufffd" in sent.decode("utf-8")


def test_plain_utf8_keeps_a_bom_as_pytest_and_node_do():
    assert repotext.plain_utf8(codecs.BOM_UTF8 + b"[pytest]\ncaf\xe9\n") == \
        "\ufeff[pytest]\ncaf\ufffd\n"
