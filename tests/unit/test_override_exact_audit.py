"""An override audit names the same twin as the debt it grants."""
from contextlib import closing
import sys

import pytest

from crapkit.override import record_override
from crapkit.ratchet import load_ratchet
from crapkit.store import SnapshotStore
from crapkit.verify import GateViolation


@pytest.mark.parametrize("raise_marks, expected_mark", [(True, 90.0), (False, 20.0)])
@pytest.mark.parametrize("key_name", ["f( )#2", "f( )#3"])
def test_twin_override_audit_matches_the_exact_grant(tmp_path, raise_marks, expected_mark, key_name):
    marks_path = tmp_path / "ratchet.tsv"
    marks_path.write_text(f"app.ts\tf( )\t12\napp.ts\t{key_name}\t20\n", encoding="utf-8")
    violation = GateViolation("app.ts", "f( )", 20, 9, 0.0, 90.0, "decompose", key_name=key_name)
    alert = f'"{sys.executable}" -B -c "import sys; sys.stdin.read()"'
    store = SnapshotStore(tmp_path / "db.sqlite")
    with closing(store._conn):
        run_id = store.write_run(commit="fixture", tool_versions={}, rows=[])
        record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                        alert_command=alert, violations=[violation], reason="reviewed debt",
                        raise_marks=raise_marks)
        marks = {(e.path, e.long_name): e.crap for e in load_ratchet(marks_path.read_text())}
        assert marks == {("app.ts", "f( )"): 12.0, ("app.ts", key_name): expected_mark}
        assert store.read_overrides(run_id) == [("app.ts", key_name, 90.0, "reviewed debt")]
        assert store.read_overrides_all()[0][1:3] == ("app.ts", key_name)
