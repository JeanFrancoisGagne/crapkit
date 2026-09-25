"""Source decoding and line normalization: one text written as different bytes
reads as the same functions.

The rule is docs/configuration.md "Source text is read as UTF-8, then cp1252 as
a fallback", with Python's universal newlines (io docs: `\n`, `\r` and
`\r\n` each end a line) and a leading UTF-8 byte order mark read as a
signature, not text (Unicode Standard sec. 23.8; Python's utf-8-sig codec).

- hand: the functions of each text, written down from the text below.
- model: `decoded()` applies the rule with Python codecs; ast (Python) or the
  PowerShell reference's `function Name(...)` form then lists the functions.
- metamorphic: every byte variant gives the rows of the UTF-8 LF variant.
- interpreter locale: PYTHONUTF8=0 (cp1252 on Windows) and PYTHONUTF8=1 read
  the same rows (R16: `function Write-Café` read three ways before the fix).
No crapkit import.
"""
from __future__ import annotations

import ast
import codecs
import re

import pytest

from accuracy.analysis_oracles import analysis_inventory

pytestmark = pytest.mark.process

PY_TEXT = ("def café(n):\n    if n:\n        return 1\n    return 2\n\n\n"
           "def second(m):\n    return m\n")
PS_TEXT = ("function Write-Café($n) {\n    if ($n) {\n        return 1\n    }\n"
           "    return 2\n}\n")
SOURCE = "hand, read off the text: a function spans its defining line to its last body line"
# (name, start, end, ccn_std) per text. ccn: NIST SP 500-235 sec. 4.1, 1 + one if.
HAND = {"py": [("café", 1, 4, 2), ("second", 7, 8, 1)],
        "ps1": [("Write-Café", 1, 6, 2)]}


def _variants(text: str) -> dict[str, bytes]:
    utf8, cp1252 = text.encode("utf-8"), text.encode("cp1252")
    return {"utf8-lf": utf8,
            "utf8-crlf": utf8.replace(b"\n", b"\r\n"),
            "utf8-cr": utf8.replace(b"\n", b"\r"),
            "utf8-bom": codecs.BOM_UTF8 + utf8,
            "utf8-bom-crlf": codecs.BOM_UTF8 + utf8.replace(b"\n", b"\r\n"),
            "cp1252-lf": cp1252,
            "cp1252-crlf": cp1252.replace(b"\n", b"\r\n"),
            # 0x81 is invalid UTF-8 and unassigned in cp1252: replaced, the rest kept
            "cp1252-unassigned-byte": cp1252 + b"# \x81\n"}


VARIANTS = sorted(_variants(PY_TEXT))
FILES = {f"{lang}/{name}.{lang}": data
         for lang, text in (("py", PY_TEXT), ("ps1", PS_TEXT))
         for name, data in _variants(text).items()}


def decoded(raw: bytes) -> str:
    """The documented rule, written from the docs and the codecs, not from crapkit."""
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8):]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")
    return "\n".join(re.split(r"\r\n|\r|\n", text))


def model_functions(lang: str, raw: bytes) -> list[tuple]:
    text = decoded(raw)
    return _python_functions(text) if lang == "py" else _powershell_functions(text)


def _python_functions(text: str) -> list[tuple]:
    return [(node.name, node.lineno, node.end_lineno) for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.FunctionDef)]


def _powershell_functions(text: str) -> list[tuple]:
    """`function Name(...) {` opens a function; the next line holding only `}` closes it
    (every probe body indents its inner braces)."""
    lines = text.split("\n")
    found = [(re.match(r"function ([\w-]+)\(", line), number)
             for number, line in enumerate(lines, 1)]
    return [(match.group(1), number, _closing_line(lines, number)) for match, number in found
            if match]


def _closing_line(lines: list[str], start: int) -> int:
    return next(number for number, line in enumerate(lines, 1) if number > start and line == "}")


@pytest.fixture(scope="module", params=["0", "1"], ids=["PYTHONUTF8=0", "PYTHONUTF8=1"])
def measured(request, tmp_path_factory):
    work = tmp_path_factory.mktemp(f"decode-utf8-{request.param}")
    done = analysis_inventory.measure(FILES, work, spawn=True, env={"PYTHONUTF8": request.param})
    assert done.code == 0, done.stderr
    return done


def _rows(measured, lang: str, variant: str) -> list[dict]:
    return measured.in_file(f"{lang}/{variant}.{lang}")


def _spans(rows: list[dict]) -> list[tuple]:
    return [(analysis_inventory.bare(row["long_name"]), row["start"], row["end"]) for row in rows]


@pytest.mark.parametrize("lang", sorted(HAND))
@pytest.mark.parametrize("variant", VARIANTS)
def test_every_variant_reads_the_hand_rows(measured, lang, variant):
    rows = _rows(measured, lang, variant)
    assert [(*span, row["ccn_std"]) for span, row in zip(_spans(rows), rows)] == HAND[lang], SOURCE


@pytest.mark.parametrize("lang", sorted(HAND))
@pytest.mark.parametrize("variant", VARIANTS)
def test_crapkit_matches_the_decoding_model(measured, lang, variant):
    raw = FILES[f"{lang}/{variant}.{lang}"]
    assert _spans(_rows(measured, lang, variant)) == model_functions(lang, raw)


def _unplaced(rows: list[dict]) -> list[dict]:
    return [{key: value for key, value in row.items() if key != "path"} for row in rows]


@pytest.mark.parametrize("lang", sorted(HAND))
@pytest.mark.parametrize("variant", VARIANTS[1:])
def test_byte_variants_read_like_utf8_lf(measured, lang, variant):
    expected = _unplaced(_rows(measured, lang, "utf8-lf"))
    assert _unplaced(_rows(measured, lang, variant)) == expected


@pytest.mark.parametrize("lang", sorted(HAND))
def test_non_ascii_names_equal_under_cp1252_and_utf8(measured, lang):
    names = {variant: [span[0] for span in _spans(_rows(measured, lang, variant))]
             for variant in ("utf8-lf", "cp1252-lf")}
    assert names == {variant: [row[0] for row in HAND[lang]] for variant in names}


def test_the_model_reads_its_own_hand_rows():
    for lang, text in (("py", PY_TEXT), ("ps1", PS_TEXT)):
        assert [row[:3] for row in HAND[lang]] == model_functions(lang, text.encode("utf-8"))
