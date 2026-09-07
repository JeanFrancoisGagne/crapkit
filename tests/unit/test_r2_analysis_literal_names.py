"""A filename controls reader selection without changing source identity."""
import hashlib
import os

import pytest

from crapkit import analyze


@pytest.mark.parametrize("suffix,source", [
    ("py", "def f(x):\n    if x: return 1\n    return 0\n"),
    ("ts", "function f(x: number) { return x ? 1 : 0; }"),
    ("rs", "fn f(x: bool) -> i32 { if x { 1 } else { 0 } }"),
    ("ps1", "function Get-Value($x) { if ($x) { return 1 }; return 0 }"),
    ("sh", "f() { if true; then echo 1; else echo 0; fi; }"),
])
@pytest.mark.parametrize("name", ["src/a\nb", "src\nfolder/a", "src/a\\b", "src/a\u2028b"])
def test_literal_names_keep_the_reader_and_every_returned_path(suffix, source, name, monkeypatch):
    ordinary = "src/ordinary." + suffix
    path = name + "." + suffix
    expected = [row._replace(path=path) for row in analyze.analyze_source(ordinary, source)]
    assert expected
    assert analyze.analyze_source(path, source) == expected
    reads = []

    def read(actual):
        reads.append(actual)
        return source

    monkeypatch.setattr(analyze.lizard, "auto_read", read)
    assert analyze.analyze_one((path, path)) == (path, expected)
    assert reads == [path]


@pytest.mark.skipif(os.name == "nt", reason="literal LF filesystem names require POSIX")
def test_an_old_wrong_reader_cache_cannot_hide_a_newline_named_file(tmp_path):
    path = "a\nb.py"
    source = b"def f(x):\n    return x\n"
    (tmp_path / path).write_bytes(source)
    old_key = analyze._analysis_key("fallback.c", hashlib.sha256(source).hexdigest())
    cache = {"fp": analyze.fingerprint(), "entries": {old_key: []}}

    rows, hits, fresh = analyze.analyze_files(tmp_path, [path], cache=cache, workers=1)

    assert hits == 0
    assert [(row.path, row.long_name) for row in rows[path]] == [(path, "f( x )")]
    warm, hits, _ = analyze.analyze_files(tmp_path, [path], cache=fresh, workers=1)
    assert hits == 1
    assert warm == rows
