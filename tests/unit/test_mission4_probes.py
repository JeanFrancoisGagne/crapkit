"""The two mission-4 probes.

A mark re-keyed on one side of a merge, or re-keyed in a commit long after it
was seeded, reads by exact key as one mark dropped and another added. The merge
probe passes: `merge_ratchets` pairs the drop with the add through
`keys.pair_moves`. The report probe still reads them apart and is pinned as a
strict xfail, which fails the suite once it passes: the signal to drop the
marker. The same shapes are pairing rows in tests/unit/test_keys_resolve.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from crapkit.marks_history import marks_history
from crapkit.ratchet import RatchetEntry, merge_ratchets
from crapkit.ratchet_report import mark_events, report_from_events
from marks_history_repo import AGES, MARKS, NEW, OLD, PATH, rekey_history
from raw_git import DAY, git

# The three revisions of the marks file, spelled out here rather than built by
# marks_history_repo.marks_bytes, so a fault in the helper shows as a mismatch.
_TOP = b"# crapkit-analysis=13 lizard=1.24.0\n# crapkit-keys=1\npath\tlong_name\tcrap\n"
_SEEDED = _TOP + b"calc/grade.py\tclassify( a , b )\t7.0000\n"
_REKEYED = _TOP + b"calc/grade.py\tclassify( a , b , c = None )\t7.0000\n"
_SECOND = _REKEYED + b"calc/grade.py\tclassify( a , b , c = None )#2\t9.0000\n"


def test_the_rekey_history_holds_three_commits_with_their_marks_and_ages(tmp_path: Path):
    """The history the report probe reads, read back through git itself: three
    commits on main, oldest first, each holding only the marks file at one
    revision and stamped 400, 30 and 0 days before the newest commit."""
    history = rekey_history(tmp_path)

    log = git(tmp_path, "log", "--reverse", "--format=%H %ct", "main").decode().split()
    shas, stamps = log[0::2], [int(stamp) for stamp in log[1::2]]
    assert shas == list(history)
    assert [git(tmp_path, "show", f"{sha}:{MARKS}") for sha in shas] == [_SEEDED, _REKEYED, _SECOND]
    assert [git(tmp_path, "ls-tree", "--name-only", sha) for sha in shas] == [MARKS.encode() + b"\n"] * 3
    # Each commit takes its own clock reading, so allow a few seconds of drift.
    ages = [stamps[-1] - stamp for stamp in stamps]
    assert [abs(age - days * DAY) <= 5 for age, days in zip(ages, (400, 30, 0))] == [True] * 3, ages


def test_a_merge_keeps_the_tighten_against_a_rename():
    """Ours re-keyed the mark at 7, theirs tightened the old key to 6. The
    merge pairs ours's drop and add as one mark and keeps the tighten under
    the new key; a merge by exact key kept the old key at 6 beside the new key
    at 7, and the function's mark loosened back to 7."""
    base = [RatchetEntry(PATH, OLD, 7.0)]
    ours = [RatchetEntry(PATH, NEW, 7.0)]
    theirs = [RatchetEntry(PATH, OLD, 6.0)]

    assert merge_ratchets(base, ours, theirs) == [RatchetEntry(PATH, NEW, 6.0)]


@pytest.mark.xfail(strict=True, reason="mission-4-06: ratchet report keeps a re-keyed mark's age")
def test_ratchet_report_keeps_a_rekeyed_marks_age(tmp_path: Path):
    """The mark was seeded 400 days before the newest commit and re-keyed 30
    days before it. Today its age restarts at the re-key and reads 30 days."""
    rekey_history(tmp_path)

    report = report_from_events(mark_events(marks_history(tmp_path, MARKS)))

    ages = {(row["path"], row["long_name"]): row["age_days"] for row in report["oldest"]}
    assert ages[(PATH, NEW)] == AGES[0]
