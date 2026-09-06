"""Every reader checks legacy ordinal identity before using a function's mark."""
from contextlib import closing
import io
import json

import pytest

from crapkit.analyze import analyze_source
from crapkit.cli import main
from crapkit.ratchet import metric_version
from crapkit.score import score_rows
from crapkit.snapshot import build_inventory_rows
from crapkit.store import SnapshotStore


def _fixture(root, stamp=''):
    branches = ' '.join(f'if (x > {n}) x++;' for n in range(7))
    source = f'values.map((x) => x).filter((x) => {{ {branches} return x; }});\n'
    (root / 'app.ts').write_text(source, encoding='utf-8')
    (root / 'crapkit.toml').write_text(
        '[crapkit]\ntarget=6\n[[scope]]\nname="web"\npaths=["."]\n'
        'languages=["typescript"]\ncoverage_optional=true\n', encoding='utf-8')
    mark = f'# {metric_version()}\n' + stamp + 'path\tlong_name\tcrap\napp.ts\t(anonymous)#2\t99.0000\n'
    (root / 'crapkit-ratchet.tsv').write_text(mark, encoding='utf-8')
    return build_inventory_rows({'web': analyze_source('app.ts', source)})


@pytest.mark.parametrize('stamp, expected', [('', 3), ('# crapkit-keys=1\n', 0)])
def test_explain_checks_legacy_identity_before_displaying_an_ordinal_mark(tmp_path, capsys,
                                                                        stamp, expected):
    rows = _fixture(tmp_path, stamp)
    assert [(r.start, r.occurrence) for r in rows] == [(1, 1), (1, 2)]
    (tmp_path / '.crapkit').mkdir()
    store = SnapshotStore(tmp_path / '.crapkit/crap.sqlite')
    with closing(store._conn):
        store.write_run(commit='fixture', tool_versions={}, rows=score_rows(rows, {},
                        lane_scopes={'web'}, target=6))
    assert main(['explain', 'app.ts', '(anonymous)#2', '--json', '--repo', str(tmp_path)]) == expected
    output = capsys.readouterr()
    if expected:
        assert 'legacy ratchet key identity is ambiguous' in output.err
        assert json.loads(output.out)['error']['exit'] == 3
    else:
        assert json.loads(output.out)['functions'][0]['ratchet_mark'] == 99


@pytest.mark.parametrize('stamp, expected', [('', 2), ('# crapkit-keys=1\n', 0),
                                           ('# crapkit-keys=999\n', 2)])
def test_advisory_warns_when_the_saved_mark_has_no_proved_callback_identity(tmp_path, capsys,
                                                                         monkeypatch, stamp,
                                                                         expected):
    _fixture(tmp_path, stamp)
    before = (tmp_path / 'crapkit-ratchet.tsv').read_bytes()
    event = {'hook_event_name': 'PostToolUse', 'tool_name': 'Edit', 'cwd': str(tmp_path),
             'tool_input': {'file_path': str(tmp_path / 'app.ts')}}
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(event)))
    monkeypatch.setattr('sqlite3.connect', lambda *a, **k: pytest.fail('advisory opened a store'))
    assert main(['claude-hook', '--protocol', '1']) == expected
    output = capsys.readouterr()
    assert not output.out
    assert bool(output.err) == bool(expected)
    assert not (tmp_path / '.crapkit').exists()
    assert (tmp_path / 'crapkit-ratchet.tsv').read_bytes() == before
