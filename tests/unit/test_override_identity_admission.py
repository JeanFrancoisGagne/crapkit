"""An override cannot publish new callback keys under unresolved legacy identity."""
from contextlib import closing
import sys

import pytest

from crapkit.analyze import analyze_source
from crapkit.errors import ConfigError
from crapkit.override import record_override
from crapkit.ratchet import checked_key_version
from crapkit.snapshot import build_inventory_rows
from crapkit.store import SnapshotStore
from crapkit.verify import GateViolation


@pytest.mark.parametrize('raise_marks', [True, False], ids=['verify', 'hook'])
def test_mixed_legacy_override_refuses_before_alert_audit_or_ratchet_write(tmp_path, raise_marks):
    source = 'const a = values.map((x) => x > 0 ? x : 0).filter((x) => x > 1);'
    rows = build_inventory_rows({'web': analyze_source('app.ts', source)})
    assert [(row.start, row.occurrence) for row in rows] == [(1, 1), (1, 2)]
    ratchet = tmp_path / 'ratchet.tsv'
    original = b'path\tlong_name\tcrap\nold.py\told( )\t20.0000\n'
    ratchet.write_bytes(original)
    version = checked_key_version(original.decode(), rows)
    assert version == 0, 'the absent old function has no proved identity mapping'
    script = tmp_path / 'alert.py'
    script.write_text("from pathlib import Path\nPath('alert.log').write_text('called')\n", encoding='utf-8')
    store = SnapshotStore(tmp_path / 'state.sqlite')
    with closing(store._conn):
        run = store.write_run(commit='fixture', tool_versions={}, rows=[])
        violation = GateViolation('app.ts', '(anonymous)', 1, 9, 0., 90., 'decompose',
                                  key_name='(anonymous)#2')
        with pytest.raises(ConfigError, match='legacy ratchet key identity is ambiguous'):
            record_override(store=store, run_id=run, root=tmp_path, ratchet_file='ratchet.tsv',
                            alert_command=f'"{sys.executable}" "{script}"',
                            violations=[violation], reason='local fixture',
                            raise_marks=raise_marks, key_version=version, identity_rows=rows)
        assert not (tmp_path / 'alert.log').exists()
        assert store.read_overrides(run) == []
        assert ratchet.read_bytes() == original
