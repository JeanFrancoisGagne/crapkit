"""Cold files share parsing only when bytes and reader semantics agree."""
from unittest.mock import patch

import pytest

from crapkit import analyze
from crapkit.merge import UnanalyzableFile


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


# A Python signature the file ends inside, and a TypeScript arrow the reader
# refuses: the two ways a reader turns a file down.
REFUSED = {".py": "def cut(a,\n        b",
           ".ts": "const f = [(x: number) => x < 2, (y: number) => y];\n"}


def _named(err: str) -> list[str]:
    """The refusal reasons a run printed, one per line under its count."""
    return [line.removeprefix("crapkit:   ") for line in err.splitlines()
            if line.startswith("crapkit:   ")]


@pytest.mark.parametrize("suffix", sorted(REFUSED))
def test_refused_copies_are_each_counted_and_named(tmp_path, capsys, suffix):
    """A refusal names the path it was read under, so two refused files are two
    refusals on stderr whatever their bytes, and neither is cached."""
    paths = [f"cut0{suffix}", f"cut1{suffix}"]
    for path in paths:
        (tmp_path / path).write_text(REFUSED[suffix], encoding="utf-8")
    records, _, cache = analyze.analyze_files(tmp_path, paths, cache={}, workers=1)
    reasons = [records[path].reason for path in paths]
    err = capsys.readouterr().err
    assert "crapkit: 2 file(s) could not be tokenized" in err
    assert _named(err) == reasons
    assert [paths[0] in reasons[0], paths[1] in reasons[1],
            paths[1] in reasons[0], paths[0] in reasons[1]] == [True, True, False, False]
    assert {type(rows) for rows in records.values()} == {UnanalyzableFile}
    assert cache["entries"] == {}
