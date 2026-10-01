"""The marks file's history, read in one place: `marks_history`.

ratchet report and brief read mark ages off the file's commits, and verify
judges a deleted or emptied marks file against the newest marks a commit since
the baseline held. Three readers once had three paths into git for these. A
marks file renamed with `git mv` keeps its history: the reader goes on from
the old name.

The git reads under them answer a fact or raise GitError. `blob_at` answered
None and `commits_touching` answered [] for any git failure, so a clone that
did not hold the baseline read as a history that never held marks, and verify
would judge against no marks at all. Every test here builds a real repo.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from crapkit.errors import GitError
from crapkit.gitio import blob_at, commits_touching
from crapkit.marks_history import marks_history, newest_committed_marks
from crapkit.ratchet import KEY_VERSION, RatchetEntry, dump_ratchet, metric_version
from crapkit.ratchet_report import mark_events, report_from_events

MARKS = "crapkit-ratchet.tsv"
UNHELD = "0123456789abcdef0123456789abcdef01234567"


def git(root: Path, *args: str, date: str = "2026-01-01T12:00:00+00:00") -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                           "-c", "commit.gpgsign=false", *args], cwd=root, check=True,
                          capture_output=True, text=True, env=env).stdout.strip()


def marks(crap: float) -> str:
    entry = RatchetEntry("src/a.py", "hot( n )", crap)
    return dump_ratchet([entry], stamp=metric_version(), key_version=KEY_VERSION)


def commit_file(root: Path, rel: str, text: str | None, message: str, date: str) -> str:
    path = root / rel
    if text is None:
        path.unlink()
    else:
        path.write_text(text, encoding="utf-8", newline="\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message, date=date)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture()
def history(tmp_path: Path) -> dict[str, str]:
    """base (no marks), a 12.0 mark, a 10.0 mark, then the file deleted."""
    git(tmp_path, "init", "-q", "-b", "main")
    shas = {"base": commit_file(tmp_path, "README", "r\n", "base", "2026-01-01T12:00:00+00:00")}
    shas["first"] = commit_file(tmp_path, MARKS, marks(12.0), "seed", "2026-02-01T12:00:00+00:00")
    shas["newer"] = commit_file(tmp_path, MARKS, marks(10.0), "tighten", "2026-03-01T12:00:00+00:00")
    shas["gone"] = commit_file(tmp_path, MARKS, None, "delete", "2026-04-01T12:00:00+00:00")
    shas["root"] = str(tmp_path)
    return shas


# --- the git reads answer a fact or raise --------------------------------------

def test_blob_at_answers_the_bytes_or_none_for_a_commit_without_the_file(history):
    root = Path(history["root"])

    assert blob_at(root, history["newer"], MARKS) == marks(10.0).encode("utf-8")
    assert blob_at(root, history["base"], MARKS) is None
    assert blob_at(root, history["gone"], MARKS) is None


@pytest.mark.parametrize("read", [
    pytest.param(lambda root: blob_at(root, UNHELD, MARKS), id="blob-at-a-commit-not-held"),
    pytest.param(lambda root: commits_touching(root, f"{UNHELD}..HEAD", MARKS),
                 id="commits-in-a-range-not-held"),
])
def test_a_git_read_that_fails_raises_instead_of_answering_nothing(history, read):
    with pytest.raises(GitError, match=UNHELD[:12]):
        read(Path(history["root"]))


def test_commits_touching_lists_the_range_newest_first(history):
    root = Path(history["root"])

    assert commits_touching(root, f"{history['base']}..HEAD", MARKS) == [
        history["gone"], history["newer"], history["first"]]


# --- verify's stand-in: the newest committed marks since the baseline ----------

def test_the_stand_in_is_the_newest_revision_that_held_marks(history):
    commit, committed = newest_committed_marks(Path(history["root"]), history["base"], MARKS)

    assert commit == history["newer"]
    assert [e.crap for e in committed.entries] == [10.0]


def test_no_revision_since_the_baseline_held_marks(history):
    root = Path(history["root"])

    assert newest_committed_marks(root, history["gone"], MARKS) is None


def test_a_baseline_the_clone_does_not_hold_is_named_not_read_as_no_marks(history):
    """Reading it as None let verify judge against no marks at all, the case the
    stand-in exists to stop. The refusal names the file and the commit."""
    with pytest.raises(GitError) as refused:
        newest_committed_marks(Path(history["root"]), UNHELD, MARKS)

    message = str(refused.value)
    assert message.startswith(f"cannot read the history of {MARKS} since the baseline "
                              f"{UNHELD[:11]} to stand in for the missing marks: ")
    assert "--unshallow" not in message, "a full clone is not told to fetch more history"


def test_a_shallow_clone_that_lacks_the_baseline_names_the_fetch(history, tmp_path):
    """The default CI checkout: depth 1 holds HEAD alone, so the baseline and the
    commits that held the marks are not there to read."""
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth", "1", Path(history["root"]).as_uri(), str(shallow))

    with pytest.raises(GitError) as refused:
        newest_committed_marks(shallow, history["first"], MARKS)

    assert str(refused.value).endswith("set fetch-depth: 0 on the checkout or run "
                                       "git fetch --unshallow")


# --- the history ratchet report and brief read ----------------------------------

def test_the_history_starts_at_the_first_commit_that_touched_the_file(history):
    read = marks_history(Path(history["root"]), MARKS)

    assert [ts for ts, _ in read] == sorted(ts for ts, _ in read)
    assert len(read) == 3


def test_a_file_no_commit_touched_has_no_history(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit_file(tmp_path, "README", "r\n", "base", "2026-01-01T12:00:00+00:00")

    assert marks_history(tmp_path, MARKS) == []


# --- a renamed marks file keeps its history -------------------------------------
# The log walks no renames, so `git mv` started the history at the rename: every
# mark entered there, 0 days old, and no earlier repayment counted.

RENAMED = "2026-06-01T12:00:00+00:00"
DAY = 86400


def several(*names: str) -> str:
    entries = [RatchetEntry("src/a.py", f"{name}( n )", 12.0) for name in names]
    return dump_ratchet(entries, stamp=metric_version(), key_version=KEY_VERSION)


def stamp(date: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(date).timestamp())


def test_a_renamed_file_goes_on_from_its_old_name(history):
    """The rename itself changes no mark: it moves the clock and nothing else."""
    root = Path(history["root"])
    commit_file(root, MARKS, marks(9.0), "restore", "2026-05-01T12:00:00+00:00")
    before = marks_history(root, MARKS)
    git(root, "mv", MARKS, "debt.tsv")
    commit_file(root, "README", "r2\n", "rename", RENAMED)

    read = marks_history(root, "debt.tsv")

    assert read == [*before, (stamp(RENAMED), "")]
    report = report_from_events(mark_events(read))
    assert (report["dropped_total"], [e["age_days"] for e in report["oldest"]]) == (1, [31])


def test_a_rename_that_edits_the_file_reads_as_the_marks_it_changed(tmp_path):
    """git pairs a rename that keeps most lines; the commit that also repaid a
    mark reads as that repayment, and the marks it kept keep their age."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit_file(tmp_path, MARKS, several("a", "b", "c", "d"), "seed", "2026-01-01T12:00:00+00:00")
    git(tmp_path, "mv", MARKS, "debt.tsv")
    commit_file(tmp_path, "debt.tsv", several("a", "b", "c"), "rename and repay d", RENAMED)

    read = marks_history(tmp_path, "debt.tsv")

    assert len(read) == 2 and [line[0] for line in read[1][1].splitlines()] == ["-"]
    report = report_from_events(mark_events(read))
    assert (report["open"], report["dropped_total"]) == (3, 1)
    assert {e["age_days"] for e in report["oldest"]} == {(stamp(RENAMED) - stamp("2026-01-01T12:00:00+00:00")) // DAY}


def test_every_rename_is_followed_back_to_the_first_name(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit_file(tmp_path, "a.tsv", several("a", "b"), "seed", "2026-01-01T12:00:00+00:00")
    commit_file(tmp_path, "a.tsv", several("a"), "repay b", "2026-02-01T12:00:00+00:00")
    git(tmp_path, "mv", "a.tsv", "b.tsv")
    commit_file(tmp_path, "README", "r\n", "first rename", "2026-03-01T12:00:00+00:00")
    git(tmp_path, "mv", "b.tsv", "c.tsv")
    commit_file(tmp_path, "README", "r2\n", "second rename", RENAMED)

    read = marks_history(tmp_path, "c.tsv")

    assert [patch == "" for _, patch in read] == [False, False, True, True]
    report = report_from_events(mark_events(read))
    assert (report["dropped_total"], report["oldest"][0]["age_days"]) == (1, 151)


def test_a_rewrite_git_cannot_pair_starts_the_history_there(tmp_path):
    """A new name that shares no line with the old file is no rename to git:
    the history starts where the new file does, as a file created there."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit_file(tmp_path, MARKS, several("a", "b"), "seed", "2026-01-01T12:00:00+00:00")
    (tmp_path / MARKS).unlink()
    commit_file(tmp_path, "debt.tsv", several(*(f"k{i}" for i in range(10))),
                "rewrite under a new name", RENAMED)

    read = marks_history(tmp_path, "debt.tsv")

    assert len(read) == 1 and read[0][0] == stamp(RENAMED)


def test_a_depth_one_clone_of_a_renamed_file_reads_its_one_commit(history, tmp_path):
    """The clone's one commit has no parent to rename from, so the history
    is that commit; ratchet report says the clone is shallow."""
    root = Path(history["root"])
    commit_file(root, MARKS, marks(9.0), "restore", "2026-05-01T12:00:00+00:00")
    git(root, "mv", MARKS, "debt.tsv")
    commit_file(root, "README", "r2\n", "rename", RENAMED)
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth", "1", root.as_uri(), str(shallow))

    read = marks_history(shallow, "debt.tsv")

    report = report_from_events(mark_events(read))
    assert len(read) == 1 and report["oldest"][0]["age_days"] == 0


# A past revision reads by the marks file's own rule and is never refused:
# the file it came from may be gone, so "save it as UTF-8" names nothing the
# user can open. The newest revision here holds the 10.0 mark in each encoding.
PAST_ENCODINGS = [
    pytest.param(lambda text: b"\xff\xfe" + text.encode("utf-16-le"), id="utf16-le-out-file"),
    pytest.param(lambda text: b"\xfe\xff" + text.encode("utf-16-be"), id="utf16-be"),
    pytest.param(lambda text: text.encode("utf-8") + b"# caf\xe9\n", id="cp1252-byte"),
    pytest.param(lambda text: b"\xef\xbb\xbf" + text.encode("utf-8"), id="utf8-bom"),
]


@pytest.mark.parametrize("saved", PAST_ENCODINGS)
def test_the_stand_in_reads_a_revision_in_any_encoding_the_marks_file_takes(tmp_path, saved):
    git(tmp_path, "init", "-q", "-b", "main")
    base = commit_file(tmp_path, "README", "r\n", "base", "2026-01-01T12:00:00+00:00")
    (tmp_path / MARKS).write_bytes(saved(marks(10.0)))
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "saved elsewhere", date="2026-02-01T12:00:00+00:00")
    commit_file(tmp_path, MARKS, None, "delete", "2026-03-01T12:00:00+00:00")

    commit, committed = newest_committed_marks(tmp_path, base, MARKS)

    assert commit == git(tmp_path, "rev-parse", "HEAD~1")
    assert [(e.path, e.long_name, e.crap) for e in committed.entries] == [("src/a.py", "hot( n )", 10.0)]


# --- one rule for a revision that held marks -------------------------------------
# The working tree reads as blank through repotext.marks_text, which drops a
# byte-order mark. The stand-in tested a past revision's raw bytes, so a revision
# holding a byte-order mark and blank lines held marks to it: verify judged a
# blank file against that revision's no marks, the case the stand-in exists for.

BLANK_REVISIONS = [
    pytest.param(b"\xef\xbb\xbf\n", id="utf8-bom"),
    pytest.param(b"\xff\xfe" + "\n\n".encode("utf-16-le"), id="utf16-le-bom"),
]


@pytest.mark.parametrize("blank", BLANK_REVISIONS)
def test_a_revision_of_a_byte_order_mark_and_blank_lines_holds_no_marks(history, blank):
    root = Path(history["root"])
    (root / MARKS).write_bytes(blank)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "blank again", date="2026-05-01T12:00:00+00:00")

    commit, committed = newest_committed_marks(root, history["base"], MARKS)

    assert commit == history["newer"]
    assert [e.crap for e in committed.entries] == [10.0]


def test_the_held_history_ticks_every_commit_after_the_newest_that_held_marks(history):
    """ratchet report replays a blank or missing marks file from here: the
    deletion moves the clock and repays nothing."""
    from crapkit.marks_history import held_history

    root = Path(history["root"])
    full = marks_history(root, MARKS)

    assert held_history(root, MARKS) == [*full[:2], (full[2][0], "")]
    assert held_history(root, "never.tsv") == []
