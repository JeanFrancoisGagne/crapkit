"""Per-test coverage contexts (coverage.py --show-contexts) and the watch
loop's core: which files a poll finds changed. The transport shells stay
thin; the logic lives here."""
import json
import os
import subprocess

import pytest

from coverage_readers import parse_coveragepy_contexts
from crapkit import watch
from crapkit.errors import GitError
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


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()


def _tree(tmp_path, **files):
    """A repo holding `files`, committed: watch reads content through git."""
    _git(tmp_path, "init", "-q")
    for name, text in files.items():
        (tmp_path / name).write_bytes(text.encode("utf-8"))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "one")
    return tmp_path


def _later(path, seconds=60):
    later = path.stat().st_mtime + seconds
    os.utime(path, (later, later))


def _git_refuses(root, paths, within=()):
    raise GitError("git hash-object failed in repo: fatal: index file corrupt")


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


def test_a_poll_git_cannot_answer_names_its_error_once_and_judges_the_file_when_git_answers(
        tmp_path, monkeypatch, capsys):
    """git failing on a file's content is neither "unchanged" nor "changed": the
    content recorded before stands, the mtime drops so the next poll asks
    again, and git's error is named on the first poll that hits it."""
    root = _tree(tmp_path, **{"a.py": "a = 1\n"})
    before = snapshot(root, ["a.py"])
    (root / "a.py").write_text("a = 2\n", encoding="utf-8")
    _later(root / "a.py")
    real = watch.record
    monkeypatch.setattr(watch, "record", _git_refuses)

    held, moved = poll(root, ["a.py"], before)
    again, moved_again = poll(root, ["a.py"], held)

    assert moved == moved_again == [] and again.content == before.content and again.mtimes == {}
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "crapkit watch: git could not read the content of a.py (git hash-object failed in repo: "
        "fatal: index file corrupt); fix what git reports. Until git answers, each poll asks "
        "again and rescores nothing it could not read"], out
    monkeypatch.setattr(watch, "record", real)
    _, moved = poll(root, ["a.py"], again)
    assert moved == ["a.py"]


def test_a_poll_git_cannot_answer_over_many_files_names_three_sorted_and_counts_the_rest(
        tmp_path, monkeypatch, capsys):
    """The refusal names files the way every crapkit list does (named.first_few):
    the first three, here sorted, then how many more."""
    names = ["e.py", "d.py", "c.py", "b.py", "a.py"]
    root = _tree(tmp_path, **{name: "x = 1\n" for name in names})
    monkeypatch.setattr(watch, "record", _git_refuses)

    snapshot(root, names)

    assert "git could not read the content of a.py, b.py, c.py and 2 more (" in capsys.readouterr().out


def test_a_file_git_cannot_read_at_the_start_is_judged_once_it_reads(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path, **{"a.py": "a = 1\n"})
    real = watch.record
    monkeypatch.setattr(watch, "record", _git_refuses)
    before = snapshot(root, ["a.py"])
    monkeypatch.setattr(watch, "record", real)

    after, moved = poll(root, ["a.py"], before)

    assert before.mtimes == {} and moved == ["a.py"], "nothing was recorded, so its bytes are new"
    assert poll(root, ["a.py"], after)[1] == []
    assert "git could not read the content of a.py" in capsys.readouterr().out


def test_digests_are_the_content_records_blob_ids_and_leave_out_a_path_with_none(tmp_path):
    """watch records content the way a lane stamp does: git's blob id, the
    index's for a tracked file git calls unchanged, hashed for an untracked or
    edited one. A path that is gone or is a directory holds none, so it is left
    out and the next poll reads it again."""
    root = _tree(tmp_path, **{"a.py": "a = 1\n"})
    (root / "new.py").write_text("n = 1\n", encoding="utf-8")
    (root / "pkg").mkdir()

    found = watch.digests(root, ["a.py", "new.py", "gone.py", "pkg"])

    assert found == {"a.py": _git(root, "hash-object", "a.py"),
                     "new.py": _git(root, "hash-object", "new.py")}


def test_a_crlf_rewrite_under_autocrlf_is_the_same_blob_and_rescores_nothing(tmp_path):
    """Under core.autocrlf=true git stores CRLF bytes as their LF blob, so a
    checkout or an editor rewriting a file's line endings is no change."""
    root = _tree(tmp_path, **{"a.py": "a = 1\nb = 2\n"})
    _git(root, "config", "core.autocrlf", "true")
    before = snapshot(root, ["a.py"])
    (root / "a.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    _later(root / "a.py")

    assert poll(root, ["a.py"], before)[1] == []


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="chmod 000 refuses a read only to a non-root POSIX user")
def test_a_file_that_refuses_a_read_is_left_out_and_the_others_are_still_judged(tmp_path):
    """One file nobody may read would make git's hash-object refuse the whole
    batch; watch leaves it out, so every other edit in that poll still lands."""
    root = _tree(tmp_path, **{"a.py": "a = 1\n", "locked.py": "l = 1\n"})
    before = snapshot(root, ["a.py", "locked.py"])
    for name, text in (("a.py", "a = 2\n"), ("locked.py", "l = 2\n")):
        (root / name).write_text(text, encoding="utf-8")
        _later(root / name)
    (root / "locked.py").chmod(0)
    try:
        after, moved = poll(root, ["a.py", "locked.py"], before)
    finally:
        (root / "locked.py").chmod(0o644)

    assert moved == ["a.py"] and "locked.py" not in after.mtimes


def test_git_reads_narrow_to_the_polled_paths_while_they_fit_on_a_command_line():
    """A poll asks git about the few files that stirred; a snapshot of a whole
    large tree reads the whole index instead of naming every file."""
    few = ["a.py", "src/b.py"]
    many = [f"src/pkg{n:05d}/module_{n:05d}.py" for n in range(2000)]

    assert watch.git_reach(few) == ("a.py", "src/b.py")
    assert watch.git_reach(many) == ()
