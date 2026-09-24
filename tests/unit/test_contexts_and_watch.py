"""Per-test coverage contexts (coverage.py --show-contexts) and the watch
loop's core: which files a poll finds changed. The transport shells stay
thin; the logic lives here."""
import json
import os

from coverage_readers import parse_coveragepy_contexts
from crapkit import watch
from crapkit.watch import changed_paths, poll, snapshot

REPORT = {
    "meta": {"branch_coverage": True, "show_contexts": True},
    "files": {"pylib/mod.py": {
        "functions": {},
        "contexts": {
            "2": ["tests/test_mod.py::test_over|run", "tests/test_mod.py::test_under|run"],
            "3": ["tests/test_mod.py::test_over|run"],
            "9": [""],
        },
    }},
}


def test_contexts_map_lines_to_cleaned_test_ids():
    ctx = parse_coveragepy_contexts(json.dumps(REPORT), path_prefix="")
    assert ctx["pylib/mod.py"][2] == ["tests/test_mod.py::test_over", "tests/test_mod.py::test_under"]
    assert ctx["pylib/mod.py"][3] == ["tests/test_mod.py::test_over"]
    assert 9 not in ctx["pylib/mod.py"], "the empty (module import) context is not a test"


def test_contexts_absent_returns_empty():
    bare = {"meta": {"branch_coverage": True}, "files": {"a.py": {"functions": {}}}}
    assert parse_coveragepy_contexts(json.dumps(bare), path_prefix="") == {}


def test_changed_paths_reports_new_modified_and_deleted():
    before = {"a.py": 1.0, "b.py": 2.0, "gone.py": 3.0}
    after = {"a.py": 1.0, "b.py": 5.0, "new.py": 1.0}
    assert changed_paths(before, after) == ["b.py", "gone.py", "new.py"]


def test_changed_paths_quiet_when_nothing_moved():
    snap = {"a.py": 1.0}
    assert changed_paths(snap, dict(snap)) == []


def _tree(tmp_path, **files):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def _later(path, seconds=60):
    later = path.stat().st_mtime + seconds
    os.utime(path, (later, later))


def test_a_poll_names_the_edited_the_new_and_the_deleted_file_and_not_the_touched_one(tmp_path):
    root = _tree(tmp_path, **{"a.py": "a = 1\n", "b.py": "b = 1\n", "gone.py": "g = 1\n"})
    before = snapshot(root, ["a.py", "b.py", "gone.py"])
    _later(root / "a.py")
    (root / "b.py").write_text("b = 2\n", encoding="utf-8")
    _later(root / "b.py")
    (root / "gone.py").unlink()
    (root / "new.py").write_text("n = 1\n", encoding="utf-8")

    after, moved = poll(root, ["a.py", "b.py", "gone.py", "new.py"], before)

    assert moved == ["b.py", "gone.py", "new.py"]
    assert set(after.content) == {"a.py", "b.py", "new.py"}


def test_a_file_that_cannot_be_read_is_read_again_next_poll_and_never_taken_for_a_change(
        tmp_path, monkeypatch):
    """A save in progress can refuse a read. The failed read keeps the content
    recorded before and drops the mtime, so the next poll looks again."""
    root = _tree(tmp_path, **{"a.py": "a = 1\n"})
    before = snapshot(root, ["a.py"])
    (root / "a.py").write_text("a = 2\n", encoding="utf-8")
    _later(root / "a.py")
    real = watch.digests
    monkeypatch.setattr(watch, "digests", lambda root, paths: {})

    held, moved = poll(root, ["a.py"], before)

    assert moved == [] and held.content == before.content and held.mtimes == {}
    monkeypatch.setattr(watch, "digests", real)
    _, moved = poll(root, ["a.py"], held)
    assert moved == ["a.py"]


def test_a_file_unreadable_at_the_start_is_judged_once_it_reads(tmp_path, monkeypatch):
    root = _tree(tmp_path, **{"a.py": "a = 1\n"})
    real = watch.digests
    monkeypatch.setattr(watch, "digests", lambda root, paths: {})
    before = snapshot(root, ["a.py"])
    monkeypatch.setattr(watch, "digests", real)

    after, moved = poll(root, ["a.py"], before)

    assert before.mtimes == {} and moved == ["a.py"], "nothing was recorded, so its bytes are new"
    assert poll(root, ["a.py"], after)[1] == []
