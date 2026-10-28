"""Mark ages and repayments read the same across the marks file's change of stamp form.

One raw_git history: a cp1252 revision under the single-number stamp, a
PowerShell 5.1 resave (UTF-16 LE behind its byte-order mark, CRLF) that repays
one mark and adds one, the same marks under per-scope stamps, then a tighten
and a repayment. Every revision parses and none refuses: a past revision is
read for judging, never rewritten, so no format it holds stops a report. The per-scope
revision changes no mark, so `ratchet report` and brief's mark age give the
ages and the repayment count the commit dates give by hand (check 1, check 2),
and the same history without that commit gives the same report (check 3).

marks_history carries a revision forward a row at a time, and 0.8.1 read the
history as patch lines; both read each row alone, so every scope stamp line
reads as no mark there too.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crapkit.cli import main
from crapkit.marks_history import marks_history
from crapkit.packet import mark_age_days
from crapkit.ratchet import (RatchetEntry, ScopeStamp, ScopeStamps, SingleNumberStamp,
                             dump_ratchet, read_ratchet, read_stamp)
from crapkit.ratchet_report import crap_by_key, mark_events, report_from_events
from crapkit.ratchetfile import RatchetFile
from crapkit.repotext import marks_bytes
from raw_git import checkout, commit, repository
from test_marks_history import _same_events_both_ways

MARKS = "crapkit-ratchet.tsv"
CONFIG = '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "calc"\npaths = ["src"]\nlanguages = ["python"]\n'
SINGLE = SingleNumberStamp(13, "1.24.0")
SCOPED = ScopeStamps("1.24.0", {"calc": ScopeStamp({"python": 13}, {"coveragepy": 1}),
                                "web": ScopeStamp({"tsx": 13, "typescript": 13}, {"istanbul": 1})})
CAFE = "café( n )"
REPLACED = "caf�( n )"  # the cp1252 byte 0xE9 read by the marks rule
# Commit ages in days, oldest first: cp1252, the UTF-16 resave, the per-scope
# stamps, then the tighten of a( n ) and the repayment of café( n ).
AGES = {"cp1252": 100, "utf-16": 60, "per-scope": 30, "after": 10}


def marks_text(stamp, *marks: tuple[str, float]) -> str:
    entries = [RatchetEntry("src/a.py", name, crap) for name, crap in marks]
    return dump_ratchet(entries, stamp=stamp, key_version=1, file_name=MARKS)


def utf16(text: str) -> bytes:
    """PowerShell 5.1's resave: UTF-16 LE behind its byte-order mark, CRLF."""
    return marks_bytes(text.replace("\n", "\r\n"), "utf-16-le")


# Ten marks with wide names that every revision holds, so a revision runs past
# the 1,024 characters per differing stretch marks_history needs before it
# carries a revision forward instead of reading it in full.
WIDE = tuple((f"wide_{i}( {' , '.join(f'arg{j}' for j in range(40))} )", 9.0) for i in range(10))
CP1252 = marks_text(SINGLE, ("a( n )", 20.0), ("b( n )", 20.0), (CAFE, 5.0), *WIDE).encode("cp1252")
RESAVED = (("a( n )", 20.0), (CAFE, 5.0), ("d( n )", 12.0), *WIDE)
AFTER = (("a( n )", 10.0), ("d( n )", 12.0), *WIDE)
# What a report reads: a( n ) and every wide mark entered 100 days ago and
# d( n ) 60 days ago, against the newest commit, 10 days ago.
OPEN_AGES = {"a( n )": 90, **{name: 90 for name, _ in WIDE}, "d( n )": 50}


def revisions(spelled) -> dict[str, bytes]:
    """The four revisions' bytes, each later one spelled by `spelled`."""
    return {"cp1252": CP1252, "utf-16": spelled(marks_text(SINGLE, *RESAVED)),
            "per-scope": spelled(marks_text(SCOPED, *RESAVED)),
            "after": spelled(marks_text(SCOPED, *AFTER))}


def built(root: Path, data: dict[str, bytes], *, without: str | None = None) -> Path:
    """A repo whose marks file held each revision in turn, checked out at the newest."""
    repository(root)
    for name, body in data.items():
        if name != without:
            commit(root, files={MARKS.encode(): body}, age_days=AGES[name])
    checkout(root)
    (root / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    return root


def report(root: Path, capsys) -> dict:
    assert main(["ratchet", "report", "--json", "--repo", str(root)]) == 0
    printed = json.loads(capsys.readouterr().out)
    return {key: value for key, value in printed.items() if key != "anchor_ts"}


def key(name: str) -> tuple[str, str]:
    return ("src/a.py", name)


@pytest.fixture()
def history(tmp_path: Path) -> Path:
    return built(tmp_path / "history", revisions(utf16))


def test_every_revision_parses_and_none_refuses(history):
    """Each revision loads strictly, as a writer would read it, and holds the marks it was written with."""
    data = revisions(utf16)
    loaded = [RatchetFile.committed(history / MARKS, data[name]).entries for name in AGES]
    read = marks_history(history, MARKS)

    assert [type(read_stamp(RatchetFile.committed(history / MARKS, data[name]).text)) for name in AGES] == [
        SingleNumberStamp, SingleNumberStamp, ScopeStamps, ScopeStamps]
    assert [revision.marks for revision in read] == [crap_by_key(entries) for entries in loaded]
    wide = {key(name): crap for name, crap in WIDE}
    assert read[0].marks == {key("a( n )"): 20.0, key("b( n )"): 20.0, key(REPLACED): 5.0, **wide}
    assert read[2].marks == read[1].marks == {key("a( n )"): 20.0, key(CAFE): 5.0, key("d( n )"): 12.0,
                                              **wide}


def test_the_change_of_stamp_form_is_carried_a_row_at_a_time_and_changes_no_mark(history):
    """The per-scope revision lines up with the one it changed, so marks_history
    reads only the rows the two do not share, one row at a time: the stamp
    lines. They hold no mark, so the revision changes no key."""
    read = marks_history(history, MARKS)

    assert (read[2].before, read[2].changed) == (read[1].marks, frozenset())
    assert mark_events(read[2:3]) == [(read[2].time, None, "observed", 0.0)]


def test_a_row_read_alone_holds_no_mark_for_any_stamp_line():
    """The per-line rule: each stamp line of either form, read as one row, is no mark."""
    stamp_rows = [line for text in (marks_text(SINGLE), marks_text(SCOPED))
                  for line in text.splitlines() if line.startswith("#")]

    assert len(stamp_rows) == 6
    assert [read_ratchet(row)[0] for row in stamp_rows] == [[]] * 6


def test_check_1_ratchet_report_gives_the_ages_and_repayments_the_commit_dates_give(history, capsys):
    """Ages count from the newest commit, 10 days ago: 100 - 10 for the marks
    the cp1252 revision entered, 60 - 10 for d( n ). b( n ) and the U+FFFD name
    went at the resave 60 days ago, café( n ) at the newest commit."""
    printed = report(history, capsys)

    assert OPEN_AGES == {"a( n )": AGES["cp1252"] - AGES["after"], **{name: 90 for name, _ in WIDE},
                         "d( n )": AGES["utf-16"] - AGES["after"]}
    assert {row["long_name"]: row["age_days"] for row in printed["oldest"]} == OPEN_AGES
    assert (printed["open"], printed["dropped_total"]) == (len(OPEN_AGES), 3)
    assert (printed["dropped_last_30d"], printed["dropped_last_90d"]) == (1, 3)


def test_check_2_briefs_mark_age_reads_the_same_ages(history):
    """brief reads a marked function's age through packet.mark_age_days over the
    events of marks_history (cli/queue.py's _read_mark_events)."""
    events = mark_events(marks_history(history, MARKS))

    assert {name: mark_age_days(events, key(name)) for name in OPEN_AGES} == OPEN_AGES
    assert mark_age_days(events, key(CAFE)) is None


def test_check_3_the_history_without_the_change_of_form_reports_the_same(history, tmp_path, capsys):
    """The newest revision is spelled single-number there, so nothing but the stamp form differs."""
    single_after = revisions(utf16)
    single_after["after"] = utf16(marks_text(SINGLE, *AFTER))
    without = built(tmp_path / "without", single_after, without="per-scope")

    assert report(without, capsys) == report(history, capsys)
    assert len(marks_history(without, MARKS)) == len(marks_history(history, MARKS)) - 1


def test_the_same_history_read_as_patch_lines_gives_the_same_events(tmp_path):
    """0.8.1 read each revision's -U0 patch a line at a time; git splits UTF-16
    text into no rows, so this history spells its later revisions in UTF-8.
    The frozen 0.8.1 replay (test_marks_history.py, check 3) and today's read
    give the same events and the same report."""
    root = built(tmp_path / "utf-8", revisions(lambda text: text.encode("utf-8")))

    events = _same_events_both_ways(root)

    assert [kind for _, _, kind, _ in events].count("observed") == 1
    report_now = report_from_events(events)
    assert {row["long_name"]: row["age_days"] for row in report_now["oldest"]} == OPEN_AGES
    assert report_now["dropped_total"] == 3
