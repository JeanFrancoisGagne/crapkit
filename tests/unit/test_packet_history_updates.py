"""Packet age and ratchet report interpret one committed history."""
from crapkit.packet import mark_age_days
from crapkit.ratchet_report import DAY, mark_events, report_from_events


def test_committed_tightening_keeps_the_same_age_in_packet_and_report():
    key = ("src/a.py", "f( )")
    events = mark_events([(1000, "+src/a.py\tf( )\t50"),
                          (1000 + 90 * DAY, "-src/a.py\tf( )\t50\n+src/a.py\tf( )\t20")])

    assert mark_age_days(events, key) == 90
    assert report_from_events(events)["oldest"] == [
        {"path": "src/a.py", "long_name": "f( )", "age_days": 90}]
