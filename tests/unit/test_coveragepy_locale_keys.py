"""A coverage.py key written under a POSIX locale that is not UTF-8.

crapkit restarts itself in UTF-8 mode under such a locale, and a lane's own
Python keeps the locale, as ruled. Its coverage.py names `pkg/café.py` in the
locale's encoding, so the report keys the file as `pkg/cafÃ©.py`, the UTF-8
bytes read as Latin-1. The key matched no row, and the file's functions read as
untested where the UTF-8 locale reads them measured (py314-help boundary-7). The
adapter now reads such a key back through the locale's codec, when the file it
then names exists and the key as written does not.
"""
import json

import pytest

from crapkit import coverage_py
from crapkit.config import Lane

LANE = Lane(name="py", command="python -m pytest", artifact="cov.json", parser="coveragepy",
            scopes=("pkg",), inputs=())


def _function(start: int) -> dict:
    return {"start_line": start, "executed_lines": [start, start + 1], "missing_lines": [start + 2],
            "summary": {"covered_lines": 2, "num_statements": 3, "num_branches": 0, "covered_branches": 0}}


def _report(tmp_path, keys: list[str]):
    files = {key: {"functions": {"f": _function(1)}, "missing_lines": [3],
                   "contexts": {"1": ["tests/test_a.py::test_f"]}} for key in keys}
    path = tmp_path / "cov.json"
    path.write_text(json.dumps({"meta": {"branch_coverage": True}, "files": files}), encoding="utf-8")
    return path


def _tree(tmp_path, names: list[str]):
    for name in names:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("def f():\n    return 1\n", encoding="utf-8")


# id, the child's codec, the file on disk, the key its coverage.py wrote, the key crapkit reads
KEYS = [
    ("latin1-accent", "iso8859-1", "pkg/café.py", "pkg/cafÃ©.py", "pkg/café.py"),
    ("latin1-cjk", "iso8859-1", "pkg/日本.py", "pkg/æ\x97¥æ\x9c¬.py", "pkg/日本.py"),
    ("latin1-emoji", "iso8859-1", "pkg/\U0001f680.py", "pkg/ð\x9f\x9a\x80.py", "pkg/\U0001f680.py"),
    ("latin9-accent", "iso8859-15", "pkg/café.py", "pkg/cafÃ©.py", "pkg/café.py"),
    ("koi8r-accent", "koi8_r", "pkg/café.py", "pkg/cafц╘.py", "pkg/café.py"),
    ("shift-jis-undecodable-byte", "shift_jis", "pkg/À.py", "pkg/ﾃ\udc80.py", "pkg/À.py"),
    ("utf8-key-as-written", "iso8859-1", "pkg/café.py", "pkg/café.py", "pkg/café.py"),
    ("ascii-key", "iso8859-1", "pkg/cafe.py", "pkg/cafe.py", "pkg/cafe.py"),
    ("a-real-file-named-with-the-mojibake", "iso8859-1", "pkg/cafÃ©.py", "pkg/cafÃ©.py", "pkg/cafÃ©.py"),
    ("no-file-behind-either-spelling", "iso8859-1", "pkg/other.py", "pkg/cafÃ©.py", "pkg/cafÃ©.py"),
    ("utf8-locale-reads-as-written", None, "pkg/café.py", "pkg/cafÃ©.py", "pkg/cafÃ©.py"),
]


@pytest.mark.parametrize("codec, on_disk, written, read", [row[1:] for row in KEYS], ids=[row[0] for row in KEYS])
def test_a_key_is_read_as_the_file_the_lane_child_named(tmp_path, monkeypatch, codec, on_disk, written, read):
    monkeypatch.setattr(coverage_py, "_child_codec", lambda: codec)
    _tree(tmp_path, [on_disk])
    artifact = _report(tmp_path, [written])

    per_file, dead, _ = coverage_py.read(LANE, tmp_path, artifact)

    assert list(per_file) == [read] and list(dead) == [read]
    assert list(coverage_py.missing(LANE, tmp_path, artifact)) == [read]
    assert coverage_py.contexts(LANE, tmp_path, artifact, on_disk) == (
        {1: ["tests/test_a.py::test_f"]} if read == on_disk else {})


def test_the_codec_is_utf8_where_the_os_names_files_in_utf8(monkeypatch):
    monkeypatch.setattr(coverage_py.sys, "platform", "darwin")

    assert coverage_py._child_codec() is None


@pytest.mark.parametrize("encoding, codec", [("ISO-8859-1", "iso8859-1"), ("UTF-8", None),
                                             ("ANSI_X3.4-1968", None)],
                         ids=["latin1", "utf8", "c-locale-coerced-to-utf8"])
def test_the_codec_is_the_locale_encoding_on_linux(monkeypatch, encoding, codec):
    monkeypatch.setattr(coverage_py.sys, "platform", "linux")
    monkeypatch.setattr(coverage_py.os, "name", "posix")
    monkeypatch.setattr(coverage_py.locale, "getencoding", lambda: encoding)

    assert coverage_py._child_codec() == codec
