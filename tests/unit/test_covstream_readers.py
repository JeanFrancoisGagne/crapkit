"""covstream's two pieces for the line and XML coverage formats: HashingReader,
which hashes every byte a consumer reads, and lines(), which splits a
machine-written line format under repotext's JSON decode rule.

The digest oracle is hashlib's sha256 of the file's own bytes. The read size
is a parameter so a read boundary lands inside a CRLF pair and inside a
multi-byte character.
"""
import codecs
import hashlib
import io
import tracemalloc
import xml.etree.ElementTree as ET

import pytest

from crapkit import covstream
from crapkit.errors import ToolError

MIB = 1 << 20


def _reader(tmp_path, data: bytes, name: str = "lcov.info"):
    path = tmp_path / name
    path.write_bytes(data)
    return covstream.HashingReader(path.open("rb"))


@pytest.mark.parametrize("size", [1, 7, 4 * MIB])
@pytest.mark.parametrize("reads", ["all", "part", "none"])
def test_digest_is_the_whole_file_whatever_the_consumer_read(tmp_path, size, reads):
    data = bytes(range(256)) * ((2 * size + 5) // 256 + 40)
    reader = _reader(tmp_path, data)
    taken = {"all": lambda: iter(lambda: reader.read(size), b""),
             "part": lambda: [reader.read(size)],
             "none": lambda: []}[reads]()

    consumed = b"".join(taken)

    assert data.startswith(consumed)
    assert len(consumed) == {"all": len(data), "part": size, "none": 0}[reads]
    assert reader.hexdigest() == hashlib.sha256(data).hexdigest()
    assert reader.hexdigest() == hashlib.sha256(data).hexdigest()


def test_iterparse_reads_xml_through_the_reader(tmp_path):
    data = b'<?xml version="1.0"?>\n<coverage><package name="p"/><package name="q"/></coverage>\n'
    reader = _reader(tmp_path, data, "coverage.xml")

    names = [el.get("name") for _, el in ET.iterparse(reader) if el.tag == "package"]

    assert reader.readable()
    assert names == ["p", "q"]
    assert reader.hexdigest() == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, covstream.CHUNK])
@pytest.mark.parametrize("data, expected", [
    (b"SF:a.py\nDA:1,1\n", ["SF:a.py", "DA:1,1"]),
    (b"SF:a.py\r\nDA:1,1\r\n", ["SF:a.py", "DA:1,1"]),
    (b"SF:a.py\nDA:1,1", ["SF:a.py", "DA:1,1"]),
    (codecs.BOM_UTF8 + b"SF:a.py\nDA:1,1\n", ["SF:a.py", "DA:1,1"]),
    (b"mode: set\n\nend\r\n", ["mode: set", "", "end"]),
    (b"a\rb\n", ["a\rb"]),
], ids=["lf", "crlf", "no-final-newline", "utf8-bom", "blank-line", "lone-cr-is-not-an-end"])
def test_lines_end_at_lf_and_crlf_and_read_past_a_bom(tmp_path, chunk, data, expected):
    reader = _reader(tmp_path, data)

    assert list(covstream.lines(reader, "lcov.info", chunk)) == expected
    assert reader.hexdigest() == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("data, cut, expected", [
    (b"ab\r\ncd\n", 3, ["ab", "cd"]),
    ("aé\nb\n".encode("utf-8"), 2, ["aé", "b"]),
    ("a€\nb\n".encode("utf-8"), 2, ["a€", "b"]),
    ("a€\nb\n".encode("utf-8"), 3, ["a€", "b"]),
    ("a\U0001f3af\nb\n".encode("utf-8"), 3, ["a\U0001f3af", "b"]),
], ids=["crlf", "two-byte", "three-byte-after-1", "three-byte-after-2", "four-byte"])
def test_a_read_boundary_inside_crlf_or_a_character_splits_nothing(tmp_path, data, cut, expected):
    """`cut` is the read size, so the first read ends inside the pair or the
    character."""
    reader = _reader(tmp_path, data)

    assert list(covstream.lines(reader, "lcov.info", cut)) == expected


def test_an_empty_file_yields_no_line_and_the_digest_of_nothing(tmp_path):
    reader = _reader(tmp_path, b"")

    assert list(covstream.lines(reader, "lcov.info")) == []
    assert reader.hexdigest() == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("chunk", [2, covstream.CHUNK])
def test_utf16_is_refused_by_name_with_the_powershell_fix(tmp_path, chunk):
    data = codecs.BOM_UTF16_LE + "SF:a.py\n".encode("utf-16-le")
    reader = _reader(tmp_path, data)

    with pytest.raises(ToolError) as refused:
        list(covstream.lines(reader, "lcov.info", chunk))

    assert refused.value.exit_code == 5
    assert str(refused.value) == (
        "lcov.info is not UTF-8 (first bytes ff fe = UTF-16, the PowerShell 5.1 Out-File "
        "default); save it as UTF-8: write the file with Out-File -Encoding utf8, or with "
        "the tool's own output-path flag")
    assert refused.value.__cause__ is None


@pytest.mark.parametrize("chunk", [1, 3, covstream.CHUNK])
def test_a_byte_that_is_not_utf8_is_refused_naming_the_file_and_offset(tmp_path, chunk):
    data = codecs.BOM_UTF8 + b"SF:a.py\nSF:\xe9.py\n"
    reader = _reader(tmp_path, data, "cover.out")

    with pytest.raises(ToolError) as refused:
        list(covstream.lines(reader, "cover.out", chunk))

    assert refused.value.exit_code == 5
    assert str(refused.value) == "cover.out is not UTF-8 (byte e9 at offset 14); save it as UTF-8"
    assert refused.value.__cause__ is None


class _Synthetic(io.RawIOBase):
    """`size` bytes of whole lines, made as they are read, so the file never
    sits on the heap or the disk."""

    def __init__(self, size: int):
        line = b"DA:" + b"7" * 93 + b",1\r\n"
        self._block = line * (covstream.CHUNK // len(line))
        self._left = size

    def read(self, size: int = -1) -> bytes:
        take = min(size, len(self._block), self._left)
        self._left -= take
        return self._block[:take]


def test_a_200_mb_line_file_reads_inside_covstreams_memory_bound():
    reader = covstream.HashingReader(_Synthetic(200 * 1000 * 1000))
    tracemalloc.start()
    try:
        count = sum(1 for _ in covstream.lines(reader, "lcov.info"))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert count == 200 * 1000 * 1000 // 100
    assert peak < 32 * 1000 * 1000
