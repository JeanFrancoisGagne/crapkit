"""Packet age and ratchet report interpret one committed history."""
from crapkit.marks_history import MarksRevision
from crapkit.packet import mark_age_days
from crapkit.ratchet_report import DAY, mark_events, report_from_events


def test_committed_tightening_keeps_the_same_age_in_packet_and_report():
    key = ("src/a.py", "f( )")
    events = mark_events([MarksRevision("c1", 1000, {key: 50.0}, None),
                          MarksRevision("c2", 1000 + 90 * DAY, {key: 20.0}, {key: 50.0})])

    assert mark_age_days(events, key) == 90
    assert report_from_events(events)["oldest"] == [
        {"path": "src/a.py", "long_name": "f( )", "age_days": 90}]
