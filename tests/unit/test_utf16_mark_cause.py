"""Each UTF-16 message names the byte order its mark announces.

PowerShell 5.1's `Out-File` and `>` write UTF-16 little-endian behind ff fe.
A file behind fe ff is big-endian, which Out-File never writes by default, so
blaming Out-File for it sends the reader after the wrong tool. The JSON
refusal, the coverage JSON stream's refusal, init's .gitignore line and
doctor's source note take their words from `repotext.utf16_cause`.
"""
import codecs

import pytest

from crapkit import repotext
from crapkit.cli import admin
from crapkit.errors import ConfigError

MARKS = [
    # id, the mark, the words every UTF-16 message uses for it
    ("le", codecs.BOM_UTF16_LE, "the PowerShell 5.1 Out-File default"),
    ("be", codecs.BOM_UTF16_BE, "big-endian"),
]
BODY = '{"files": {}}'


def _utf16(mark: bytes) -> bytes:
    return mark + BODY.encode("utf-16-le" if mark == codecs.BOM_UTF16_LE else "utf-16-be")


@pytest.mark.parametrize("mark, cause", [row[1:] for row in MARKS], ids=[row[0] for row in MARKS])
def test_the_json_refusal_names_the_byte_order_of_its_mark(tmp_path, mark, cause):
    path = tmp_path / "package.json"
    path.write_bytes(_utf16(mark))

    with pytest.raises(ConfigError) as refused:
        repotext.repo_json(path, "package.json")

    assert str(refused.value) == (f"package.json is not UTF-8 (first bytes {mark.hex(' ')} = "
                                  f"UTF-16, {cause}); save it as UTF-8")


@pytest.mark.parametrize("mark, cause", [row[1:] for row in MARKS], ids=[row[0] for row in MARKS])
def test_the_coverage_json_stream_names_the_byte_order_of_its_mark(mark, cause):
    """covstream feeds the artifact a chunk at a time, and the first chunk
    holds the mark."""
    stream = repotext.JsonStream("cov.json")

    with pytest.raises(ConfigError) as refused:
        stream.decode(_utf16(mark), final=True)

    assert str(refused.value) == (f"cov.json is not UTF-8 (first bytes {mark.hex(' ')} = "
                                  f"UTF-16, {cause}); save it as UTF-8")


def test_doctor_names_each_mark_in_a_note_of_its_own(tmp_path):
    """One note per byte order, so a big-endian file is never listed under
    Out-File's default."""
    src = tmp_path / "src"
    src.mkdir()
    for name, mark in (("le.ps1", codecs.BOM_UTF16_LE), ("be.ps1", codecs.BOM_UTF16_BE)):
        (src / name).write_bytes(_utf16(mark))
    (src / "plain.ps1").write_bytes(BODY.encode())
    tail = ("crapkit scores them, but git diffs them as binary; save them as UTF-8 "
            "(PowerShell: Set-Content -Encoding utf8) to diff them as text")

    notes = admin._doctor_utf16_sources(tmp_path, {"ps": ["src/le.ps1", "src/be.ps1", "src/plain.ps1"]})

    assert [(note.level, note.text) for note in notes] == [
        ("note", "1 source file(s) open with a UTF-16 byte-order mark, the PowerShell 5.1 "
                 f"Out-File default: src/le.ps1. {tail}"),
        ("note", f"1 source file(s) open with a UTF-16 byte-order mark, big-endian: src/be.ps1. {tail}"),
    ]


def test_doctor_reads_a_source_it_cannot_open_as_unmarked(tmp_path):
    assert admin._doctor_utf16_sources(tmp_path, {"ps": ["src/gone.ps1"]}) == []
