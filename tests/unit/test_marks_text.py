"""The marks file's one read rule, and the write-back that keeps its encoding.

The marks file is a TSV a person or a shell may save: PowerShell 5.1's bare
`Out-File` writes UTF-16 behind a byte-order mark, `Out-File -Encoding utf8`
puts a UTF-8 BOM in front, and a cp1252 editor saves `é` as the one byte 0xE9.
Today's copy, every past revision `ratchet report` reads and the three sides git
hands the merge driver read by one rule: UTF-16 when a byte-order mark says so,
else UTF-8 with a BOM dropped, and each byte neither reads as U+FFFD.
"""
import codecs

import pytest

from crapkit.textcodec import marks_bytes, marks_codec, marks_text, unreadable_byte

ROWS = "# crapkit-analysis=11 lizard=1.24.0\npath\tlong_name\tcrap\nsrc/app.py\tcafé( n )\t9.0000\n"

READS = [
    # id, the bytes on disk, the text the rule reads, the first byte it replaced
    ("utf8", ROWS.encode(), ROWS, None),
    ("utf8-bom", codecs.BOM_UTF8 + ROWS.encode(), ROWS, None),
    ("utf16-le-bom", codecs.BOM_UTF16_LE + ROWS.encode("utf-16-le"), ROWS, None),
    ("utf16-be-bom", codecs.BOM_UTF16_BE + ROWS.encode("utf-16-be"), ROWS, None),
    ("utf16-le-crlf", codecs.BOM_UTF16_LE + ROWS.replace("\n", "\r\n").encode("utf-16-le"),
     ROWS.replace("\n", "\r\n"), None),
    ("crlf", ROWS.replace("\n", "\r\n").encode(), ROWS.replace("\n", "\r\n"), None),
    ("nul", ROWS.encode() + b"# \x00\n", ROWS + "# \x00\n", None),
    ("cjk-emoji", "src/日本.py\tf\U0001f680( )\t9.0\n".encode(), "src/日本.py\tf\U0001f680( )\t9.0\n", None),
    ("cp1252-name", ROWS.encode("cp1252"), ROWS.replace("é", "\ufffd"), "byte e9 at offset 70"),
    ("cp1252-comment-after-bom", codecs.BOM_UTF8 + b"# caf\xe9\n", "# caf\ufffd\n", "byte e9 at offset 8"),
    ("utf16-odd-length", codecs.BOM_UTF16_LE + "a\n".encode("utf-16-le") + b"x", "a\n\ufffd",
     "byte 78 at offset 6"),
    ("utf16-lone-surrogate", codecs.BOM_UTF16_LE + b"a\x00\x00\xd8b\x00", "a\ufffdb", "byte 00 at offset 4"),
    ("a-bom-that-is-not-utf16", b"\xfe\xfe" + ROWS.encode(), "\ufffd\ufffd" + ROWS, "byte fe at offset 0"),
]


@pytest.mark.parametrize("raw, text, replaced", [row[1:] for row in READS], ids=[row[0] for row in READS])
def test_a_marks_file_reads_by_its_own_mark_else_utf8_with_replacement(raw, text, replaced):
    assert marks_text(raw) == text
    assert unreadable_byte(raw) == replaced


@pytest.mark.parametrize("raw, codec", [
    (ROWS.encode(), "utf-8"),
    (codecs.BOM_UTF8 + ROWS.encode(), "utf-8"),
    (codecs.BOM_UTF16_LE + ROWS.encode("utf-16-le"), "utf-16-le"),
    (codecs.BOM_UTF16_BE + ROWS.encode("utf-16-be"), "utf-16-be"),
    (b"", "utf-8"),
], ids=["utf8", "utf8-bom", "utf16-le", "utf16-be", "empty"])
def test_the_mark_names_the_codec(raw, codec):
    assert marks_codec(raw) == codec


@pytest.mark.parametrize("like", [
    codecs.BOM_UTF16_LE + ROWS.encode("utf-16-le"),
    codecs.BOM_UTF16_BE + ROWS.encode("utf-16-be"),
], ids=["utf16-le", "utf16-be"])
def test_a_utf16_file_is_written_back_as_utf16_behind_its_own_mark(like):
    """A rewrite in UTF-8 would hand PowerShell 5.1, which wrote the file, a
    file it reads as cp1252 on its next save."""
    assert marks_bytes(ROWS, marks_codec(like)) == like


@pytest.mark.parametrize("like", [b"", ROWS.encode(), codecs.BOM_UTF8 + ROWS.encode()],
                         ids=["new-or-empty", "utf8", "utf8-bom"])
def test_every_other_file_is_written_as_utf8_without_a_bom(like):
    assert marks_bytes(ROWS, marks_codec(like)) == ROWS.encode()


def test_the_ratchet_page_quotes_the_refusal_a_rewrite_gives(tmp_path):
    """docs/ratchet.md shows the line seed prints over a cp1252 marks file."""
    import re
    from pathlib import Path

    from crapkit.errors import ConfigError
    from crapkit.ratchet import RatchetEntry
    from crapkit.ratchetfile import RatchetFile

    page = (Path(__file__).resolve().parents[2] / "docs" / "ratchet.md").read_text(encoding="utf-8")
    quoted = re.search(r"crapkit: (crapkit-ratchet\.tsv holds byte e9 at offset (\d+), [^\n]*)", page)
    marks = tmp_path / "crapkit-ratchet.tsv"
    marks.write_bytes(b"#" * int(quoted[2]) + b"\xe9\n")

    with pytest.raises(ConfigError) as refused:
        RatchetFile.read(marks).kept([RatchetEntry("src/a.py", "f( )", 9.0)])

    assert str(refused.value) == quoted[1]
