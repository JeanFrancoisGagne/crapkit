"""A queue read remains useful when another connection acquires its first item."""
from contextlib import closing

from crapkit.cli.queue import _Handles, _maybe_claim
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore


def test_claim_race_advances_to_the_next_available_item(tmp_path):
    rows = [ScoredRow("src", "src/a.py", "f( )", line, line, 4, 4, 4, 1, 0, 0,
                      0.0, "measured", 20.0, "add-tests", occurrence=1)
            for line in (1, 10, 20)]
    path = tmp_path / "db.sqlite"
    store = SnapshotStore(path)
    rival = SnapshotStore(path)
    with closing(store._conn), closing(rival._conn):
        run = store.write_run(commit="snapshot", tool_versions={}, rows=rows)
        handles = _Handles(store, run)
        rival.record_claim(path=rows[0].path, long_name=rows[0].long_name,
                           commit="rival", handle=handles.of(rows[0]), key_name=handles.key(rows[0])[1])
        selected, conflicts = _maybe_claim("caller", True, rows, handles, 1)
        assert selected == [rows[1]]
        assert conflicts == 1
        assert {(claim["commit"], claim["key_name"]) for claim in store.open_claims()} == {
            ("rival", "f( )"), ("caller", "f( )#2")}
