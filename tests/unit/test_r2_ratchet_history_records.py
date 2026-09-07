"""Historical patches retain the portable record's complete identity."""
import pytest

from crapkit.ratchet import RatchetEntry, dump_ratchet
from crapkit.ratchet_report import mark_events, report_from_events


@pytest.mark.parametrize("path", ["#source.py", "src/a\u2028b.py", "src/a\x85b.py"])
@pytest.mark.parametrize("encoded", [False, True])
def test_ratchet_history_replays_raw_and_encoded_rows(path, encoded):
    row = dump_ratchet([RatchetEntry(path, "f( )", 12)], stamp="").split("\n")[1]
    if not encoded:
        row = f"{path}\tf( )\t12.0000"
    events = mark_events([(100, "+++ b/ratchet.tsv\n+" + row + "\n"),
                          (200, "--- a/ratchet.tsv\n-" + row + "\n")])
    assert events == [(100, (path, "f( )"), "added", 12),
                      (200, (path, "f( )"), "dropped", 12)]
    report = report_from_events(events)
    assert (report["open"], report["dropped_total"]) == (0, 1)
