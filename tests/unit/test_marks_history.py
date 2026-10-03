"""The marks file's history, read in one place: `marks_history`.

ratchet report and brief read mark ages off the file's commits, and verify
judges a deleted or emptied marks file against the newest marks a commit since
the baseline held. Every reader goes through one list of revisions, each read
whole under the past-revision rule (RatchetFile.committed): no reader parses
patch lines. A marks file renamed with `git mv` keeps its history: the reader
goes on from the old name.

The git reads under them answer a fact or raise GitError. `blob_at` answered
None for any git failure, so a clone that did not hold the baseline read as a
history that never held marks, and verify would judge against no marks at
all. Every test here builds a real repo.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

import test_ratchet_report_counts_a_deleted_marks_file as deleted_file
import test_ratchet_report_reads_a_merged_history as merged_history
from cli_inproc_repo import add_knotty, commit_all, repo, seed_artifacts, template_repo  # noqa: F401
from crapkit import gitio
from crapkit.cli import main
from crapkit.errors import GitError
from crapkit.gitio import blob_at, file_revisions
from crapkit.marks_history import MarksRevision, held_history, marks_history, newest_committed_marks
from crapkit.ratchet import KEY_VERSION, RatchetEntry, dump_ratchet, metric_version, read_ratchet
from crapkit.ratchet_report import crap_by_key, held_event, mark_events, report_from_events
from crapkit.ratchetfile import RatchetFile
from crapkit.records import record_lines
from crapkit.repotext import lenient, marks_text
from raw_git import commit as raw_commit
from raw_git import repository

MARKS = "crapkit-ratchet.tsv"
UNHELD = "0123456789abcdef0123456789abcdef01234567"
DAY = 86400


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
    pytest.param(lambda root: file_revisions(root, MARKS, f"{UNHELD}..HEAD"),
                 id="revisions-in-a-range-not-held"),
])
def test_a_git_read_that_fails_raises_instead_of_answering_nothing(history, read):
    with pytest.raises(GitError, match=UNHELD[:12]):
        read(Path(history["root"]))


def test_file_revisions_lists_the_range_oldest_first_with_each_commits_bytes(history):
    """The deleting commit holds no bytes, and each commit carries the bytes
    its parent held: the revision it changed."""
    root = Path(history["root"])

    read = file_revisions(root, MARKS, f"{history['base']}..HEAD")

    assert [r.commit for r in read] == [history["first"], history["newer"], history["gone"]]
    assert [r.data for r in read] == [marks(12.0).encode(), marks(10.0).encode(), None]
    assert [r.before for r in read] == [None, marks(12.0).encode(), marks(10.0).encode()]
    assert [r.merge for r in read] == [False, False, False]
    assert file_revisions(root, MARKS) == read, "no range is all of HEAD's history"


@pytest.mark.parametrize("length", [1, 3, 7])
def test_file_revisions_starts_two_processes_for_a_history_of_any_length(tmp_path, monkeypatch,
                                                                         length):
    """One `git log` with no patch text, one `git cat-file --batch` for every blob."""
    git(tmp_path, "init", "-q", "-b", "main")
    for day in range(length):
        commit_file(tmp_path, MARKS, marks(30.0 - day), f"mark {day}",
                    f"2026-01-{day + 1:02d}T12:00:00+00:00")
    commit_file(tmp_path, MARKS, None, "delete", "2026-02-01T12:00:00+00:00")
    started = []
    real = subprocess.run
    monkeypatch.setattr(gitio.subprocess, "run",
                        lambda argv, *a, **k: started.append(argv) or real(argv, *a, **k))

    read = file_revisions(tmp_path, MARKS)

    assert len(read) == length + 1 and read[-1].data is None
    assert len(started) == 2
    assert "log" in started[0] and "-p" not in started[0]
    assert started[1][1:] == ["cat-file", "--batch"]


def test_a_history_a_depth_one_clone_does_not_hold_names_the_fetch(history, tmp_path):
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth", "1", Path(history["root"]).as_uri(), str(shallow))

    with pytest.raises(GitError) as refused:
        file_revisions(shallow, MARKS, f"{history['first']}..HEAD")

    assert gitio.shallow_fix(shallow).endswith("git fetch --unshallow")
    assert str(refused.value).endswith(gitio.shallow_fix(shallow))


# --- verify's stand-in: the newest committed marks since the baseline ----------

def test_the_stand_in_is_the_newest_revision_that_held_marks(history):
    commit, committed = newest_committed_marks(Path(history["root"]), history["base"], MARKS)

    assert commit == history["newer"]
    assert [e.crap for e in committed.entries] == [10.0]


def test_no_revision_since_the_baseline_held_marks(history):
    root = Path(history["root"])

    assert newest_committed_marks(root, history["gone"], MARKS) is None


def test_the_stand_in_reads_the_baseline_when_nothing_since_held_marks(history):
    root = Path(history["root"])
    git(root, "reset", "-q", "--hard", history["newer"])

    commit, committed = newest_committed_marks(root, history["newer"], MARKS)

    assert commit == history["newer"] and [e.crap for e in committed.entries] == [10.0]


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

    message = str(refused.value)
    assert message.endswith("set fetch-depth: 0 on the checkout or run git fetch --unshallow")
    assert message.count("--unshallow") == 1


# --- the history ratchet report and brief read ----------------------------------

def test_the_history_starts_at_the_first_commit_that_touched_the_file(history):
    read = marks_history(Path(history["root"]), MARKS)

    assert [r.time for r in read] == sorted(r.time for r in read)
    assert [r.commit for r in read] == [history["first"], history["newer"], history["gone"]]
    assert [r.marks for r in read] == [{("src/a.py", "hot( n )"): 12.0},
                                       {("src/a.py", "hot( n )"): 10.0}, None]


def test_a_file_no_commit_touched_has_no_history(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit_file(tmp_path, "README", "r\n", "base", "2026-01-01T12:00:00+00:00")

    assert marks_history(tmp_path, MARKS) == []


# --- one history, every way a past revision was saved --------------------------
# One raw_git history: a UTF-8 revision, a cp1252 byte in one mark's name, a
# PowerShell 5.1 UTF-16 LE resave (byte-order mark, CRLF) that also adds a
# mark, a `git mv` of the marks file, a re-key commit, a commit that tightens
# one value and repays one mark, and one that adds that mark again. Ages count
# from the newest commit, 10 days ago.

HEADER = b"# crapkit-analysis=11 lizard=1.24.0\n# crapkit-keys=1\npath\tlong_name\tcrap\n"
OLD = "old-marks.tsv"
CAFE = ("src/a.py", "caf�( n )")


def rows(*marked: tuple[str, float]) -> bytes:
    return HEADER + b"".join(b"src/a.py\t%s\t%.1f\n" % (name.encode("utf-8"), crap)
                             for name, crap in marked)


def key(name: str) -> tuple[str, str]:
    return ("src/a.py", name)


BASE = (("a( n )", 20.0), ("b( n )", 20.0), ("c( n )", 20.0))
WITH_CAFE = rows(*BASE) + b"src/a.py\tcaf\xe9( n )\t5.0\n"
UTF16 = b"\xff\xfe" + (WITH_CAFE.decode("utf-8", "replace")
                       + "src/a.py\td( n )\t12.0\n").replace("\n", "\r\n").encode("utf-16-le")
REKEYED = (("a( n )", 20.0), ("b( n )#2", 20.0), ("c( n )", 20.0), ("caf�( n )", 5.0),
           ("d( n )", 12.0))
TIGHTENED = tuple((name, 10.0 if name == "a( n )" else crap) for name, crap in REKEYED
                  if name != "c( n )")


@pytest.fixture()
def saved_every_way(tmp_path: Path) -> Path:
    root = repository(tmp_path / "every-way")
    raw_commit(root, files={OLD.encode(): rows(*BASE)}, age_days=100)
    raw_commit(root, files={OLD.encode(): WITH_CAFE}, age_days=80)
    raw_commit(root, files={OLD.encode(): UTF16}, age_days=60)
    raw_commit(root, files={MARKS.encode(): UTF16}, deletes=(OLD.encode(),), age_days=40)
    raw_commit(root, files={MARKS.encode(): rows(*REKEYED)}, age_days=30)
    raw_commit(root, files={MARKS.encode(): rows(*TIGHTENED)}, age_days=20)
    raw_commit(root, files={MARKS.encode(): rows(*TIGHTENED, ("c( n )", 20.0))}, age_days=10)
    return root


def test_every_revision_parses_whole_and_nothing_refuses(saved_every_way):
    read = marks_history(saved_every_way, MARKS)

    assert len(read) == 7, "the history goes on through the git mv"
    assert read[1].marks[CAFE] == 5.0, "the cp1252 byte reads as U+FFFD in that one name"
    assert read[2].marks[key("d( n )")] == 12.0, "the UTF-16 revision parses"
    assert read[2].before == read[1].marks
    assert read[3].marks == read[3].before == read[2].marks, "the rename changes no mark"


def test_every_mark_keeps_its_entry_date_and_every_repayment_counts(saved_every_way):
    report = report_from_events(mark_events(marks_history(saved_every_way, MARKS)))

    assert {row["long_name"]: row["age_days"] for row in report["oldest"]} == {
        "a( n )": 90,          # entered on day 100, tightened on day 20: same entry date
        CAFE[1]: 70,           # entered in the cp1252 revision
        "d( n )": 50,          # entered in the UTF-16 revision
        "b( n )#2": 20,        # the re-key enters the new key
        "c( n )": 0,           # repaid on day 20, added again on day 10
    }
    assert report["dropped_total"] == 2, "b( n ) at the re-key, c( n ) on day 20"


def test_the_events_say_what_each_revision_changed(saved_every_way):
    events = mark_events(marks_history(saved_every_way, MARKS))

    assert [(kind, k) for _, k, kind, _ in events] == [
        ("added", key("a( n )")), ("added", key("b( n )")), ("added", key("c( n )")),
        ("added", CAFE),
        ("added", key("d( n )")),
        ("observed", None),
        ("added", key("b( n )#2")), ("dropped", key("b( n )")),
        ("updated", key("a( n )")), ("dropped", key("c( n )")),
        ("added", key("c( n )")),
    ]


def test_a_legacy_bare_twin_key_reads_as_twin_one_as_the_mark_index_does(saved_every_way):
    """The first of a name keeps the bare key, so a mark written before the
    ordinal is twin #1: the revision reads it as keys.MarkIndex does."""
    read = marks_history(saved_every_way, MARKS)

    assert read[4].marks == crap_by_key(read_ratchet(lenient(rows(*REKEYED)))[0])
    assert {key("b( n )#2"), key("a( n )")} <= set(read[4].marks)


def test_a_key_a_revision_lists_twice_reads_its_first_mark(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / MARKS).write_bytes(rows(("a( n )", 9.0), ("a( n )", 30.0)))
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "twice")

    assert marks_history(tmp_path, MARKS)[0].marks == {key("a( n )"): 9.0}


# --- check 3: the 0.8.1 tree's replay of patch lines, frozen for this slot -------
# A frozen copy of the 0.8.1 tree's read: marks_history's `git log -p -U0` of
# the file, its walk back through every `git mv` (_segment, _renamed_from,
# _rename_patch), held_history and cli/ratchet_cmds' _with_head_revision, and
# ratchet_report's mark_events over those patches. It calls no crapkit git
# read. On every UTF-8 history the unit and e2e suites build, the new rule over
# revisions gives the same events and the same report; the one place the rules
# part is a resave that changes only line endings (C36).

_FROZEN_HEADER = re.compile(rb"^\0(-?\d+) ([0-9a-f]+)\n", re.MULTILINE)


def _frozen_git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-c", "diff.relative=true", "-c", "log.showRoot=true",
                           "--literal-pathspecs", *args], cwd=root, capture_output=True,
                          check=True).stdout


def _frozen_log(root: Path, rel: str, rev: str | None) -> list[tuple[int, str, str]]:
    """0.8.1's gitio.file_log: (timestamp, commit, -U0 patch) per commit, oldest first."""
    out = _frozen_git(root, "log", "--reverse", "--format=%x00%at %H", "-p", "-U0",
                      "--no-renames", "--no-color", "--no-ext-diff", "--no-textconv", "--text",
                      *([rev] if rev else []), "--", rel)
    heads = list(_FROZEN_HEADER.finditer(out))
    ends = [head.start() for head in heads[1:]] + [len(out)]
    return [(int(head[1]), head[2].decode(), lenient(out[head.end():end]))
            for head, end in zip(heads, ends)]


def _frozen_renamed_from(root: Path, commit: str, path: str) -> str | None:
    """0.8.1's _renamed_from over gitio.commit_renames: one commit's diff-tree -M."""
    fields = lenient(_frozen_git(root, "diff-tree", "-r", "-M", "--relative", "--name-status",
                                 "--no-commit-id", "-z", commit)).split("\0")
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        if status[0] == "R" and fields[index + 2] == path:
            return fields[index + 1]
        index += 3 if status[0] in "RC" else 2
    return None


def _frozen_blob(root: Path, commit: str, path: str) -> bytes | None:
    done = subprocess.run(["git", "cat-file", "blob", f"{commit}:{path}"], cwd=root,
                          capture_output=True)
    return done.stdout if done.returncode == 0 else None


def _frozen_rename_patch(before: bytes, after: bytes) -> str:
    """0.8.1's gitio.revisions_patch: the lines one revision holds and the other does not."""
    old, new = (list(record_lines(marks_text(side))) for side in (before, after))
    return "\n".join([f"-{line}" for line in old if line not in set(new)]
                     + [f"+{line}" for line in new if line not in set(old)])


def _frozen_history(root: Path, rel: str) -> list[tuple[int, tuple[str, str], str]]:
    """0.8.1's marks_history._history: (timestamp, (commit, path), patch) per
    commit, oldest first, back through every rename."""
    entries: list = []
    path, rev = rel, None
    while path is not None:
        log = _frozen_log(root, path, rev)
        old = _frozen_renamed_from(root, log[0][1], path) if log else None
        segment = [(ts, (commit, path), patch) for ts, commit, patch in log]
        if old is not None:
            ts, commit, _ = log[0]
            segment[0] = (ts, (commit, path), _frozen_rename_patch(
                _frozen_blob(root, f"{commit}^", old) or b"", _frozen_blob(root, commit, path) or b""))
        entries[:0] = segment
        path, rev = old, (f"{log[0][1]}^" if old is not None else None)
    return entries


def _frozen_patches(root: Path, rel: str) -> list[tuple[int, str]]:
    """0.8.1's marks_history.marks_history."""
    return [(ts, patch) for ts, _, patch in _frozen_history(root, rel)]


def _frozen_held(root: Path, commit: str, path: str) -> str | None:
    """0.8.1's marks_history._held: the text a revision holds, None when blank or missing."""
    data = _frozen_blob(root, commit, path)
    held = None if data is None else RatchetFile.committed(root / path, data)
    return None if held is None or held.blank else held.text


def _frozen_report_entries(root: Path, rel: str) -> list[tuple]:
    """0.8.1's cli/ratchet_cmds._report_basis: held_history for a missing or
    blank marks file, else the history with HEAD's text on its newest entry."""
    history = _frozen_history(root, rel)
    if not RatchetFile.read(root / rel).blank:
        entries = [(ts, patch) for ts, _, patch in history]
        text = _frozen_held(root, "HEAD", rel) if entries else None
        return entries if text is None else entries[:-1] + [entries[-1] + (text,)]
    held = [(index, _frozen_held(root, *history[index][1]))
            for index in range(len(history) - 1, -1, -1)]
    last, text = next(((index, text) for index, text in held if text is not None), (-1, None))
    entries = [(ts, patch if index <= last else "") for index, (ts, _, patch) in enumerate(history)]
    if text is not None:
        entries[last] += (text,)
    return entries


def _frozen_row(line: str) -> tuple | None:
    body = line[1:]
    if body.startswith(("++ ", "-- ")):
        return None
    entries = read_ratchet(body)[0]
    return ((entries[0].path, entries[0].long_name), entries[0].crap) if entries else None


def _frozen_mark_events(patches: list[tuple]) -> list[tuple]:
    """0.8.1's ratchet_report.mark_events: (ts, patch, *held texts) entries."""
    events = []
    for ts, patch, *texts in patches:
        added, removed = {}, {}
        for line in (line.removesuffix("\r") for line in patch.split("\n")):
            row = _frozen_row(line) if line[:1] in ("+", "-") else None
            if row is not None:
                (added if line[0] == "+" else removed)[row[0]] = row[1]
        changed = [(ts, k, "updated" if k in removed else "added", added[k]) for k in sorted(added)]
        changed += [(ts, k, "dropped", removed[k]) for k in sorted(set(removed) - set(added))]
        events += changed or [(ts, None, "observed", 0.0)]
        events += [(ts, None, "held", crap_by_key(read_ratchet(text)[0])) for text in texts]
    return events


def _same_events_both_ways(root: Path, rel: str = MARKS) -> list[tuple]:
    """The replay of every revision gives the 0.8.1 events, and so does the
    report path: `ratchet report`'s revisions against 0.8.1's report entries
    (held_history or HEAD's text). The one "held" event carries the same
    marks; 0.8.1 dated it at the newest commit that held marks and the new
    rule at the newest commit, which moves no report number, so the report
    itself is compared whole. Answers the replay's events."""
    from crapkit.cli.ratchet_cmds import _report_basis

    replayed = mark_events(marks_history(root, rel))
    assert replayed == _frozen_mark_events(_frozen_patches(root, rel))
    revisions, working = _report_basis(root, rel)
    new = mark_events(revisions) + held_event(revisions)
    old = _frozen_mark_events(_frozen_report_entries(root, rel))
    assert [e for e in new if e[2] != "held"] == [e for e in old if e[2] != "held"]
    assert [e[3] for e in new if e[2] == "held"] == [e[3] for e in old if e[2] == "held"]
    assert report_from_events(new, working=working) == report_from_events(old, working=working)
    return replayed


def _commit_bytes(root: Path, texts: list[bytes]) -> None:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "core.autocrlf", "false")
    for day, text in enumerate(texts):
        (root / MARKS).write_bytes(text)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", f"step {day}", date=f"2026-01-{day + 1:02d}T12:00:00+00:00")


def test_the_new_rule_gives_the_0_8_1_events_on_a_linear_history(history):
    assert len(_same_events_both_ways(Path(history["root"]))) == 3


def test_the_new_rule_gives_the_0_8_1_events_on_a_crlf_history(tmp_path):
    crlf = [rows(*marked).replace(b"\n", b"\r\n") for marked in
            (BASE, (("a( n )", 10.0), ("c( n )", 20.0)),
             (("a( n )", 10.0), ("b( n )", 3.0), ("c( n )", 20.0)))]
    _commit_bytes(tmp_path, crlf)

    events = _same_events_both_ways(tmp_path)

    assert [kind for _, _, kind, _ in events] == ["added"] * 3 + ["updated", "dropped", "added"]


def test_the_new_rule_gives_the_0_8_1_events_on_record_v1_rows(tmp_path):
    """Rows the portable record encoding writes as `@crapkit-record-v1` lines."""
    names = ["#src/a.py", "src/a b.py"]
    _commit_bytes(tmp_path, [dump_ratchet([RatchetEntry(path, "f( )", 12.0)], stamp="").encode()
                             for path in names])

    events = _same_events_both_ways(tmp_path)

    assert "@crapkit-record-v1" in (tmp_path / MARKS).read_text(encoding="utf-8")
    assert [(k, kind) for _, k, kind, _ in events] == [
        (("#src/a.py", "f( )"), "added"), (("src/a b.py", "f( )"), "added"),
        (("#src/a.py", "f( )"), "dropped")]


def test_a_resave_that_changes_only_line_endings_moves_only_the_clock(tmp_path):
    """The one place the rules part: the patch read a CRLF resave as each row
    removed and added at the same value, an update that kept every entry date.
    A whole revision reads it as no mark change. The report is the same."""
    _commit_bytes(tmp_path, [rows(*BASE), rows(*BASE).replace(b"\n", b"\r\n")])

    new = mark_events(marks_history(tmp_path, MARKS))
    frozen = _frozen_mark_events(_frozen_patches(tmp_path, MARKS))

    assert [kind for _, _, kind, _ in new[3:]] == ["observed"]
    assert [kind for _, _, kind, _ in frozen[3:]] == ["updated"] * 3
    assert report_from_events(new) == report_from_events(frozen)


def _walk_equals_the_report_at_every_commit(root: Path) -> int:
    """Check 1 at each commit of `root`'s history, checked out in turn: the
    report the new events give equals oracles/marks_history_walk.py's, which
    reads every committed version whole with no crapkit and diffs the mark
    sets itself. Check 3 holds there too. Answers the commits checked."""
    from accuracy.history_oracles.oracles import marks_history_walk

    shas = git(root, "rev-list", "--reverse", "HEAD").split()
    for sha in shas:
        git(root, "checkout", "-q", sha)
        disk = lenient((root / MARKS).read_bytes())
        events = mark_events(marks_history(root, MARKS))
        report = report_from_events(events, working=crap_by_key(read_ratchet(disk)[0]))
        assert report == marks_history_walk.report(root, MARKS), sha
        _same_events_both_ways(root)
    return len(shas)


def test_the_history_walk_oracle_equals_the_report_at_every_commit(tmp_path):
    """A history that tightens, repays, empties and restores."""
    steps = [rows(*BASE), rows(("a( n )", 10.0), ("b( n )", 20.0), ("c( n )", 20.0)),
             rows(("a( n )", 10.0), ("c( n )", 20.0)), b"", rows(("a( n )", 10.0)),
             rows(("a( n )", 10.0), ("b( n )", 4.0))]
    _commit_bytes(tmp_path, steps)

    assert _walk_equals_the_report_at_every_commit(tmp_path) == len(steps)


@pytest.mark.parametrize("name, commits", [("BURN", 6), ("BURN_TIGHTEN", 3)])
def test_the_history_walk_oracle_equals_the_report_on_every_history_spec(tmp_path, name,
                                                                          commits):
    """acc-history-oracles' specs, built as test_burn_down.py builds them, met
    at every commit and not only at HEAD."""
    from accuracy.history_oracles.repos import history_specs
    from accuracy.kit import repos

    built = repos.build(getattr(history_specs, name), tmp_path / name)

    assert _walk_equals_the_report_at_every_commit(built.root) == commits


def _merged(root: Path, base: list[str], side_a: list[str], side_b: list[str]) -> None:
    """`base`, a branch to `side_a` and one to `side_b`, merged back with --no-ff."""
    def write(names: list[str], message: str, day: int) -> None:
        entries = [RatchetEntry("src/app.ts", name, 20.0) for name in names]
        commit_file(root, MARKS, dump_ratchet(entries, stamp=metric_version()), message,
                    f"2026-01-{day:02d}T12:00:00+00:00")

    git(root, "init", "-q", "-b", "main")
    write(base, "base", 1)
    git(root, "checkout", "-q", "-b", "a")
    write(side_a, "side a", 2)
    git(root, "checkout", "-q", "-b", "b", "main")
    write(side_b, "side b", 3)
    git(root, "checkout", "-q", "main")
    for day, branch in ((4, "a"), (5, "b")):
        git(root, "merge", "-q", "--no-ff", "-m", f"merge {branch}", branch,
            date=f"2026-01-{day:02d}T12:00:00+00:00")


def test_the_new_rule_gives_the_0_8_1_events_on_a_merged_history(tmp_path):
    """Both branches repaid k1, and the merge that joins them shows no change:
    k1 repaid once, the merge a clock tick, and its own revision the marks it
    kept."""
    _merged(tmp_path, ["k1", "k2", "m1", "m3"], ["k2", "m1", "m2", "m3"], ["k2", "m1", "m3", "m4"])

    events = _same_events_both_ways(tmp_path)

    report = report_from_events(events)
    assert (report["dropped_total"], report["open"]) == (1, 5)
    revisions = file_revisions(tmp_path, MARKS)
    assert (revisions[-1].merge, revisions[-1].before) == (True, None)
    assert revisions[-1].data == blob_at(tmp_path, "HEAD", MARKS)
    assert marks_history(tmp_path, MARKS)[-1].marks == {
        ("src/app.ts", name): 20.0 for name in ("k2", "m1", "m2", "m3", "m4")}


# --- a renamed marks file keeps its history -------------------------------------
# The log walks no renames, so `git mv` started the history at the rename: every
# mark entered there, 0 days old, and no earlier repayment counted.

RENAMED = "2026-06-01T12:00:00+00:00"


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

    assert read[:-1] == before
    assert (read[-1].time, read[-1].marks, read[-1].before) == (
        stamp(RENAMED), before[-1].marks, before[-1].marks)
    report = report_from_events(mark_events(read))
    assert (report["dropped_total"], [e["age_days"] for e in report["oldest"]]) == (1, [31])
    assert _same_events_both_ways(root, "debt.tsv") == mark_events(read)


def test_a_rename_that_edits_the_file_reads_as_the_marks_it_changed(tmp_path):
    """git pairs a rename that keeps most lines; the commit that also repaid a
    mark reads as that repayment, and the marks it kept keep their age."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit_file(tmp_path, MARKS, several("a", "b", "c", "d"), "seed", "2026-01-01T12:00:00+00:00")
    git(tmp_path, "mv", MARKS, "debt.tsv")
    commit_file(tmp_path, "debt.tsv", several("a", "b", "c"), "rename and repay d", RENAMED)

    read = marks_history(tmp_path, "debt.tsv")

    assert len(read) == 2
    assert [(kind, k) for _, k, kind, _ in mark_events(read[1:])] == [
        ("dropped", ("src/a.py", "d( n )"))]
    report = report_from_events(mark_events(read))
    assert (report["open"], report["dropped_total"]) == (3, 1)
    assert {e["age_days"] for e in report["oldest"]} == {(stamp(RENAMED) - stamp("2026-01-01T12:00:00+00:00")) // DAY}
    assert _same_events_both_ways(tmp_path, "debt.tsv") == mark_events(read)


def test_every_rename_is_followed_back_to_the_first_name(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit_file(tmp_path, "a.tsv", several("a", "b"), "seed", "2026-01-01T12:00:00+00:00")
    commit_file(tmp_path, "a.tsv", several("a"), "repay b", "2026-02-01T12:00:00+00:00")
    git(tmp_path, "mv", "a.tsv", "b.tsv")
    commit_file(tmp_path, "README", "r\n", "first rename", "2026-03-01T12:00:00+00:00")
    git(tmp_path, "mv", "b.tsv", "c.tsv")
    commit_file(tmp_path, "README", "r2\n", "second rename", RENAMED)

    read = marks_history(tmp_path, "c.tsv")

    assert [r.marks == r.before for r in read] == [False, False, True, True]
    report = report_from_events(mark_events(read))
    assert (report["dropped_total"], report["oldest"][0]["age_days"]) == (1, 151)
    assert _same_events_both_ways(tmp_path, "c.tsv") == mark_events(read)


def test_a_rewrite_git_cannot_pair_starts_the_history_there(tmp_path):
    """A new name that shares no line with the old file is no rename to git:
    the history starts where the new file does, as a file created there."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit_file(tmp_path, MARKS, several("a", "b"), "seed", "2026-01-01T12:00:00+00:00")
    (tmp_path / MARKS).unlink()
    commit_file(tmp_path, "debt.tsv", several(*(f"k{i}" for i in range(10))),
                "rewrite under a new name", RENAMED)

    read = marks_history(tmp_path, "debt.tsv")

    assert len(read) == 1 and read[0].time == stamp(RENAMED) and read[0].before is None
    assert _same_events_both_ways(tmp_path, "debt.tsv") == mark_events(read)


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
    assert _same_events_both_ways(shallow, "debt.tsv") == mark_events(read)


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
    assert marks_history(root, MARKS)[-1].marks is None


def test_the_held_history_ticks_every_commit_after_the_newest_that_held_marks(history):
    """ratchet report replays a blank or missing marks file from here: the
    deletion moves the clock and repays nothing, and the newest revision that
    held marks says which are open."""
    root = Path(history["root"])
    full = marks_history(root, MARKS)

    held = full[1].marks
    assert held_history(root, MARKS) == [full[0], full[1],
                                         full[2]._replace(marks=held, before=held)]
    assert held_event(held_history(root, MARKS)) == [(full[2].time, None, "held", held)]
    assert held_history(root, "never.tsv") == []
    assert _same_events_both_ways(root) == mark_events(full)


def test_a_history_that_never_held_marks_reads_as_clock_ticks(tmp_path):
    _commit_bytes(tmp_path, [b"\n", b"\xef\xbb\xbf\n"])

    read = held_history(tmp_path, MARKS)

    assert [(r.marks, r.before) for r in read] == [(None, None), (None, None)]
    assert held_event(read) == []
    assert report_from_events(mark_events(read))["open"] == 0


def test_the_held_revision_decides_the_open_marks_and_the_replay_their_ages(history):
    """The replay below opens `gone( n )`, which the held revision does not
    hold: a merge kept a repayment no revision diff shows. The open marks are
    the revision's, and the one the replay also opened keeps its age."""
    root = Path(history["root"])
    read = held_history(root, MARKS)
    reopened = MarksRevision("x", read[0].time, {("src/a.py", "gone( n )"): 30.0}, None)

    report = report_from_events(mark_events([reopened, *read]) + held_event(read))

    assert [(e["long_name"], e["age_days"]) for e in report["oldest"]] == [("hot( n )", 59)]
    assert (report["open"], report["dropped_total"]) == (1, 0)


# --- check 3 on the histories the other unit and e2e files build -----------------
# Each history is built by the helpers of the file that pins its report, so the
# frozen 0.8.1 read meets the same commits those tests read through the CLI.

def _four_marks(root: Path) -> None:
    deleted_file._write_marks(root, deleted_file.NAMES)
    commit_all(root, "four marks")


def _repaid_then_deleted(root: Path) -> None:
    _four_marks(root)
    deleted_file._write_marks(root, deleted_file.NAMES[:3])
    commit_all(root, "repay knotty")
    deleted_file._delete(root)


def _stamp_and_no_rows(root: Path) -> None:
    _four_marks(root)
    deleted_file._write_marks(root, ())
    commit_all(root, "every mark repaid")


DELETED_FILE_HISTORIES = [
    pytest.param(lambda root: (_four_marks(root), deleted_file._delete(root)), id="deleted"),
    pytest.param(lambda root: (_four_marks(root), deleted_file._empty(root)), id="emptied"),
    pytest.param(lambda root: (_four_marks(root), (root / MARKS).unlink()),
                 id="deleted-uncommitted"),
    pytest.param(_repaid_then_deleted, id="repaid-then-deleted"),
    pytest.param(_stamp_and_no_rows, id="stamp-and-no-rows"),
    pytest.param(deleted_file._mark_plain_then_delete, id="plain-then-deleted"),
]


@pytest.mark.parametrize("build", DELETED_FILE_HISTORIES)
def test_the_0_8_1_events_on_the_deleted_marks_file_histories(repo, build):  # noqa: F811
    """test_ratchet_report_counts_a_deleted_marks_file.py's histories."""
    build(repo)

    assert _same_events_both_ways(repo)


MERGED_HISTORIES = [
    pytest.param(merged_history._k(1, 2, 3, 4, 5, 6, 7, 8),
                 merged_history._k(1, 3, 4, 5, 6, 7, 8, 9),
                 merged_history._k(1, 2, 3, 4, 6, 7, 8, 9), id="both-added"),
    pytest.param(merged_history._k(1, 2, 3, 4, 5, 6, 7, 8) + merged_history._names("m1", "m3"),
                 merged_history._names("m1", "m2", "m3"),
                 merged_history._names("m1", "m3", "m4"), id="both-pruned"),
    pytest.param(merged_history._k(1, 2, 3, 4) + merged_history._names("m1", "m3"),
                 merged_history._k(2, 3, 4) + merged_history._names("m1", "m2", "m3"),
                 merged_history._k(2, 3, 4) + merged_history._names("m1", "m3", "m4"),
                 id="both-repaid"),
]


@pytest.mark.parametrize("base, side_a, side_b", MERGED_HISTORIES)
def test_the_0_8_1_events_on_the_merged_histories(repo, base, side_a, side_b):  # noqa: F811
    """test_ratchet_report_reads_a_merged_history.py's two-branch histories,
    before and after the marks file is deleted."""
    merged_history._two_branches(repo, base, side_a=side_a, side_b=side_b)
    assert _same_events_both_ways(repo)

    merged_history._delete(repo)

    assert _same_events_both_ways(repo)


def test_the_0_8_1_events_on_a_merge_resolved_as_repaid(repo, monkeypatch):  # noqa: F811
    """The conflicted merge that kept b's repayment of k3, then the delete."""
    merged_history._merge_resolved_as_repaid(repo, monkeypatch)
    assert _same_events_both_ways(repo)

    merged_history._dated(monkeypatch, 6)
    merged_history._delete(repo)

    assert _same_events_both_ways(repo)


def test_the_0_8_1_events_on_a_fresh_seed(repo, capsys):  # noqa: F811
    """test_ratchet_report_fresh_seed_e2e.py's history, in process: `ratchet
    seed` writes one mark, read before and after the commit that holds it."""
    seed_artifacts(repo)
    add_knotty(repo)
    commit_all(repo, "knotty")
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    assert main(["ratchet", "seed", "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert _same_events_both_ways(repo) == []

    commit_all(repo, "seed the ratchet")

    assert [kind for _, _, kind, _ in _same_events_both_ways(repo)] == ["added"]
