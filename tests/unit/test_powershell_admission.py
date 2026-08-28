"""PowerShell enters the file universe as a cc-only language, on a reader lizard lacks.

Two constants admit it — `SUPPORTED_LANGUAGES` and `LANGUAGE_EXTENSIONS` — and no
third. Pester names its test files `Foo.Tests.ps1` beside the source, which no
default exclude claims, and crapkit does not invent one: `**/*_test.go` was safe
because the Go toolchain enforces that suffix, while `Deploy.Tests.ps1` is a name
a repo is free to give production code. The escape hatch is the `[exclude]` table
a user already has, and the test below proves it reaches these files.

The measurement is the other half of admission, twice over. lizard answers
`(get_reader_for(filename) or CLikeReader)`, and CLikeReader finds C-shaped
functions in a .ps1 file, so an unregistered corpus reports plausible wrong
numbers. And lizard reads source with `io.open(path, 'r')`, letting the machine's
locale decide the encoding — Windows PowerShell writes cp1252 by default, so the
same file produced two different function names on two machines and the ratchet
keys on the name.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import crapkit
from crapkit.analyze import ANALYSIS_VERSION, analyze_one, analyze_source, decode_source
from crapkit.config import Config, Scope, load_config_text
from crapkit.hook import staged_records
from crapkit.scaffold import DEFAULT_EXCLUDES, sniff_scopes, source_candidates
from crapkit.universe import scan_files

SRC = str(Path(crapkit.__file__).resolve().parent.parent)

PS_SCOPE = Scope(name="scripts", paths=("scripts",), languages=("powershell",))

# base 1, + if, elseif, -and, foreach, and two switch arms (`default` is free).
DEPLOY = """function Invoke-Deploy {
    param([string]$Target, [switch]$Force)
    if (-not $Target -and -not $Force) {
        return 1
    } elseif ($Target -eq "all") {
        foreach ($host in $Hosts) {
            switch ($host.State) {
                'up'   { Write-Host $host }
                'down' { Write-Warning $host }
                default { }
            }
        }
    }
    return 0
}
"""
DEPLOY_CCN = 7

# `function Write-Café` in cp1252: one byte, 0xE9, that is not valid UTF-8. The
# name is the payload — the ratchet keys on path::long_name, so a decoder that
# drops the byte writes a different row than one that keeps it.
CP1252 = b'function Write-Caf\xe9 {\n  if ($x -eq "d\xe9j\xe0") { return 1 }\n  return 0\n}\n'
CP1252_NAME = "Write-Café"

# The same declaration written as UTF-8. Read by a cp1252 interpreter it used to
# report NO function at all: `é` arrives as `Ã` plus `©`, and the `©` is no word
# character, so `function Write-CafÃ` never reaches its brace.
UTF8 = CP1252.decode("cp1252").encode("utf-8")

# 0x81 is one of the five bytes cp1252 leaves undefined, so strict cp1252 raises
# on it too. Replacement is what keeps a file like this analyzable.
UNDEFINED_BYTE = b'function Write-Bad {\n  # \x81\n  if ($x) { return 1 }\n}\n'


# --- the config seam ----------------------------------------------------------

def test_a_scope_can_declare_powershell():
    cfg = load_config_text('[[scope]]\nname = "scripts"\npaths = ["scripts"]\n'
                           'languages = ["powershell"]\n')
    assert cfg.scopes[0].languages == ("powershell",)


def test_a_powershell_scope_can_be_coverage_optional():
    """cc-only is the whole shape: neither coverage parser reads a Pester run, and
    most PowerShell in a repo is operational glue nothing tests."""
    cfg = load_config_text('[[scope]]\nname = "scripts"\npaths = ["scripts"]\n'
                           'languages = ["powershell"]\ncoverage_optional = true\n')
    assert cfg.coverage_optional_scopes == frozenset({"scripts"})


# --- the file universe --------------------------------------------------------

def test_both_powershell_extensions_join_the_scope():
    uni = scan_files(["scripts/deploy.ps1", "scripts/Tools.psm1", "scripts/notes.md"],
                     Config(scopes=(PS_SCOPE,)))
    assert uni.by_scope == {"scripts": ["scripts/Tools.psm1", "scripts/deploy.ps1"]}


def test_no_default_exclude_claims_the_pester_test_spelling():
    """The decision, pinned so it reads as one. `**/*.test.*` does not match
    `.Tests.ps1`, and adding a glob that did would delete production files from
    every repo that names a script that way."""
    cfg = Config(scopes=(PS_SCOPE,), exclude_globs=DEFAULT_EXCLUDES)
    uni = scan_files(["scripts/Deploy.ps1", "scripts/Deploy.Tests.ps1"], cfg)

    assert uni.by_scope == {"scripts": ["scripts/Deploy.Tests.ps1", "scripts/Deploy.ps1"]}
    assert not [glob for glob in DEFAULT_EXCLUDES if glob.lower().endswith(".ps1")]


def test_a_repo_with_pester_excludes_its_suite_in_one_config_line():
    """What docs/configuration.md tells a Pester user to write. Exclude globs are
    matched against a lowered path, so one lowercase glob claims every casing."""
    cfg = Config(scopes=(PS_SCOPE,), exclude_globs=("**/*.tests.ps1",))
    uni = scan_files(["scripts/Deploy.ps1", "scripts/Deploy.Tests.ps1"], cfg)

    assert uni.by_scope == {"scripts": ["scripts/Deploy.ps1"]}


def test_init_sniffs_a_powershell_directory_as_a_powershell_scope():
    assert sniff_scopes(["scripts/deploy.ps1", "scripts/Tools.psm1"]) == {
        "scripts": ("powershell",)}


def test_init_proposes_powershell_sources():
    assert source_candidates(["scripts/deploy.ps1", "scripts/Tools.psm1"]) == [
        "scripts/deploy.ps1", "scripts/Tools.psm1"]


# --- the measurement ----------------------------------------------------------

def _child(code: str, **env_overrides) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([SRC, env.get("PYTHONPATH", "")])
    env.update(env_overrides)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=env, timeout=180)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _reader_after_importing_analyze(filename: str) -> str:
    """The reader a FRESH interpreter resolves once it has imported nothing but
    crapkit.analyze. In-process this proves nothing: importing the reader module
    registers it, and some other test already has. A pool worker imports
    crapkit.analyze and nothing else, so that is the import under test."""
    return _child("import lizard, crapkit.analyze\n"
                  f"print(lizard.get_reader_for({filename!r}).__name__)\n")


def test_importing_the_analysis_module_alone_registers_the_powershell_reader():
    assert _reader_after_importing_analyze("scripts/deploy.ps1") == "PowerShellReader"


def test_psm1_resolves_to_the_powershell_reader_too():
    assert _reader_after_importing_analyze("scripts/Tools.psm1") == "PowerShellReader"


def test_crapkit_reads_the_hand_counted_ccn_off_a_powershell_file():
    (record,) = analyze_source("scripts/deploy.ps1", DEPLOY)

    assert record.ccn == DEPLOY_CCN


def test_top_level_script_code_is_not_reported_as_a_function():
    """Statements outside any declaration belong to lizard's `*global*` pseudo
    function, exactly like Python module level and exactly like shell."""
    assert analyze_source("scripts/run.ps1",
                          "$ErrorActionPreference = 'Stop'\nGet-Process | Out-Null\n") == []


def test_analysis_version_invalidates_caches_written_before_powershell():
    """Cached `.ps1` records were measured by lizard's C reader at version 5, and
    every non-ASCII file at that version was decoded by the machine's locale. A
    cache keys on content plus this fingerprint, so 5 must not be reusable."""
    assert ANALYSIS_VERSION > 5


# --- decoding: by content, not by locale --------------------------------------

def _written(tmp_path: Path, name: str, raw: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(raw)
    return str(path)


def test_a_cp1252_source_file_keeps_the_characters_it_holds(tmp_path: Path):
    """lizard's own read raises on these bytes under a UTF-8 locale and answers
    with `utf8, 'ignore'`, which reports the function as `Write-Caf`."""
    _, records = analyze_one((_written(tmp_path, "a.ps1", CP1252), "a.ps1"))

    assert [r.long_name for r in records] == [CP1252_NAME]


def test_a_byte_cp1252_itself_leaves_undefined_does_not_break_the_analysis(tmp_path: Path):
    """0x81 has no cp1252 character, so a strict second decode would raise where
    the first one did and the file would score nothing. Replacement is why the
    fallback carries 'replace'."""
    _, records = analyze_one((_written(tmp_path, "b.ps1", UNDEFINED_BYTE), "b.ps1"))

    assert [(r.long_name, r.ccn) for r in records] == [("Write-Bad", 2)]


def test_a_utf8_source_file_still_declares_its_function(tmp_path: Path):
    """The other half of the locale defect, and the worse one. Read as cp1252 the
    UTF-8 bytes of `é` become `Ã` and `©`; `©` is no word character, so the
    declaration stops being one and a cp1252 machine reported this file as
    holding no function at all."""
    _, records = analyze_one((_written(tmp_path, "u.ps1", UTF8), "u.ps1"))

    assert [r.long_name for r in records] == [CP1252_NAME]


@pytest.mark.parametrize(("name", "raw"), [("cp1252", CP1252), ("utf8", UTF8)])
def test_the_same_file_scores_the_same_whatever_the_interpreter_locale(
        tmp_path: Path, name: str, raw: bytes):
    """The defect this replaces, measured across all four cells. `io.open(path,
    'r')` takes the machine's encoding, so one function written two ways and read
    two ways produced three different answers: no function, `Write-Café` and
    `Write-Caf`. The ratchet keys on path::long_name, so a Windows developer and
    a UTF-8 CI wrote rows neither could match. UTF-8 mode is exactly that
    difference, forced on and off in two children."""
    path = _written(tmp_path, f"{name}.ps1", raw)
    # `ascii()`, because the child's own stdout encoding follows the very mode
    # under test: printing the name itself would compare two pipes, not two
    # analyses.
    code = ("import crapkit.analyze as a\n"
            f"print(ascii([r.long_name for r in a.analyze_one(({path!r}, 'c.ps1'))[1]]))\n")

    utf8_mode = _child(code, PYTHONUTF8="1")
    locale_mode = _child(code, PYTHONUTF8="0")

    assert utf8_mode == locale_mode == ascii([CP1252_NAME])


def test_a_utf8_bom_is_stripped_rather_than_read_as_part_of_the_first_token(tmp_path: Path):
    """Three of the 106 real PowerShell files carry one. Left in place it joins
    the `function` keyword and no declaration is found at all."""
    _, records = analyze_one(
        (_written(tmp_path, "d.ps1", b"\xef\xbb\xbf" + DEPLOY.encode()), "d.ps1"))

    assert [r.ccn for r in records] == [DEPLOY_CCN]


def test_crlf_line_endings_score_exactly_like_lf(tmp_path: Path):
    """lizard read source in text mode, which translated line endings; reading
    bytes does not. Every line number and NLOC in every cache written before this
    change assumes the translation, so `read_source` keeps it."""
    lf = analyze_one((_written(tmp_path, "lf.ps1", DEPLOY.encode()), "x.ps1"))[1]
    crlf = analyze_one(
        (_written(tmp_path, "crlf.ps1", DEPLOY.replace("\n", "\r\n").encode()), "x.ps1"))[1]

    assert crlf == lf


def test_the_precommit_gate_decodes_a_staged_blob_the_same_way(tmp_path: Path):
    """The hook never writes the blob down below its pool threshold, so it had a
    decoder of its own that mirrored lizard's. Two decoders is how the gate ends
    up judging different content than the inventory scored."""
    on_disk = analyze_one((_written(tmp_path, "e.ps1", CP1252), "scripts/e.ps1"))[1]

    assert staged_records({"scripts/e.ps1": CP1252}) == {"scripts/e.ps1": on_disk}


def test_decode_source_is_the_one_rule_both_paths_use():
    assert decode_source(CP1252).startswith("function " + CP1252_NAME)
