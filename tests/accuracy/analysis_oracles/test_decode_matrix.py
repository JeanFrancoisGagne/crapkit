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
- self-diff: hook-precommit decodes a staged blob in memory for a small commit
  and reads it back from a temp tree for a large one; both gate the same
  functions for every byte variant, and (hand) every function of a new file.
  A lone CR is a line end to the reader and not to git's diff: two strict
  xfails (calc-bug analysis-oracles-150).
No crapkit import.
"""
from __future__ import annotations

import ast
import codecs
from pathlib import Path
import re

import pytest

from accuracy.analysis_oracles import analysis_inventory
from accuracy.kit import drive, repos, rulings

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


# --- self-diff: the two reads of a staged blob in hook-precommit ---------------------------------

# hook-precommit reads staged blobs (src/crapkit/hook.py staged_records): below
# 16 files it decodes each blob in memory; at 16 or more it writes them to a
# temp tree and reads the files back. Every byte variant is staged both ways.
# Every function holds one if, so ccn 2 (NIST SP 500-235 sec. 4.1) is over a
# target of 1, and a new file's diff adds every line, so the gate names every
# function: (name, start, ccn) read off the text.
HOOK_TEXTS = {"py": ("def café(n):\n    if n:\n        return 1\n    return 2\n\n\n"
                     "def second(m):\n    if m:\n        return m\n    return 0\n"),
              "ps1": ("function Write-Café($n) {\n    if ($n) {\n        return 1\n    }\n"
                      "    return 2\n}\n\nfunction Get-Second($m) {\n    if ($m) {\n"
                      "        return $m\n    }\n    return 0\n}\n")}
HOOK_HAND = {"py": [("café", 1, 2), ("second", 7, 2)],
             "ps1": [("Write-Café", 1, 2), ("Get-Second", 8, 2)]}
HOOK_FILES = {f"{lang}/{name}.{lang}": data for lang, text in HOOK_TEXTS.items()
              for name, data in _variants(text).items()}
HOOK_CONFIG = ('[crapkit]\ntarget = 1\n\n[[scope]]\nname = "all"\npaths = ["."]\n'
               'languages = ["python", "powershell"]\ncoverage_optional = true\n')
# The gate's lines: `  ccn   2  py/utf8-lf.py:7  second( m )`.
GATED = re.compile(r"^ +ccn +(\d+) +(\S+):(\d+) +(.+)$", re.M)
# git numbers lines at LF only; a lone CR ends a line for the reader (above).
CR_ONLY = "utf8-cr"


def _gated(stdout: str) -> dict[str, list[tuple]]:
    found: dict[str, list[tuple]] = {}
    for ccn, path, start, name in GATED.findall(stdout):
        found.setdefault(path, []).append((analysis_inventory.bare(name), int(start), int(ccn)))
    return {path: sorted(rows, key=lambda row: row[1]) for path, rows in found.items()}


def _staged(root: Path, paths: tuple, spawn: bool = False) -> tuple[int, dict]:
    """hook-precommit's exit code and gated rows with only `paths` staged."""
    repos.git(root, "reset", "-q")
    repos.git(root, "add", *paths)
    done = drive.Driver(root, spawn=spawn).run("hook-precommit")
    return done.code, _gated(done.stdout)


def _gate(root: Path, paths: tuple, spawn: bool = False) -> dict[str, list[tuple]]:
    code, gated = _staged(root, paths, spawn)
    assert code == 6, gated
    return gated


# `x = 1` sits after a lone CR: git line 1, reader and Python line 2. The edit is
# on `target`'s def line, git line 8, reader line 9.
LONE_CR = (b"# note\rx = 1\n" b"def first(a):\n    if a:\n        return 1\n    return 2\n\n\n"
           b"def target(b):\n    if b:\n        return 1\n    return 2\n")


@pytest.fixture(scope="module")
def hook_arms(tmp_path_factory):
    """{arm: {path: gated rows}}. All 16 files staged take the temp tree, spawned,
    since that arm can reach the analysis pool; each language's 8 alone take
    the in-memory blob. Then an edit to the committed m.py, staged alone."""
    root = analysis_inventory.build({"crapkit.toml": HOOK_CONFIG, "m.py": LONE_CR},
                                    tmp_path_factory.mktemp("hook") / "repo")
    for path, data in HOOK_FILES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    arms = {"temp tree": _gate(root, ("py", "ps1"), spawn=True),
            "blob": {**_gate(root, ("py",)), **_gate(root, ("ps1",))}}
    (root / "m.py").write_bytes(LONE_CR.replace(b"def target(b):", b"def target(b, c=0):"))
    return {**arms, "lone CR edit": _staged(root, ("m.py",))[1]}


@pytest.mark.parametrize("lang", sorted(HOOK_TEXTS))
@pytest.mark.parametrize("variant", VARIANTS)
def test_the_staged_blob_and_the_temp_tree_gate_the_same_functions(hook_arms, lang, variant):
    path = f"{lang}/{variant}.{lang}"
    assert hook_arms["blob"].get(path) == hook_arms["temp tree"].get(path)


@pytest.mark.parametrize("arm", ["blob", "temp tree"])
@pytest.mark.parametrize("lang", sorted(HOOK_TEXTS))
@pytest.mark.parametrize("variant", [variant for variant in VARIANTS if variant != CR_ONLY])
def test_the_hook_gates_every_function_of_a_new_file(hook_arms, arm, lang, variant):
    assert hook_arms[arm].get(f"{lang}/{variant}.{lang}") == HOOK_HAND[lang]


@rulings.applies("AO-HOOK-CR-ONLY")
@pytest.mark.parametrize("lang", sorted(HOOK_TEXTS))
def test_a_new_cr_only_file_gates_every_function(hook_arms, lang):
    gated = hook_arms["blob"].get(f"{lang}/{CR_ONLY}.{lang}", [])
    rulings.pin_ruling("AO-HOOK-CR-ONLY", crapkit=len(gated), oracle=len(HOOK_HAND[lang]))


@rulings.applies("AO-HOOK-LONE-CR-SHIFT")
def test_an_edit_below_a_lone_cr_gates_the_edited_function(hook_arms):
    gated = [row[0] for row in hook_arms["lone CR edit"].get("m.py", [])]
    rulings.pin_ruling("AO-HOOK-LONE-CR-SHIFT", crapkit=",".join(gated) or "none",
                       oracle="target")
