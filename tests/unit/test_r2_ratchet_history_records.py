"""Historical revisions retain the portable record's complete identity."""
import pytest

from crapkit.marks_history import marks_history
from crapkit.ratchet import RatchetEntry, dump_ratchet
from crapkit.ratchet_report import mark_events, report_from_events
from raw_git import commit, repository
from test_marks_history import _same_events_both_ways


@pytest.mark.parametrize("path", ["#source.py", "src/a\u2028b.py", "src/a\x85b.py"])
@pytest.mark.parametrize("encoded", [False, True])
def test_ratchet_history_replays_raw_and_encoded_rows(tmp_path, path, encoded):
    row = dump_ratchet([RatchetEntry(path, "f( )", 12)], stamp="").split("\n")[1]
    if not encoded:
        row = f"{path}\tf( )\t12.0000"
    root = repository(tmp_path)
    commit(root, files={b"ratchet.tsv": (row + "\n").encode()}, age_days=1)
    commit(root, files={b"ratchet.tsv": b""})
    events = mark_events(marks_history(root, "ratchet.tsv"))
    assert [(key, kind, crap) for _, key, kind, crap in events] == [
        ((path, "f( )"), "added", 12), ((path, "f( )"), "dropped", 12)]
    assert _same_events_both_ways(root, "ratchet.tsv") == events, "the 0.8.1 read agrees"
    report = report_from_events(events)
    assert (report["open"], report["dropped_total"]) == (0, 1)
