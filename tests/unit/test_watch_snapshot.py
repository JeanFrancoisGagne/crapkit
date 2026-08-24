"""What one watch poll costs and what it is allowed to see.

The reference here is the implementation this replaced: one os.stat per tracked
file. Every case is asserted EQUAL to that reference, because the poll runs
every two seconds against a live tree and any divergence would show up as a
file that silently stopped being watched.
"""
import os
import stat
from pathlib import Path

import pytest

from crapkit.watch import snapshot_mtimes


def _stat_snapshot(root: Path, files: list[str]) -> dict[str, float]:
    """The old body, kept as the oracle: stat each tracked file, keep the
    readable regular ones, drop everything else without a word."""
    out = {}
    for rel in files:
        try:
            st = os.stat(root / rel)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            out[rel] = st.st_mtime
    return out


class _Listed:
    """os.scandir's shape: an iterable context manager over DirEntry."""

    def __init__(self, entries):
        self._entries = entries

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._entries)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "top.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("z = 3\n", encoding="utf-8")
    (tmp_path / "src" / "deep" / "c.py").write_text("w = 4\n", encoding="utf-8")
    (tmp_path / "src" / "pkg").mkdir()  # a directory standing where a file was
    return tmp_path


def _counting_stat(monkeypatch) -> list:
    calls = []
    real = os.stat

    def counted(path, *a, **kw):
        calls.append(path)
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "stat", counted)
    return calls


def _recording_scandir(monkeypatch) -> list:
    listed = []
    real = os.scandir

    def recorded(path):
        listed.append(path)
        return real(path)

    monkeypatch.setattr(os, "scandir", recorded)
    return listed


def test_present_files_cost_one_listing_per_directory_not_a_stat_each(tree, monkeypatch):
    """14,152 tracked files were 14,152 stats every two seconds, 234 ms of a
    poll. The times ride in the directory listing on Windows, so one scandir
    per directory answers every file in it and no per-file stat is left."""
    files = ["top.py", "src/a.py", "src/b.py", "src/deep/c.py"]
    listed = _recording_scandir(monkeypatch)
    stats = _counting_stat(monkeypatch)

    snap = snapshot_mtimes(tree, files)
    per_file_stats = list(stats)

    assert sorted(snap) == sorted(files)
    assert per_file_stats == [], "every file was answered by a listing"
    assert sorted(str(p) for p in listed) == sorted(
        str(p) for p in (tree, tree / "src", tree / "src" / "deep")), \
        "one listing per directory, no more"


def test_the_snapshot_matches_the_stat_it_replaced_on_every_odd_path(tree):
    """A deleted file, a directory where a file was expected, a file under a
    directory that does not exist at all, and the ordinary ones beside them."""
    files = ["top.py", "src/a.py", "src/gone.py", "src/pkg",
             "nowhere/x.py", "src/deep/c.py"]

    assert snapshot_mtimes(tree, files) == _stat_snapshot(tree, files)


def test_an_empty_file_list_lists_nothing_at_all(tree, monkeypatch):
    listed = _recording_scandir(monkeypatch)

    assert snapshot_mtimes(tree, []) == {}
    assert listed == []


def test_a_file_born_after_its_directory_was_listed_is_still_found(tree, monkeypatch):
    """The listing is a snapshot taken at one instant; a file created a
    millisecond later is not in it. Falling back to that file's own stat is
    what keeps the answer identical to the stat-per-file poll."""
    real = os.scandir

    def list_then_create(path):
        entries = list(real(path))
        (tree / "src" / "born.py").write_text("n = 5\n", encoding="utf-8")
        return _Listed(entries)

    monkeypatch.setattr(os, "scandir", list_then_create)
    files = ["src/a.py", "src/born.py"]

    snap = snapshot_mtimes(tree, files)

    monkeypatch.undo()
    assert snap == _stat_snapshot(tree, files), "the newborn is stat-ed, not dropped"
    assert "src/born.py" in snap


def test_a_directory_that_cannot_be_listed_falls_back_to_stat(tree, monkeypatch):
    """A listing that raises leaves its files unanswered; each one is then
    stat-ed exactly as before, so an unreadable directory costs speed, never
    an entry."""
    def refuse(path):
        raise PermissionError(str(path))

    monkeypatch.setattr(os, "scandir", refuse)
    files = ["src/a.py", "src/gone.py", "src/pkg"]

    snap = snapshot_mtimes(tree, files)

    monkeypatch.undo()
    assert snap == _stat_snapshot(tree, files)
    assert snap == {"src/a.py": (tree / "src" / "a.py").stat().st_mtime}


def test_the_snapshot_keeps_the_order_it_was_given(tree):
    """Two polls of the same tree have to produce the same mapping in the same
    order; a filesystem listing order is nobody's contract."""
    files = ["src/b.py", "top.py", "src/a.py", "src/deep/c.py"]

    assert list(snapshot_mtimes(tree, files)) == files
