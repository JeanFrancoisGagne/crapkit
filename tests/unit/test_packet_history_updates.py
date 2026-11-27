"""Packet age and ratchet report interpret one committed history."""
import pytest

from crapkit.marks_history import MarksRevision
from crapkit.packet import mark_age_days
from crapkit.ratchet_report import DAY, held_event, mark_events, report_from_events


def test_committed_tightening_keeps_the_same_age_in_packet_and_report():
    key = ("src/a.py", "f( )")
    events = mark_events([MarksRevision("c1", 1000, {key: 50.0}, None),
                          MarksRevision("c2", 1000 + 90 * DAY, {key: 20.0}, {key: 50.0})])

    assert mark_age_days(events, key) == 90
    assert report_from_events(events)["oldest"] == [
        {"path": "src/a.py", "long_name": "f( )", "age_days": 90}]


OLD, NEW = ("calc/grade.py", "classify( a , b )"), ("calc/grade.py", "classify( a , b , c = None )")
MOVED = ("calc/grading.py", "classify( a , b , c = None )")
SEEDED = MarksRevision("seed", 1000, {OLD: 7.0}, None)
TICK = MarksRevision("tick", 1000 + 370 * DAY, {OLD: 7.0}, {OLD: 7.0})


@pytest.mark.parametrize("revisions, key, age", [
    pytest.param([SEEDED, TICK], OLD, 370, id="before-the-re-key"),
    pytest.param([SEEDED, TICK, MarksRevision("re-key", 1000 + 400 * DAY, {NEW: 7.0}, {OLD: 7.0})],
                 NEW, 400, id="after-the-re-key-commit"),
    pytest.param([SEEDED, MarksRevision("re-key", 1000 + 30 * DAY, {NEW: 6.0}, {OLD: 7.0}),
                  MarksRevision("ratchet move", 1000 + 400 * DAY, {MOVED: 6.0}, {NEW: 6.0})],
                 MOVED, 400, id="after-a-re-key-then-a-path-move"),
])
def test_brief_reads_the_age_ratchet_report_reads_across_a_move(revisions, key, age):
    """brief's gate_rule.mark_age_days is packet.mark_age_days over the same
    events ratchet report replays, so both follow the mark to its new key."""
    events = mark_events(revisions)
    report = report_from_events(events + held_event(revisions))

    assert mark_age_days(events, key) == age
    assert report["oldest"] == [{"path": key[0], "long_name": key[1], "age_days": age}]
    assert report["dropped_total"] == 0
