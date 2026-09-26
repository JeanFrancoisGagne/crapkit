"""Source bytes crapkit did not write: scored as the file declares, rewritten exactly.

Three readers, one order of encodings (UTF-16 by its byte-order mark, then
UTF-8, then cp1252):

- the scorer (`analyze.decode_source`) read a UTF-16 file as NUL-separated
  cp1252 and found no function in it, so inventory scored nothing, the
  pre-commit gate passed a ccn-8 function it refuses in UTF-8, and the advisory
  hook said nothing;
- `mutate` read a cp1252 file as UTF-8 with replacement and wrote it back as
  UTF-8, so every accented byte outside the mutated line changed and a suite
  that checked one killed every mutant for a reason no mutant caused;
- `brief --json`'s `source` read UTF-8 with replacement, where the long_name
  beside it read the cp1252 `é` the file holds.
"""
import codecs
import sys
import types

import pytest

from raw_git import checkout, commit, repository, stage

from crapkit import mutate_pool, repotext
from crapkit.analyze import analyze_one, decode_source
from crapkit.cli._shared import _load_sources
from crapkit.cli.analyses import _file_mutants
from crapkit.cli.claude_hook import _records
from crapkit.cli.parser import main
from crapkit.hook import staged_records

PS1 = 'function Get-Café {\n  if ($a) { 1 } elseif ($b) { 2 } else { 3 }\n}\n'
PY = 'def café(x):\n    if x:\n        return 1\n    return 2\n'
UTF16 = [
    # id, the encoder a file was written with
    ("utf16-le-bom", lambda text: codecs.BOM_UTF16_LE + text.encode("utf-16-le")),
    ("utf16-be-bom", lambda text: codecs.BOM_UTF16_BE + text.encode("utf-16-be")),
]
READABLE = UTF16 + [
    ("utf8", lambda text: text.encode()),
    ("utf8-bom", lambda text: codecs.BOM_UTF8 + text.encode()),
    ("cp1252", lambda text: text.encode("cp1252")),
    ("crlf", lambda text: text.replace("\n", "\r\n").encode()),
]


@pytest.mark.parametrize("encode", [row[1] for row in READABLE], ids=[row[0] for row in READABLE])
@pytest.mark.parametrize("name, text", [("a.ps1", PS1), ("a.py", PY)], ids=["powershell", "python"])
def test_a_source_file_scores_the_same_functions_in_every_encoding_it_declares(tmp_path, encode, name, text):
    path = tmp_path / name
    path.write_bytes(text.encode())
    utf8 = analyze_one((str(path), name))[1]
    path.write_bytes(encode(text))

    assert analyze_one((str(path), name))[1] == utf8
    assert staged_records({name: encode(text)}) == {name: utf8}
    assert len(utf8) == 1


TANGLED = "def tangled(a):\n" + "".join(f"    if a == {i}:\n        a += 1\n" for i in range(7)) + "    return a\n"
CONFIG = b'[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'


@pytest.mark.parametrize("encode", [row[1] for row in UTF16], ids=[row[0] for row in UTF16])
def test_the_precommit_gate_refuses_a_ccn8_function_staged_as_utf16(tmp_path, capsys, encode):
    root = repository(tmp_path)
    commit(root, files={b"crapkit.toml": CONFIG, b"src/app.py": b"x = 1\n"})
    checkout(root)
    stage(root, b"src/big.py", encode(TANGLED))

    assert main(["hook-precommit", "--repo", str(root)]) == 6
    assert "tangled( a )" in capsys.readouterr().out


UNDEFINED = [
    # id, a declaration whose name holds the byte, the name each byte scores as
    ("powershell", "a.ps1", b"function Write-Caf%s {\n  param($x)\n  if ($x) { return 1 }\n  return 2\n}\n",
     "Write-Caf%s"),
    ("python", "a.py", b"def caf%s(x):\n    if x:\n        return 1\n    return 2\n", "caf%s( x )"),
    ("typescript", "a.ts", b"export function caf%s(x: number) {\n  if (x) { return 1; }\n  return 2;\n}\n",
     "caf%s ( x )"),
    ("c", "a.c", b"int caf%s(int x) {\n  if (x) { return 1; }\n  return 2;\n}\n", "caf%s( int x)"),
    ("go", "a.go", b"package a\nfunc caf%s(x int) int {\n  if x > 0 { return 1 }\n  return 2\n}\n",
     "caf%s x int"),
    ("shell", "a.sh", b"caf%s() {\n  if [ \"$1\" ]; then echo 1; fi\n}\n", "caf%s()"),
]
STAND_INS = {0x81: "\u0181", 0x8D: "\u018d", 0x8F: "\u018f", 0x90: "\u0190", 0x9D: "\u019d"}


@pytest.mark.parametrize("byte", STAND_INS, ids=[f"{b:02x}" for b in STAND_INS])
@pytest.mark.parametrize("name, source, scored", [row[1:] for row in UNDEFINED],
                         ids=[row[0] for row in UNDEFINED])
def test_a_byte_cp1252_leaves_undefined_keeps_its_identifier_whole(tmp_path, byte, name, source, scored):
    """cp932, cp936 and cp1251 sources hold these bytes, and read as U+FFFD the
    identifier split in two: PowerShell, Go and shell dropped the function, and
    Python, TypeScript and C named it `\\ufffd`, `(anonymous)` or `if`. Each
    reads as the letter U+01NN, NN the byte, which no cp1252 file can spell."""
    raw = source % bytes([byte])
    path = tmp_path / name
    path.write_bytes(raw)

    records = analyze_one((str(path), name))[1]

    assert [(r.long_name, r.ccn) for r in records] == [(scored % STAND_INS[byte], 2)]
    assert staged_records({name: raw}) == {name: records}


@pytest.mark.parametrize("raw, read", [
    (b"# caf\xe9 \x81\n", "# café \u0181\n"),
    ("# caf\u0081\n".encode(), "# caf\u0081\n"),
], ids=["cp1252-file", "utf8-file-keeps-its-c1-control"])
def test_only_a_cp1252_read_turns_the_five_bytes_into_letters(raw, read):
    """A UTF-8 file that spells U+0081 holds that character, not a byte to stand in for."""
    assert decode_source(raw) == read


def test_a_cached_record_from_before_the_letters_is_read_again(tmp_path):
    """A cache=7 record holds a file with such a byte as no function, or as one
    named by half its identifier; read warm, it would keep hiding the function."""
    from crapkit.analyze import analyze_files, fingerprint

    (tmp_path / "a.ps1").write_bytes(UNDEFINED[0][2] % b"\x81")
    _, _, old = analyze_files(tmp_path, ["a.ps1"], cache={})
    old["fp"] = fingerprint().rsplit(";cache=", 1)[0] + ";cache=7"

    records, hits, _ = analyze_files(tmp_path, ["a.ps1"], cache=old)

    assert hits == 0
    assert [r.long_name for r in records["a.ps1"]] == ["Write-CafƁ"]


@pytest.mark.parametrize("encode", [row[1] for row in UTF16], ids=[row[0] for row in UTF16])
def test_the_advisory_hook_reads_an_edited_utf16_file(tmp_path, encode):
    (tmp_path / "big.py").write_bytes(encode(TANGLED))

    _, records = _records(tmp_path / "big.py", "big.py")

    assert [(r.long_name, r.ccn) for r in records] == [("tangled( a )", 8)]


# --- the bytes a mutant is written back into ---------------------------------------

ROUND_TRIP = [
    # id, source bytes
    ("utf8", "NAME = 'café 渡辺 \U0001f680'\n".encode()),
    ("utf8-bom", codecs.BOM_UTF8 + b"NAME = 'x'\n"),
    ("cp1252", b"NAME = 'caf\xe9 \x92 \x85'\n"),
    ("cp1252-undefined-bytes", b"# \x81\x8d\x8f\x90\x9d\nNAME = 'x'\n"),
    ("latin1-c1-range", bytes(range(0x80, 0x100)) + b"\n"),
    ("utf16-le-bom", codecs.BOM_UTF16_LE + "NAME = 'café'\n".encode("utf-16-le")),
    ("utf16-be-bom", codecs.BOM_UTF16_BE + "NAME = 'café'\n".encode("utf-16-be")),
    ("crlf-and-nul", b"NAME = 'x'\r\n# \x00\r\n"),
]


@pytest.mark.parametrize("raw", [row[1] for row in ROUND_TRIP], ids=[row[0] for row in ROUND_TRIP])
def test_source_text_and_source_bytes_return_every_byte(raw):
    assert repotext.source_bytes(repotext.source_text(raw), raw) == raw


MODULE = "{cookie}NAME = 'caf{e}'\n\n\ndef positive(x):\n    return x > 0\n"
CHECK = ("import pathlib, shutil, sys\nsys.path.insert(0, '.')\nimport m\n"
         "shutil.copyfile('m.py', 'seen.bin')\nsys.exit(0 if m.NAME == 'caf\\xe9' else 1)\n")
MUTATED = [
    # id, the module's bytes: a constant only the suite checks, and an untested function
    ("source-cp1252-coding-cookie", MODULE.format(cookie="# -*- coding: cp1252 -*-\n", e="é").encode("cp1252")),
    ("source-latin1-coding-cookie", MODULE.format(cookie="# -*- coding: latin-1 -*-\n", e="é").encode("latin-1")),
    ("source-utf8-control", MODULE.format(cookie="", e="é").encode()),
    ("source-utf8-bom", codecs.BOM_UTF8 + MODULE.format(cookie="", e="é").encode()),
    ("source-utf8-crlf", MODULE.format(cookie="", e="é").replace("\n", "\r\n").encode()),
]


@pytest.mark.parametrize("raw", [row[1] for row in MUTATED], ids=[row[0] for row in MUTATED])
def test_a_mutant_changes_its_own_line_and_no_other_byte(tmp_path, raw):
    """The suite checks NAME only, so a mutant of `positive` survives, and the
    file it ran against differs from the original in the mutated line alone."""
    (tmp_path / "m.py").write_bytes(raw)
    (tmp_path / "check.py").write_text(CHECK, encoding="utf-8")
    cfg = types.SimpleNamespace(mutation_command=f'"{sys.executable}" check.py',
                                mutation_timeout_seconds=120)
    mutant = next(m for m in _file_mutants(tmp_path, "m.py", None) if "x > 0" in m.original)

    assert mutate_pool.run_one(tmp_path, cfg, mutant) is mutate_pool.MutantVerdict.SURVIVED
    seen = (tmp_path / "seen.bin").read_bytes().split(b"\n")
    before = raw.split(b"\n")
    assert [i for i, (a, b) in enumerate(zip(before, seen)) if a != b] == [mutant.line - 1]
    assert (tmp_path / "m.py").read_bytes() == raw


def test_brief_source_holds_the_characters_a_cp1252_file_holds(tmp_path):
    (tmp_path / "a.ps1").write_bytes(PS1.encode("cp1252"))

    assert _load_sources(tmp_path, {"a.ps1"}) == {"a.ps1": decode_source(PS1.encode("cp1252"))}
    assert "Get-Café" in _load_sources(tmp_path, {"a.ps1"})["a.ps1"]
