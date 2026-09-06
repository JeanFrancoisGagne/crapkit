"""Moves and merges retain the meaning of ratchet keys and their measured metric."""
import subprocess
import sys

import pytest

from crapkit.ratchet import RatchetEntry, dump_ratchet, load_ratchet, read_key_version, read_stamp


def _run(root, *args):
    return subprocess.run([sys.executable, "-B", "-m", "crapkit", "ratchet", *args],
                          cwd=root, capture_output=True, text=True, encoding="utf-8")


def _marks(value=20, version=0):
    return dump_ratchet([RatchetEntry("a.ts", "f( x )#2", value)],
                        stamp="crapkit-analysis=7 lizard=older", key_version=version)


@pytest.mark.parametrize("version", [0, 1])
def test_move_preserves_both_stamps_and_values(tmp_path, version):
    (tmp_path / "crapkit.toml").write_text(
        '[crapkit]\ntarget=6\n[[scope]]\nname="app"\npaths=["."]\nlanguages=["typescript"]\n',
        encoding="utf-8")
    path = tmp_path / "crapkit-ratchet.tsv"
    path.write_text(_marks(version=version), encoding="utf-8")
    result = _run(tmp_path, "move", "a.ts", "renamed.ts")
    assert result.returncode == 0, result.stderr
    text = path.read_text(encoding="utf-8")
    assert read_key_version(text) == version
    assert read_stamp(text) == "crapkit-analysis=7 lizard=older"
    assert load_ratchet(text) == [RatchetEntry("renamed.ts", "f( x )#2", 20)]


@pytest.mark.parametrize("version", [0, 1])
def test_merge_preserves_shared_identity_version(tmp_path, version):
    paths = [tmp_path / name for name in ("base", "ours", "theirs")]
    for path, mark in zip(paths, (50, 30, 20)):
        path.write_text(_marks(mark, version), encoding="utf-8")
    result = _run(tmp_path, "merge", *(str(path) for path in paths))
    assert result.returncode == 0, result.stderr
    assert read_key_version(paths[1].read_text()) == version
    assert load_ratchet(paths[1].read_text())[0].crap == 20


def test_merge_different_key_versions_refuses_without_rewriting_ours(tmp_path):
    paths = [tmp_path / name for name in ("base", "ours", "theirs")]
    for path, version in zip(paths, (0, 1, 1)):
        path.write_text(_marks(version=version), encoding="utf-8")
    before = paths[1].read_bytes()
    result = _run(tmp_path, "merge", *(str(path) for path in paths))
    assert result.returncode == 3
    assert "key identity versions differ" in result.stderr
    assert paths[1].read_bytes() == before
