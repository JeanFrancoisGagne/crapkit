"""Reader expansion cannot move older marks or claim ownership to a new arrow."""
from contextlib import closing
import json
import sys

import lizard
import pytest

from cli_inproc_repo import commit_all, git
from crapkit import analyze
from crapkit.cli import main
from crapkit.cli._shared import _check_ratchet_identity
from crapkit.cli.ratchet_cmds import _publish_checked
from crapkit.errors import ConfigError
from crapkit.lizardtypescript import LizardExtension
from crapkit.override import record_override
from crapkit.ratchet import (RatchetEntry, ScopeStamp, ScopeStamps, SingleNumberStamp, check_reader_keys,
                             dump_ratchet, load_ratchet, metric_version)
from crapkit.ratchetfile import RatchetFile
from crapkit.score import score_rows
from crapkit.snapshot import build_inventory_rows
from crapkit.store import SnapshotStore
from crapkit.verify import GateViolation


MARKS = 'crapkit-ratchet.tsv'
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


def _old_mark(name='(anonymous)#2', key_version=0, form='single-number'):
    """A mark the analysis 9 reader keyed; `per-scope` records 9 as scope web's typescript number."""
    if form == 'per-scope':
        stamp = ScopeStamps(lizard.version, {'web': ScopeStamp({'typescript': 9}, {'istanbul': 1})})
        return dump_ratchet([RatchetEntry('app.ts', name, 6.0)], stamp=stamp, key_version=key_version,
                            file_name=MARKS)
    stamp = f'# crapkit-analysis=9 lizard={lizard.version}\n'
    key = '# crapkit-keys=1\n' if key_version else ''
    return stamp + key + 'path\tlong_name\tcrap\napp.ts\t' + name + '\t6.0000\n'


def _coverage(root, capsys):
    assert main(['coverage', '--reuse-artifacts', '--json', '--repo', str(root)]) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize('form', ['single-number', 'per-scope'])
@pytest.mark.parametrize('key_version', [0, 1])
def test_version9_mark_cannot_restamp_after_multiline_reader_expansion(tmp_path, capsys, key_version, form):
    _repo(tmp_path)
    path = tmp_path / 'crapkit-ratchet.tsv'
    path.write_text(_old_mark(key_version=key_version, form=form), encoding='utf-8', newline='\n')
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
        assert store.list_runs()[-1]['tool_versions']['analysis_version'] == str(analyze.ANALYSIS_VERSION)
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
                identity_rows=store.read_scored(run), metric=metric_version())
        assert store.read_overrides(run) == []
    assert not (tmp_path / 'alert-fired').exists()
    assert path.read_bytes() == before


# --- each mark's own language number, under either stamp form ----------------------
# A JavaScript-family mark asks for the number of its own language: javascript
# for .js, .jsx, .mjs and .cjs, typescript for .ts, tsx for .tsx. A per-scope
# stamp answers with the lowest number any scope records for that language.

RUN = {'analysis_version': '13', 'lizard': '1.24.0'}


def _stamped(stamp, *paths: str) -> str:
    entries = [RatchetEntry(path, '(anonymous)#2', 6.0) for path in paths]
    return dump_ratchet(entries, stamp=stamp, key_version=1, file_name=MARKS)


def _web(**numbers) -> ScopeStamps:
    """Scope api reads Python under 13, scope web its languages under `numbers`."""
    return ScopeStamps('1.24.0', {'api': ScopeStamp({'python': 13}, {'coveragepy': 1}),
                                  'web': ScopeStamp(numbers, {'istanbul': 1})})


def _expression_check(text, root):
    check_reader_keys(text)


def _identity_proof(text, root):
    """cli/_shared's proof, which explain, brief, rescore and seed run first."""
    _check_ratchet_identity(text, root, MARKS, [])


def _seed_publish(text, root):
    """seed's last check before it writes `text`, measured by a current run."""
    _publish_checked(RatchetFile.read(root / MARKS), text, load_ratchet(text), [], RUN)


SEAMS = [_expression_check, _identity_proof, _seed_publish]


def _refused(seam, text, root) -> str | None:
    """The files the reader refusal names, or None when the seam admits the marks."""
    try:
        seam(text, root)
    except (ValueError, ConfigError) as exc:
        assert 'expression reader 10 changed anonymous function ordinals in ' in str(exc)
        return str(exc).partition('ordinals in ')[2].partition(';')[0]
    return None


@pytest.mark.parametrize('seam', SEAMS, ids=lambda seam: seam.__name__.strip('_'))
@pytest.mark.parametrize('stamp, refused', [
    (SingleNumberStamp(9, '1.24.0'), 'app.ts'),
    (SingleNumberStamp(10, '1.24.0'), None),
    (_web(typescript=9), 'app.ts'),
    (_web(typescript=10), None),
    (_web(javascript=13, tsx=13, typescript=13), None),
], ids=['single-9', 'single-10', 'per-scope-9', 'per-scope-10', 'per-scope-13'])
def test_the_typescript_number_proves_an_anonymous_typescript_mark_under_both_forms(tmp_path, seam, stamp,
                                                                                   refused):
    assert _refused(seam, _stamped(stamp, 'app.ts'), tmp_path) == refused


@pytest.mark.parametrize('seam', SEAMS, ids=lambda seam: seam.__name__.strip('_'))
@pytest.mark.parametrize('stamp, refused', [
    (_web(javascript=9), 'src/a.js, src/b.mjs, src/c.ts, src/d.tsx'),
    (_web(javascript=9, tsx=13, typescript=13), 'src/a.js, src/b.mjs'),
], ids=['no-typescript-number', 'typescript-at-13'])
def test_a_javascript_number_of_9_refuses_only_javascript_family_marks(tmp_path, seam, stamp, refused):
    """Python reads under 13, so its mark is never named; a TypeScript mark is
    refused only when no scope records a typescript or tsx number to prove it."""
    text = _stamped(stamp, 'src/a.js', 'src/b.mjs', 'src/c.ts', 'src/d.tsx', 'src/e.py')

    assert _refused(seam, text, tmp_path) == refused


def test_the_lowest_number_any_scope_records_answers_for_its_language(tmp_path):
    """The check reads no config, so it cannot place a mark in one scope."""
    stamp = ScopeStamps('1.24.0', {'old': ScopeStamp({'typescript': 9}, 'cc-only'),
                                   'web': ScopeStamp({'typescript': 13}, {'istanbul': 1})})

    assert _refused(_expression_check, _stamped(stamp, 'app.ts'), tmp_path) == 'app.ts'


def test_seed_refuses_marks_a_run_under_reader_9_measured_whatever_the_file_records(tmp_path):
    """_publish_checked reads the run's own stamp (ratchet.run_stamp), not the raw analysis_version."""
    text = _stamped(_web(javascript=13, tsx=13, typescript=13), 'app.ts')
    old_run = {'analysis_version': '9', 'lizard': '1.24.0'}

    with pytest.raises(ConfigError, match='ordinals in app.ts;'):
        _publish_checked(RatchetFile.read(tmp_path / MARKS), text, load_ratchet(text), [], old_run)

    assert not (tmp_path / MARKS).exists()
