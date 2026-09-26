"""A lane's line numbers are stale when the bytes they point into moved, and only then.

The stamp a lane run writes holds the git blob id of every file under its
scopes (`blobs`), and every reader of lane staleness compares blob ids: the
`--reuse-artifacts` warning, the dark-line note `brief`, `next-item`,
`explain` and the MCP `get_function_brief` print, and the report's banner.
They used to ask git's diff instead, and that answer is about commits and its
stat cache, not the content: a `touch` under diff.autoRefreshIndex=false, a
mode bit, a message-only amend, a shallow CI clone missing the stamp commit and
a measurement taken on an uncommitted edit all read as stale, while an edit
reverted after a dirty measurement read as fresh. Each EVENTS row in stale_tree
is one of those, run through the lane runner the coverage command uses and read
back through each reader. The oracle for "moved" is git's own: the id
`git hash-object --path` gives the file now against the one recorded.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import stale_tree
from crapkit.invocation import _self
from stale_tree import DARK, EVENTS, REL

MOVED = sorted(name for name, event in EVENTS.items() if event.moved)
KEPT = sorted(name for name, event in EVENTS.items() if not event.moved)
# A monorepo member keeps crapkit.toml one directory below the checkout's top.
PLACES = pytest.mark.parametrize("nested", [False, True], ids=["root-at-top", "root-below-top"])


# --- the root: one content rule, one comparison ------------------------------

def _repo(tmp_path: Path, files: dict, gitcfg: dict | None = None, nested: bool = False) -> Path:
    """A committed repo holding `files`, rooted one directory below the
    checkout's top when `nested`."""
    root = tmp_path / "rec" / "member" if nested else tmp_path / "rec"
    root.mkdir(parents=True)
    stale_tree.git(tmp_path / "rec", "init", "-q", "-b", "main")
    for key, value in (gitcfg or {}).items():
        stale_tree.git(root, "config", key, value)
    for rel, data in files.items():
        stale_tree.write(root / rel, data)
        stale_tree.age(root / rel)
    stale_tree.git(root, "add", "-A")
    stale_tree.git(root, "commit", "-q", "-m", "init")
    return root


def _blob(root: Path, rel: str) -> str:
    return stale_tree.git(root, "hash-object", f"--path={rel}", "--", rel).strip()


def test_the_record_holds_the_blob_id_git_add_would_store(tmp_path):
    """A CRLF checkout under core.autocrlf=true holds its LF blob's id, and
    only the id git itself gives a file is recorded, never a digest of raw
    bytes."""
    from crapkit.lane_sources import record

    root = _repo(tmp_path, {"src/a.ts": b"a\nb\n"}, {"core.autocrlf": "true"})
    (root / "src/a.ts").unlink()
    stale_tree.git(root, "checkout", "--", "src/a.ts")
    assert b"\r\n" in (root / "src/a.ts").read_bytes()
    committed = stale_tree.git(root, "rev-parse", "HEAD:src/a.ts").strip()

    assert record(root, ["src/a.ts", "src/gone.ts"], ("src",)) == {"src/a.ts": committed}


def test_a_crlf_rewrite_under_autocrlf_false_is_a_new_blob(tmp_path):
    from crapkit.lane_sources import record

    root = _repo(tmp_path, {"src/a.ts": b"a\nb\n"}, {"core.autocrlf": "false"})
    (root / "src/a.ts").write_bytes(b"a\r\nb\r\n")

    assert record(root, ["src/a.ts"], ("src",)) == {"src/a.ts": _blob(root, "src/a.ts")}
    assert record(root, ["src/a.ts"], ("src",))["src/a.ts"] != stale_tree.git(
        root, "rev-parse", "HEAD:src/a.ts").strip()


def test_an_expanded_ident_keyword_holds_the_stored_blob(tmp_path):
    from crapkit.lane_sources import record

    root = _repo(tmp_path, {".gitattributes": "*.ts ident\n", "src/a.ts": "// $Id$\nx\n"})
    (root / "src/a.ts").unlink()
    stale_tree.git(root, "checkout", "--", "src/a.ts")
    assert b"$Id: " in (root / "src/a.ts").read_bytes()

    assert record(root, ["src/a.ts"], ("src",)) == {
        "src/a.ts": stale_tree.git(root, "rev-parse", "HEAD:src/a.ts").strip()}


@PLACES
def test_an_untracked_file_and_a_path_outside_the_reads_are_hashed(tmp_path, nested):
    from crapkit.lane_sources import record

    root = _repo(tmp_path, {"src/a.ts": "a\n", "lib/b.ts": "b\n"}, nested=nested)
    stale_tree.write(root / "src/new.ts", "new\n")

    assert record(root, ["src/new.ts", "lib/b.ts"], ("src",)) == {
        "src/new.ts": _blob(root, "src/new.ts"), "lib/b.ts": _blob(root, "lib/b.ts")}


@PLACES
def test_a_flagged_file_edited_on_disk_is_hashed_not_read_from_the_index(tmp_path, nested):
    """skip-worktree and assume-unchanged hide an edit from git's diff; the
    index's id would vouch for bytes that are not on disk."""
    from crapkit.lane_sources import record

    root = _repo(tmp_path, {"src/a.ts": "a\n", "src/b.ts": "b\n"}, nested=nested)
    stale_tree.git(root, "update-index", "--skip-worktree", "src/a.ts")
    stale_tree.git(root, "update-index", "--assume-unchanged", "src/b.ts")
    stale_tree.write(root / "src/a.ts", "edited a\n")
    stale_tree.write(root / "src/b.ts", "edited b\n")

    assert record(root, ["src/a.ts", "src/b.ts"], ("src",)) == {
        "src/a.ts": _blob(root, "src/a.ts"), "src/b.ts": _blob(root, "src/b.ts")}


def test_a_same_size_edit_under_the_old_mtime_keeps_the_index_id(tmp_path):
    """The named limit of the fast path: git's stat cache calls the file
    unchanged, so the record keeps the index's id while hash-object gives
    another. Hashing every file would close it; that cost is measured first."""
    from crapkit.lane_sources import record

    root = _repo(tmp_path, {"src/a.ts": "case 1\n"})
    path = root / "src/a.ts"
    stat = path.stat()
    path.write_bytes(b"case 7\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    committed = stale_tree.git(root, "rev-parse", "HEAD:src/a.ts").strip()

    assert _blob(root, "src/a.ts") != committed
    assert record(root, ["src/a.ts"], ("src",)) == {"src/a.ts": committed}


def test_a_file_that_moved_while_the_run_read_it_is_not_vouched_for():
    from crapkit.lane_sources import settled

    before = {"src/a.ts": "1", "src/b.ts": "2"}
    after = {"src/a.ts": "1", "src/b.ts": "3", "src/__pycache__/a.pyc": "4"}

    assert settled(before, after) == {"src/a.ts": "1", "src/__pycache__/a.pyc": "4"}


def test_moved_names_changed_deleted_and_new_files(tmp_path):
    from crapkit.lane_sources import file_moved, moved, record

    root = _repo(tmp_path, {"same.ts": "same\n", "edited.ts": "old\n", "deleted.ts": "gone\n"})
    recorded = record(root, ["same.ts", "edited.ts", "deleted.ts"])
    stale_tree.write(root / "edited.ts", "new\n")
    (root / "deleted.ts").unlink()
    stale_tree.write(root / "added.ts", "added\n")

    assert moved(root, recorded, ["same.ts", "edited.ts", "added.ts"]) == [
        "added.ts", "deleted.ts", "edited.ts"]
    assert not file_moved(root, recorded, "same.ts")
    assert file_moved(root, recorded, "edited.ts")
    assert not file_moved(root, recorded, "never-measured.ts")


def test_a_listed_path_with_no_bytes_is_not_a_new_file(tmp_path):
    """git lists a tracked file deleted before the run though nothing is on
    disk, and a directory holds no blob. Neither had content to record, so
    neither is new on a later read until a file appears."""
    from crapkit.lane_sources import moved

    root = _repo(tmp_path, {"deleted.ts": "was here\n"})
    (root / "deleted.ts").unlink()
    (root / "vendor").mkdir()

    assert moved(root, {}, ["vendor", "deleted.ts"]) == []
    stale_tree.write(root / "deleted.ts", "back\n")
    assert moved(root, {}, ["vendor", "deleted.ts"]) == ["deleted.ts"]


def test_without_git_the_record_raises_instead_of_answering(tmp_path, monkeypatch):
    from crapkit.errors import GitError
    from crapkit.lane_sources import moved

    root = _repo(tmp_path, {"a.ts": "a\n"})
    monkeypatch.setenv("PATH", str(tmp_path / "no-git-here"))

    with pytest.raises(GitError):
        moved(root, {"a.ts": "0" * 40})


# --- the stamp ------------------------------------------------------------------

def test_the_stamp_records_the_blob_id_of_each_file_under_the_lane_scope(tmp_path):
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))

    blobs = stale_tree.stamp(root)["coverage/coverage-final.json"]["blobs"]

    assert blobs == {REL: stale_tree.git(root, "rev-parse", f"HEAD:{REL}").strip()}


def test_a_run_before_the_first_commit_writes_no_stamp_and_the_note_says_so(tmp_path):
    """No HEAD, no commit to stamp: the run records nothing rather than a
    stamp git could not fill, and the dark-line note withholds the artifact's
    lines, naming the missing stamp. The reuse warning stays quiet, since no
    stamp means nothing to compare against."""
    root = tmp_path / "repo"
    root.mkdir()
    stale_tree.git(root, "init", "-q", "-b", "main")
    for rel, text in {".gitignore": ".crapkit/\ncoverage/\n", "crapkit.toml": stale_tree.toml(),
                      "make_cov.py": stale_tree.MAKE_COV, REL: stale_tree.APP_TS}.items():
        stale_tree.write(root / rel, text)
    stale_tree.git(root, "add", "-A")
    stale_tree.measure(root)

    note, lines = _explained(root)

    assert not (root / ".crapkit" / "artifacts.json").exists()
    assert lines == [] and "no stamp records the commit coverage/coverage-final.json was built at" in note


def test_a_file_the_lane_itself_writes_under_its_scope_is_not_a_change(tmp_path):
    """crapkit init ignores only .crapkit/, and a python lane with bytecode on
    writes src/__pycache__ as it runs. That by-product is recorded as the run
    left it, so the next reader does not count it as a new file."""
    root = stale_tree.measure(stale_tree.build(
        tmp_path / "repo", byproduct="src/__pycache__/app.cpython-311.pyc"))

    blobs = stale_tree.stamp(root)["coverage/coverage-final.json"]["blobs"]

    assert "src/__pycache__/app.cpython-311.pyc" in blobs
    assert _lane_view(root)["note"] == ""


def _with_submodule(tmp_path: Path) -> Path:
    """The measured repo with a submodule at src/vendor, under the lane's scope."""
    library = tmp_path / "library"
    library.mkdir()
    stale_tree.git(library, "init", "-q", "-b", "main")
    stale_tree.write(library / "lib.ts", "export const v = 1;\n")
    stale_tree.git(library, "add", "-A")
    stale_tree.git(library, "commit", "-q", "-m", "lib")
    root = stale_tree.build(tmp_path / "repo")
    stale_tree.git(root, "-c", "protocol.file.allow=always", "submodule", "--quiet", "add",
                   library.as_uri(), "src/vendor")
    stale_tree.git(root, "commit", "-q", "-m", "vendor")
    return stale_tree.measure(root)


def _touch_inside(root: Path, norefresh: bool = False) -> None:
    if norefresh:
        stale_tree.git(root / "src/vendor", "config", "diff.autoRefreshIndex", "false")
        stale_tree.git(root, "config", "diff.autoRefreshIndex", "false")
    stale_tree.touch(root / "src/vendor/lib.ts")


SUBMODULE_EVENTS = {
    "nothing": (lambda root: None, False),
    "submodule-touch": (_touch_inside, False),
    "submodule-touch-norefresh": (lambda root: _touch_inside(root, norefresh=True), False),
    "submodule-edit": (lambda root: stale_tree.write(root / "src/vendor/lib.ts",
                                                    "export const v = 2;\n"), True),
}


@pytest.mark.parametrize("name", sorted(SUBMODULE_EVENTS))
def test_a_submodule_under_the_scope_is_recorded_by_its_commit(name, tmp_path, capsys):
    """git lists the submodule as one path, src/vendor. The record holds the
    commit its checkout is at, so a touch inside it moves nothing, and an edit
    inside it names src/vendor: the checkout no longer holds that commit's
    bytes. Counting it as a file the record lacked made every read after the
    run say src/vendor changed; recording nothing for it made an edit silent."""
    root = _with_submodule(tmp_path)
    act, moves = SUBMODULE_EVENTS[name]
    act(root)

    warning, note = _reuse_warning(root, capsys), _lane_view(root)["note"]

    if not moves:
        assert (warning, note) == ("", "")
        return
    assert "1 file(s) in its scopes changed since it measured them (src/vendor)" in warning
    assert "src/vendor" in note


def test_a_tracked_file_deleted_before_the_run_is_not_a_change_after_it(tmp_path, capsys):
    root = stale_tree.build(tmp_path / "repo", extra={"src/other.ts": "export const o = 1;\n"})
    (root / "src/other.ts").unlink()
    stale_tree.measure(root)

    assert (_reuse_warning(root, capsys), _lane_view(root)["note"]) == ("", "")


# --- the readers, one matrix row at a time -----------------------------------

def _prepared(name: str, tmp_path: Path, monkeypatch,
              nested: bool = False) -> tuple[Path, tuple[str, ...]]:
    """The repo after the event, and the paths that moved in it."""
    if name == "symlink-add" and not stale_tree.symlinks_work(tmp_path):
        pytest.skip("needs os.symlink: developer mode or elevation on Windows (runs on ubuntu CI)")
    event = EVENTS[name]
    root = event.prepare(tmp_path, nested=nested)
    if name == "git-missing":
        monkeypatch.setenv("PATH", str(tmp_path))
    return root, _truth(name, root, event.moved)


def _truth(name: str, root: Path, expected: tuple[str, ...]) -> tuple[str, ...]:
    """A case-only rename moves nothing where the filesystem folds case (NTFS,
    APFS) and renames the file where it does not (ext4). Through `git mv` the
    index names src/App.ts either way, a file the stamp never measured."""
    if name.startswith("case-only-") and not (root / REL).exists():
        return ("src/App.ts", REL)
    return expected


def _reuse_warning(root: Path, capsys) -> str:
    capsys.readouterr()
    stale_tree.run_lanes(root, reuse=True)
    return "\n".join(line for line in capsys.readouterr().err.splitlines() if "lane 'unit'" in line)


def _lane_view(root: Path) -> dict:
    from crapkit.uncovered import lane_views

    (view,) = lane_views(root, stale_tree.config(root))
    return view


@pytest.mark.parametrize("name", sorted(EVENTS))
def test_the_reuse_warning_names_exactly_the_files_whose_bytes_moved(name, tmp_path, monkeypatch,
                                                                     capsys):
    root, truth = _prepared(name, tmp_path, monkeypatch)

    _assert_the_warning_names(name, _reuse_warning(root, capsys), truth)


@pytest.mark.parametrize("name", stale_tree.NESTED)
def test_under_a_root_below_the_checkout_top_the_warning_names_the_files_that_moved(
        name, tmp_path, monkeypatch, capsys):
    """A monorepo member keeps crapkit.toml one directory below .git. git
    hash-object reads `--stdin-paths` from the checkout's top, so every file
    the index could not vouch for came back `could not open`, and the warning
    said git could not answer where it should have named the file."""
    root, truth = _prepared(name, tmp_path, monkeypatch, nested=True)

    _assert_the_warning_names(name, _reuse_warning(root, capsys), truth)


def _assert_the_warning_names(name: str, warning: str, truth) -> None:
    if not truth:
        assert warning == "", warning
        return
    assert warning.endswith("so its coverage may be stale; rerun the lane "
                            f"(`{_self()} coverage --lane unit`) to measure the tree as it is"), warning
    if EVENTS[name].unknown:
        assert "git cannot say which files in its scopes changed since it measured them" in warning
        assert "git executable not found" in warning
        return
    assert f"{len(truth)} file(s) in its scopes changed since it measured them" in warning
    assert all(path in warning for path in truth), warning


@pytest.mark.parametrize("name", sorted(EVENTS))
def test_the_dark_line_note_withholds_only_a_file_whose_bytes_moved(name, tmp_path, monkeypatch):
    from crapkit.uncovered import load_uncovered

    root, truth = _prepared(name, tmp_path, monkeypatch)

    lines = load_uncovered(root, stale_tree.config(root))

    if REL not in truth:
        assert lines.note_for(REL) == "", lines.note_for(REL)
        assert lines.in_span(REL, 1, 20) == sorted(DARK)
        return
    assert lines.in_span(REL, 1, 20) == []
    note = lines.note_for(REL)
    if EVENTS[name].unknown:
        assert f"git cannot say whether {REL} changed since" in note, note
        return
    assert f"{REL} changed since coverage/coverage-final.json measured it" in note, note
    assert "coverage` to measure it again" in note


def _banner(view: dict) -> str:
    from crapkit.report import _stale_lane_reason

    return "".join(_stale_lane_reason([view]))


def _assert_named(view: dict, truth) -> None:
    assert all(path in view["note"] for path in truth), view["note"]
    assert "1 of 1 lanes are stale" in _banner(view), _banner(view)
    assert "every other file keeps its own" in _banner(view)


@pytest.mark.parametrize("name", sorted(EVENTS))
def test_the_report_banner_names_the_moved_files_and_blacks_out_nothing_else(name, tmp_path,
                                                                            monkeypatch):
    root, truth = _prepared(name, tmp_path, monkeypatch)

    view = _lane_view(root)

    assert view["blackout"] is False, "a stamp with digests withholds only the files it names"
    if not truth:
        assert (view["note"], _banner(view)) == ("", "")
        return
    if EVENTS[name].unknown:
        assert "git cannot say which files in its scopes changed" in view["note"], view["note"]
        return
    _assert_named(view, truth)


# --- history: what the artifact measured, whatever git says -------------------

def _explained(root: Path) -> tuple[str, list[int]]:
    from crapkit.uncovered import load_uncovered

    lines = load_uncovered(root, stale_tree.config(root))
    return lines.note_for(REL), lines.in_span(REL, 1, 20)


@PLACES
def test_a_measurement_of_an_uncommitted_edit_is_fresh_at_once(tmp_path, nested):
    """Below the checkout's top, the run's own record of the edited file
    failed, so the stamp held no blob ids and its commit judged it stale."""
    root = stale_tree.build(**stale_tree.placed(tmp_path, nested))
    stale_tree.write(root / REL, stale_tree.APP_TS + "\nexport const more = 1;\n")
    stale_tree.measure(root)

    assert _explained(root) == ("", sorted(DARK))
    assert REL in stale_tree.stamp(root)["coverage/coverage-final.json"]["blobs"]


@PLACES
def test_reverting_the_edit_a_lane_measured_withholds_its_lines(tmp_path, nested):
    """The artifact measured an edit that pushed every line down by two. git
    calls the reverted tree clean, and the old verdict served the shifted lines
    against it with no note."""
    root = stale_tree.build(**stale_tree.placed(tmp_path, nested))
    stale_tree.write(root / REL, "// one\n// two\n" + stale_tree.APP_TS)
    stale_tree.measure(root)
    stale_tree.git(root, "checkout", "--", REL)

    note, lines = _explained(root)

    assert lines == [] and "changed since coverage/coverage-final.json measured it" in note


@PLACES
def test_the_reuse_warning_judges_the_bytes_the_run_measured(tmp_path, capsys, nested):
    """The warning compared the stamp's commit with the tree: it fired right
    after a run on an uncommitted edit, and went quiet once that edit was
    reverted, while the artifact still described the edit."""
    root = stale_tree.build(**stale_tree.placed(tmp_path, nested))
    stale_tree.write(root / REL, "// one\n// two\n" + stale_tree.APP_TS)
    stale_tree.measure(root)

    after_the_run = _reuse_warning(root, capsys)
    stale_tree.git(root, "checkout", "--", REL)

    assert after_the_run == ""
    assert f"1 file(s) in its scopes changed since it measured them ({REL})" in _reuse_warning(
        root, capsys)


def test_an_edit_to_another_file_keeps_this_files_lines(tmp_path):
    root = stale_tree.measure(stale_tree.build(
        tmp_path / "repo", extra={"src/other.ts": "export const other = 1;\n"}))
    stale_tree.write(root / "src/other.ts", "export const other = 2;\n")

    assert _explained(root) == ("", sorted(DARK))


def test_a_non_source_file_planted_under_the_scope_keeps_the_lines(tmp_path):
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    stale_tree.write(root / "src/__pycache__/app.cpython-311.pyc", b"bytecode")

    assert _explained(root) == ("", sorted(DARK))


def test_a_committed_mode_change_keeps_the_lines(tmp_path):
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    stale_tree.git(root, "update-index", "--chmod=+x", REL)
    stale_tree.git(root, "commit", "-q", "-m", "exec bit")

    assert _explained(root) == ("", sorted(DARK))


def test_a_descendant_commit_outside_the_scope_keeps_the_lines(tmp_path):
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    stale_tree.write(root / "README.md", "docs\n")
    stale_tree.git(root, "add", "-A")
    stale_tree.git(root, "commit", "-q", "-m", "docs")

    assert _explained(root) == ("", sorted(DARK))


def test_a_stamp_commit_force_pushed_away_keeps_the_lines(tmp_path):
    """A fresh clone carrying .crapkit/ from a clone whose commit was amended
    and force-pushed: the stamp commit is in no object store here."""
    origin = stale_tree.measure(stale_tree.build(tmp_path / "origin"))
    stale_tree.git(origin, "commit", "-q", "--amend", "-m", "force-pushed")
    clone = stale_tree.clone_with_state(origin, tmp_path / "clone")
    commit = stale_tree.stamp(clone)["coverage/coverage-final.json"]["commit"]
    assert stale_tree.git(clone, "cat-file", "-t", commit, check=False).strip() == ""

    assert _explained(clone) == ("", sorted(DARK))


def test_a_stamp_commit_force_pushed_away_still_names_a_real_edit(tmp_path, capsys):
    """The same clone after src/app.ts really changed. The commit check could
    not reach the stamp's commit, so it could not say what moved; the blob ids
    name the edited file in the note and in the reuse warning."""
    origin = stale_tree.measure(stale_tree.build(tmp_path / "origin"))
    stale_tree.git(origin, "commit", "-q", "--amend", "-m", "force-pushed")
    clone = stale_tree.clone_with_state(origin, tmp_path / "clone")
    stale_tree.write(clone / REL, "// one\n// two\n" + stale_tree.APP_TS)

    note, lines = _explained(clone)

    assert lines == [] and f"{REL} changed since coverage/coverage-final.json measured it" in note
    assert REL in _reuse_warning(clone, capsys)


def test_a_stamp_from_an_older_crapkit_still_reads_by_its_commit(tmp_path):
    """A stamp with no `blobs` (0.8.0 and older) is judged the old way until
    the next run writes one, and withholds every file while it is stale."""
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    stamps = stale_tree.stamp(root)
    del stamps["coverage/coverage-final.json"]["blobs"]
    from crapkit.lanes import write_stamps
    (root / ".crapkit" / "artifacts.json").unlink()
    write_stamps(root, stamps)
    stale_tree.write(root / "src/other.ts", "export const other = 1;\n")

    view = _lane_view(root)
    note, lines = _explained(root)

    assert view["blackout"] is True and "src/other.ts" in view["note"]
    assert lines == [] and "src/other.ts" in note and "uncommitted edits included" in note
