"""Reader expansion cannot move older marks or claim ownership to a new arrow."""
from contextlib import closing
import json
import sys

import lizard
import pytest

from cli_inproc_repo import commit_all, git
from crapkit import analyze
from crapkit.cli import main
from crapkit.errors import ConfigError
from crapkit.lizardtypescript import LizardExtension
from crapkit.override import record_override
from crapkit.ratchet import check_reader_keys, load_ratchet
from crapkit.score import score_rows
from crapkit.snapshot import build_inventory_rows
from crapkit.store import SnapshotStore
from crapkit.verify import GateViolation


SOURCE = ('const f = [\n (x) => x ? 1 : 2,\n'
          ' (x) => x && 2 && 3 && 4 && 5 && 6 && 7 && 8\n];\n\n'
          'const g = rows.map((x) => x || 0);\n'
          'function kept(x) { return x ? 1 : 2; }\n')


def _repo(root, source=SOURCE):
    git(root, 'init', '-q', '-b', 'main')
    (root / 'app.ts').write_text(source, encoding='utf-8')
    (root / '.gitignore').write_text('.crapkit/\n', encoding='utf-8')
    (root / 'crapkit.toml').write_text(
        '[crapkit]\ntarget=6\n[[scope]]\nname="web"\npaths=["."]\n'
        'languages=["typescript"]\n[[lane]]\nname="unit"\ncommand="python -c pass"\n'
        'artifact=".crapkit/coverage.json"\nparser="istanbul"\nscopes=["web"]\n', encoding='utf-8')
    commit_all(root, 'multiline expression arrows')
    (root / '.crapkit').mkdir()
    artifact = {'app.ts': {'fnMap': {}, 'f': {}, 'statementMap': {
        '0': {'start': {'line': 2}, 'end': {'line': 2}}}, 's': {'0': 0}, 'branchMap': {}, 'b': {}}}
    (root / '.crapkit/coverage.json').write_text(json.dumps(artifact), encoding='utf-8')


def _old_mark(name='(anonymous)#2', key_version=0):
    stamp = f'# crapkit-analysis=9 lizard={lizard.version}\n'
    key = '# crapkit-keys=1\n' if key_version else ''
    return stamp + key + 'path\tlong_name\tcrap\napp.ts\t' + name + '\t6.0000\n'


def _coverage(root, capsys):
    assert main(['coverage', '--reuse-artifacts', '--json', '--repo', str(root)]) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize('key_version', [0, 1])
def test_version9_mark_cannot_restamp_after_multiline_reader_expansion(tmp_path, capsys, key_version):
    _repo(tmp_path)
    path = tmp_path / 'crapkit-ratchet.tsv'
    path.write_text(_old_mark(key_version=key_version), encoding='utf-8')
    before = path.read_bytes()
    assert _coverage(tmp_path, capsys)['functions'] == 4
    assert main(['ratchet', 'seed', '--repo', str(tmp_path)]) == 3
    assert 'reader' in capsys.readouterr().err
    assert path.read_bytes() == before


@pytest.mark.parametrize('stamp', ['', '# 10\n', '# crapkit-analysis=broken lizard=1.24.0\n'])
def test_only_an_analysis_stamp_can_prove_reader_identity(stamp):
    text = stamp + '# crapkit-keys=1\npath\tlong_name\tcrap\napp.ts\t(anonymous)#2\t6\n'
    with pytest.raises(ValueError, match='reader'):
        check_reader_keys(text)


def test_pre10_named_mark_keeps_its_value_on_compatible_reseed(tmp_path, capsys):
    _repo(tmp_path)
    path = tmp_path / 'crapkit-ratchet.tsv'
    path.write_text(_old_mark('kept( x )'), encoding='utf-8')
    _coverage(tmp_path, capsys)
    assert main(['ratchet', 'seed', '--repo', str(tmp_path)]) == 0
    capsys.readouterr()
    marks = {entry.long_name: entry.crap for entry in load_ratchet(path.read_text())}
    assert marks['kept( x )'] == 6
    assert marks['(anonymous)#2'] == 72


def _claim_source():
    branches = ' '.join(f'if (x > {n}) x++;' for n in range(10))
    return SOURCE.replace('x || 0', '{ ' + branches + ' return x; }')


def _old_snapshot(root, version='9'):
    extensions = [e for e in analyze._extensions_for('app.ts') if not isinstance(e, LizardExtension)]
    old = lizard.FileAnalyzer(extensions).analyze_source_code('app.ts', _claim_source()).function_list
    records = [analyze._record('app.ts', fn) for fn in old]
    assert [r.start for r in records if r.long_name == '(anonymous)'] == [2, 6]
    store = SnapshotStore(root / '.crapkit/crap.sqlite')
    with closing(store._conn):
        return store.write_run(commit=git(root, 'rev-parse', 'HEAD').strip(),
            tool_versions={'analysis_version': version, 'lizard': lizard.version},
            rows=score_rows(build_inventory_rows({'web': records}), {}, lane_scopes={'web'}, target=6))


def _next(root, capsys, claim=False):
    args = ['next-item', '--repo', str(root)] + (['--claim'] if claim else [])
    assert main(args) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize('version', ['9', None])
def test_claim_taken_from_an_old_snapshot_stays_with_the_whole_unproved_group(tmp_path, capsys, version):
    _repo(tmp_path, _claim_source())
    _old_snapshot(tmp_path, version)
    assert _next(tmp_path, capsys, claim=True)['item']['handle'] == '(anonymous)#2'
    _coverage(tmp_path, capsys)
    assert _next(tmp_path, capsys)['empty'] is True
    store = SnapshotStore(tmp_path / '.crapkit/crap.sqlite')
    with closing(store._conn):
        assert store.open_claims()[0]['key_version'] == 0
        other = store.record_claim(path='other.py', long_name='stable( )', commit='fixture')
    assert main(['claims', 'release', 'app.ts', '(anonymous)#2', '--json',
                 '--repo', str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)['released'] == 1
    assert _next(tmp_path, capsys)['item']['handle'] == '(anonymous)#3'
    store = SnapshotStore(tmp_path / '.crapkit/crap.sqlite')
    with closing(store._conn):
        assert [claim['id'] for claim in store.open_claims()] == [other]


def test_seed_cannot_label_old_snapshot_ordinals_as_reader10(tmp_path, capsys):
    _repo(tmp_path, _claim_source())
    _old_snapshot(tmp_path)
    assert main(['ratchet', 'seed', '--repo', str(tmp_path)]) == 3
    assert 'reader' in capsys.readouterr().err
    assert not (tmp_path / 'crapkit-ratchet.tsv').exists()


def test_current_run_records_reader_proof_and_allows_precise_arrow_claims(tmp_path, capsys):
    _repo(tmp_path, _claim_source())
    _coverage(tmp_path, capsys)
    first = _next(tmp_path, capsys, claim=True)['item']['handle']
    second = _next(tmp_path, capsys, claim=True)['item']['handle']
    assert first != second
    store = SnapshotStore(tmp_path / '.crapkit/crap.sqlite')
    with closing(store._conn):
        assert store.list_runs()[-1]['tool_versions']['analysis_version'] == '10'
        assert [claim['key_version'] for claim in store.open_claims()] == [1, 1]


@pytest.mark.parametrize('key_version', [None, 0, 1])
def test_direct_override_refuses_old_reader_marks_before_any_side_effect(tmp_path, capsys, key_version):
    _repo(tmp_path)
    _coverage(tmp_path, capsys)
    path = tmp_path / 'crapkit-ratchet.tsv'
    path.write_text(_old_mark(key_version=1), encoding='utf-8')
    before = path.read_bytes()
    (tmp_path / 'alert.py').write_text("from pathlib import Path\nPath('alert-fired').touch()\n")
    store = SnapshotStore(tmp_path / '.crapkit/crap.sqlite')
    with closing(store._conn):
        run = store.list_runs()[-1]['id']
        with pytest.raises(ConfigError, match='reader'):
            record_override(store=store, run_id=run, root=tmp_path,
                ratchet_file=path.name, alert_command=f'"{sys.executable}" alert.py',
                violations=[GateViolation('app.ts', 'kept( x )', 7, 8, 0, 72,
                                          'decompose', key_name='kept( x )')],
                reason='reader migration test', key_version=key_version,
                identity_rows=store.read_scored(run))
        assert store.read_overrides(run) == []
    assert not (tmp_path / 'alert-fired').exists()
    assert path.read_bytes() == before
