"""Malformed Git merge-driver arguments refuse before reading or changing state."""
import pytest

from crapkit.cli import main
from crapkit.ratchet import RatchetEntry, dump_ratchet


@pytest.mark.parametrize("count", [0, 1, 2, 4])
def test_merge_requires_three_inputs_and_preserves_every_file(tmp_path, capsys, count):
    paths = [tmp_path / name for name in ("base.tsv", "ours.tsv", "theirs.tsv", "extra.tsv")]
    for path, value in zip(paths, (50, 30, 20, 90)):
        path.write_text(dump_ratchet([RatchetEntry("a.py", "f( )", value)]), encoding="utf-8")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

    assert main(["ratchet", "merge", *map(str, paths[:count])]) == 3

    output = capsys.readouterr()
    assert "exactly three files: BASE OURS THEIRS" in output.err
    assert output.out == ""
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
    assert set(tmp_path.iterdir()) == set(paths), "invalid arguments must not create state"
