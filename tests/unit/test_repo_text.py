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
_DECODE = re.compile(r"[\"']utf-8-sig[\"']")


def test_the_cli_name_is_the_core_reader_not_a_copy():
    assert _shared.repo_text is repotext.repo_text


def test_the_utf8_sig_decode_is_written_once():
    """One reader, zero inline copies. A second decode is a second place where
    a UTF-16 file is a traceback instead of the sentence: the hook's config
    read, the override's marks read and the merge driver each had one."""
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
