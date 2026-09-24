"""A lane's line numbers are stale when the bytes they point into moved, and only then.

The stamp a lane run writes holds a digest of every file under its scopes
(`sources`), and every reader of lane staleness compares digests: the
`--reuse-artifacts` warning, the dark-line note `brief`, `next-item`,
`explain` and the MCP `get_function_brief` print, and the report's banner.
They used to ask git instead, and git's answer is about commits and its stat
cache, not the bytes: a `touch` under diff.autoRefreshIndex=false, a mode bit,
a message-only amend, a shallow CI clone missing the stamp commit and a
measurement taken on an uncommitted edit all read as stale, while a same-size
edit under a restored mtime and an edit reverted after a dirty measurement read
as fresh. Each EVENTS row in stale_tree is one of those, run through the lane
runner the coverage command uses and read back through each reader.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import stale_tree
from stale_tree import DARK, EVENTS, REL

MOVED = sorted(name for name, event in EVENTS.items() if event.moved)
KEPT = sorted(name for name, event in EVENTS.items() if not event.moved)


# --- the root: one digest rule, one comparison -------------------------------

def test_a_digest_reads_crlf_as_lf_and_a_missing_file_as_nothing(tmp_path):
    from crapkit.lane_sources import source_digest

    (tmp_path / "lf.ts").write_bytes(b"a\nb\n")
    (tmp_path / "crlf.ts").write_bytes(b"a\r\nb\r\n")

    assert source_digest(tmp_path / "lf.ts") == source_digest(tmp_path / "crlf.ts")
    assert source_digest(tmp_path / "gone.ts") == ""


def test_a_digest_reads_an_expanded_ident_keyword_as_the_stored_one(tmp_path):
    from crapkit.lane_sources import source_digest

    (tmp_path / "stored.ts").write_bytes(b"// $Id$\nx\n")
    (tmp_path / "checked-out.ts").write_bytes(b"// $Id: 47f02ca3b0e1 $\r\nx\r\n")
    (tmp_path / "edited.ts").write_bytes(b"// $Id$\ny\n")

    stored = source_digest(tmp_path / "stored.ts")
    assert source_digest(tmp_path / "checked-out.ts") == stored
    assert source_digest(tmp_path / "edited.ts") != stored


def test_a_file_that_moved_while_the_run_read_it_is_not_vouched_for():
    from crapkit.lane_sources import settled

    before = {"src/a.ts": "1", "src/b.ts": "2"}
    after = {"src/a.ts": "1", "src/b.ts": "3", "src/__pycache__/a.pyc": "4"}

    assert settled(before, after) == {"src/a.ts": "1", "src/__pycache__/a.pyc": "4"}


def test_moved_names_changed_deleted_and_new_files(tmp_path):
    from crapkit.lane_sources import file_moved, moved, source_digest

    (tmp_path / "same.ts").write_bytes(b"same\n")
    (tmp_path / "edited.ts").write_bytes(b"new\n")
    recorded = {"same.ts": source_digest(tmp_path / "same.ts"),
                "edited.ts": "old", "deleted.ts": "gone"}

    assert moved(tmp_path, recorded, ["same.ts", "edited.ts", "added.ts"]) == [
        "added.ts", "deleted.ts", "edited.ts"]
    assert not file_moved(tmp_path, recorded, "same.ts")
    assert file_moved(tmp_path, recorded, "edited.ts")
    assert not file_moved(tmp_path, recorded, "never-measured.ts")


# --- the stamp ------------------------------------------------------------------

def test_the_stamp_records_a_digest_of_each_file_under_the_lane_scope(tmp_path):
    from crapkit.lane_sources import source_digest

    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))

    sources = stale_tree.stamp(root)["coverage/coverage-final.json"]["sources"]

    assert sources == {REL: source_digest(root / REL)}


def test_a_file_the_lane_itself_writes_under_its_scope_is_not_a_change(tmp_path):
    """crapkit init ignores only .crapkit/, and a python lane with bytecode on
    writes src/__pycache__ as it runs. That by-product is recorded as the run
    left it, so the next reader does not count it as a new file."""
    root = stale_tree.measure(stale_tree.build(
        tmp_path / "repo", byproduct="src/__pycache__/app.cpython-311.pyc"))

    sources = stale_tree.stamp(root)["coverage/coverage-final.json"]["sources"]

    assert "src/__pycache__/app.cpython-311.pyc" in sources
    assert _lane_view(root)["note"] == ""


# --- the readers, one matrix row at a time -----------------------------------

def _prepared(name: str, tmp_path: Path, monkeypatch) -> tuple[Path, tuple[str, ...]]:
    """The repo after the event, and the paths that moved in it."""
    if name == "symlink-add" and not stale_tree.symlinks_work(tmp_path):
        pytest.skip("needs os.symlink: developer mode or elevation on Windows (runs on ubuntu CI)")
    event = EVENTS[name]
    root = event.prepare(tmp_path)
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

    warning = _reuse_warning(root, capsys)

    if not truth:
        assert warning == "", warning
        return
    assert f"{len(truth)} file(s) in its scopes changed since it measured them" in warning
    assert all(path in warning for path in truth), warning
    assert "coverage may be stale" in warning


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
    assert f"{REL} changed since coverage/coverage-final.json measured it" in note, note
    assert "coverage` to measure it again" in note


@pytest.mark.parametrize("name", sorted(EVENTS))
def test_the_report_banner_names_the_moved_files_and_blacks_out_nothing_else(name, tmp_path,
                                                                            monkeypatch):
    from crapkit.report import _stale_lane_reason

    root, truth = _prepared(name, tmp_path, monkeypatch)

    view = _lane_view(root)

    assert view["blackout"] is False, "a stamp with digests withholds only the files it names"
    if not truth:
        assert view["note"] == "" and _stale_lane_reason([view]) == []
        return
    assert all(path in view["note"] for path in truth), view["note"]
    banner = "".join(_stale_lane_reason([view]))
    assert "1 of 1 lanes are stale" in banner and "every other file keeps its own" in banner


# --- history: what the artifact measured, whatever git says -------------------

def _explained(root: Path) -> tuple[str, list[int]]:
    from crapkit.uncovered import load_uncovered

    lines = load_uncovered(root, stale_tree.config(root))
    return lines.note_for(REL), lines.in_span(REL, 1, 20)


def test_a_measurement_of_an_uncommitted_edit_is_fresh_at_once(tmp_path):
    root = stale_tree.build(tmp_path / "repo")
    stale_tree.write(root / REL, stale_tree.APP_TS + "\nexport const more = 1;\n")
    stale_tree.measure(root)

    assert _explained(root) == ("", sorted(DARK))


def test_reverting_the_edit_a_lane_measured_withholds_its_lines(tmp_path):
    """The artifact measured an edit that pushed every line down by two. git
    calls the reverted tree clean, and the old verdict served the shifted lines
    against it with no note."""
    root = stale_tree.build(tmp_path / "repo")
    stale_tree.write(root / REL, "// one\n// two\n" + stale_tree.APP_TS)
    stale_tree.measure(root)
    stale_tree.git(root, "checkout", "--", REL)

    note, lines = _explained(root)

    assert lines == [] and "changed since coverage/coverage-final.json measured it" in note


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


def test_a_stamp_from_an_older_crapkit_still_reads_by_its_commit(tmp_path):
    """A stamp with no `sources` (0.8.0 and older) is judged the old way until
    the next run writes one, and withholds every file while it is stale."""
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    stamps = stale_tree.stamp(root)
    del stamps["coverage/coverage-final.json"]["sources"]
    from crapkit.lanes import write_stamps
    (root / ".crapkit" / "artifacts.json").unlink()
    write_stamps(root, stamps)
    stale_tree.write(root / "src/other.ts", "export const other = 1;\n")

    view = _lane_view(root)
    note, lines = _explained(root)

    assert view["blackout"] is True and "src/other.ts" in view["note"]
    assert lines == [] and "src/other.ts" in note and "uncommitted edits included" in note
