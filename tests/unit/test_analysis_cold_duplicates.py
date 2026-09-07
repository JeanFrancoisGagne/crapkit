"""Cold files share parsing only when bytes and reader semantics agree."""
from unittest.mock import patch

import pytest

from crapkit import analyze


@pytest.mark.parametrize("source", ["", "def f(x):\n    return 1 if x else 0\n"])
def test_cold_copies_parse_once_and_keep_every_path(tmp_path, source):
    paths = ["z.py", "a.py", "middle.py"]
    for path in paths:
        (tmp_path / path).write_text(source, encoding="utf-8")
    expected = {path: analyze.analyze_source(path, source) for path in sorted(paths)}
    parser = analyze.lizard.FileAnalyzer.analyze_source_code
    with patch.object(analyze.lizard.FileAnalyzer, "analyze_source_code", autospec=True,
                      side_effect=parser) as calls:
        records, hits, cache = analyze.analyze_files(tmp_path, paths, cache={}, workers=1)
    assert records == expected
    assert list(records) == sorted(paths)
    assert hits == 0
    assert calls.call_count == 1
    assert len(cache["entries"]) == 1
    warm, hits, _ = analyze.analyze_files(tmp_path, paths, cache=cache)
    assert warm == expected
    assert hits == 3


def test_equal_bytes_keep_distinct_reader_and_type_modes(tmp_path):
    source = "function f() {\n    return 0\n}\n"
    paths = ["a.sh", "b.ps1", "c.js", "d.ts", "e.tsx", "f.jsx"]
    for path in paths:
        (tmp_path / path).write_text(source, encoding="utf-8")
    expected = {path: analyze.analyze_source(path, source) for path in paths}
    records, hits, cache = analyze.analyze_files(tmp_path, paths, cache={}, workers=1)
    assert hits == 0
    assert records == expected
    assert len(cache["entries"]) >= 4
    assert records["a.sh"][0].long_name == "f()"
    assert records["b.ps1"][0].long_name == "f"
