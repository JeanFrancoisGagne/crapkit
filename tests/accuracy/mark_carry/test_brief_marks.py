"""The carried mark in the packet: brief, explain and the worklist ask
`keys.resolve` which mark judges a function, and brief ages it under the key
it was recorded under.

hand_brief.tsv holds a file's functions, its marks file's dated revisions and
the asked function, with the ratchet_mark, carried_from and mark_age_days
worked by hand from docs/ratchet.md: the mark's value off the newest revision,
and its age in days from the revision that brought it in to the newest one. An
update keeps the entry date, and a move does too. The packet's mark read and
ratchet report's age rule give each row's answer.

The probe row fails against 0.8.1's rule, a mark read under its exact key
only, which is the retro check. crapkit is loaded by name when a test runs
(kit/closure.py follows import statements only).
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import NamedTuple

import pytest

HERE = Path(__file__).resolve().parent
PATH = "calc/grade.py"
DAY = 86400
NEWEST = 1000  # the day number of a revision 0 days old


class Function(NamedTuple):
    """One function of the run, as far as its key reads it."""
    path: str
    long_name: str
    start: int
    occurrence: int


def crapkit(name: str):
    return importlib.import_module(f"crapkit.{name}")


def hand_rows() -> list[dict]:
    lines = (HERE / "hand_brief.tsv").read_bytes().decode("utf-8").splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


HAND = hand_rows()


def revisions(row: dict) -> list[tuple[int, dict]]:
    """Each revision of the marks file, oldest first, as (days ago, marks): the
    revisions split on " ; ", a revision's marks on " / "."""
    found = []
    for revision in row["history"].split(" ; "):
        days, marks = revision.split(":", 1)
        pairs = (cell.rsplit("=", 1) for cell in marks.split(" / "))
        found.append((int(days), {(PATH, name): float(value) for name, value in pairs}))
    return found


def events(row: dict) -> list[tuple]:
    """ratchet report's mark events over the hand revisions."""
    revision = crapkit("marks_history").MarksRevision
    parsed, before, found = revisions(row), None, []
    for n, (days, marks) in enumerate(parsed):
        found.append(revision(str(n), (NEWEST - days) * DAY, marks, before))
        before = marks
    return crapkit("ratchet_report").mark_events(found)


def run_rows(names: str) -> list[Function]:
    """The run's rows behind NAMES, key names split on " | ": a `name#N` key is
    the Nth function of its long name in file order, so it opens on line N."""
    split = crapkit("keys").split_ordinal
    return [Function(PATH, *split(name), 1) for name in names.split(" | ")]


def packet_mark(row: dict) -> tuple:
    """(ratchet_mark, carried_from, mark_age_days) as brief reads them."""
    entry = crapkit("ratchet").RatchetEntry
    marks = revisions(row)[-1][1]
    found = crapkit("cli.queue")._brief_mark([entry(*key, value) for key, value in marks.items()],
                                             (PATH, row["asked"]), run_rows(row["functions"]), True)
    age = None if found.value is None else crapkit("packet").mark_age_days(events(row), found.key)
    return found.value, found.carried_from, age


def expected(row: dict) -> tuple:
    mark = float(row["ratchet_mark"]) if row["ratchet_mark"] else None
    age = None if row["mark_age_days"] == "none" else int(row["mark_age_days"])
    return mark, row["carried_from"] or None, age


@pytest.mark.parametrize("row", HAND, ids=lambda r: r["case"])
def test_every_hand_row_gets_its_mark_and_age_from_the_packet(row):
    assert packet_mark(row) == expected(row)


def test_the_hand_table_holds_a_carry_an_own_mark_and_a_refusal():
    answers = [expected(row) for row in HAND]

    assert {carried is not None for _, carried, _ in answers} == {True, False}
    assert {mark is None for mark, _, _ in answers} == {True, False}


def test_the_probe_row_fails_against_the_exact_key_read():
    """Retro detection: 0.8.1 read a mark under the function's own key only."""
    (probe,) = [row for row in HAND if row["case"].startswith("the probe:")]
    marks = revisions(probe)[-1][1]

    assert (marks.get((PATH, probe["asked"])), expected(probe)[0]) == (None, 7.0)


def test_an_unproved_marks_file_reads_the_function_s_own_key_alone():
    """A marks file that cannot prove its key identity carries nothing."""
    (probe,) = [row for row in HAND if row["case"].startswith("the probe:")]
    entry = crapkit("ratchet").RatchetEntry

    found = crapkit("cli.queue")._brief_mark([entry(PATH, "classify( a , b )", 7.0)],
                                             (PATH, probe["asked"]), run_rows(probe["asked"]), False)

    assert (found.value, found.carried_from) == (None, None)
