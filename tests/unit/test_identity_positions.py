"""Same-line callbacks retain separate keys, stored marks and selectable history."""
from collections import namedtuple
from contextlib import closing

import pytest

from crapkit import keys, packet
from crapkit.errors import ToolError
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore
from crapkit.worklist import RatchetMarks

ROW = (ScoredRow if 'occurrence' in ScoredRow._fields else
       namedtuple('SourceRow', (*ScoredRow._fields, 'occurrence')))


def row(occurrence, *, scope='web', start=1, name='(anonymous)', crap=12.):
    return ROW(scope, 'app.ts', name, start, start, 3, 3, 3, 1, 0, 0,
               0., 'untested', crap, 'add-tests', 0, occurrence)


def test_same_line_keys_follow_occurrence_not_score_or_arrival():
    first, second, later = row(1), row(2, crap=20), row(1, start=10)
    rows = [second, first._replace(scope='copy'), later, first]
    assert keys.key_names(rows) == {
        ('app.ts', '(anonymous)', 1, 1): '(anonymous)',
        ('app.ts', '(anonymous)', 1, 2): '(anonymous)#2',
        ('app.ts', '(anonymous)', 10, 1): '(anonymous)#3',
    }
    assert keys.key_of(keys.key_names(rows), second) == ('app.ts', '(anonymous)#2')


def test_handles_distinguish_same_line_and_keep_global_anonymous_order():
    first, second = row(1), row(2, name='(anonymous) ( x )')
    assert packet.handles([second, first, first._replace(scope='copy')]) == {
        ('app.ts', '(anonymous)', 1, 1): '(anonymous)#1',
        ('app.ts', '(anonymous) ( x )', 1, 2): '(anonymous)#2',
    }


def test_legacy_collision_refuses_keys_but_scope_copies_do_not():
    old = row(0)
    with pytest.raises(ToolError, match='ambiguous legacy'):
        keys.key_names([old, old._replace(crap=20)])
    assert keys.key_names([old, old._replace(scope='copy')]) == {
        ('app.ts', '(anonymous)', 1, 0): '(anonymous)'}


def test_stored_same_line_callbacks_keep_scores_spans_and_history(tmp_path):
    store = SnapshotStore(tmp_path / 'state.sqlite')
    with closing(store._conn):
        first, second = row(1), row(2, crap=20)
        run = store.write_run(commit='fixture', tool_versions={}, rows=[second, first])
        assert [(r.occurrence, r.crap) for r in store.read_scored(run)] == [(1, 12), (2, 20)]
        assert [r.occurrence for r in store.read_rows(run)] == [1, 2]
        assert [r.occurrence for r in store.read_crap(run)] == [1, 2]
        assert store.read_marks(run).score(second) == (20, 0)
        assert store.function_span(run, 'app.ts', '(anonymous)#2') == (1, 1)
        assert store.function_history('app.ts', '(anonymous)#2')[0]['crap'] == 20
        assert store.function_key('app.ts', '(anonymous)', '(anonymous)#2') == '(anonymous)#2'
        ratchet = RatchetMarks({('app.ts', '(anonymous)#2'): 25}, store.twin_key_names(run))
        assert ratchet.of(second) == 25
        assert ratchet.of(first) is None


def test_legacy_database_keeps_rows_and_refuses_precise_collision_reads(tmp_path):
    path = tmp_path / 'state.sqlite'
    store = SnapshotStore(path)
    with closing(store._conn):
        run = store.write_run(commit='fixture', tool_versions={}, rows=[row(0), row(0)])
        if 'occurrence' in {r[1] for r in store._conn.execute('PRAGMA table_info(functions)')}:
            store._conn.execute('ALTER TABLE functions DROP COLUMN occurrence')
            store._conn.commit()
    reopened = SnapshotStore(path)
    with closing(reopened._conn):
        assert len(reopened.read_rows(run)) == 2
        assert [r.occurrence for r in reopened.read_rows(run)] == [0, 0]
        for read in [lambda: reopened.read_marks(run), lambda: reopened.twin_key_names(run),
                     lambda: reopened.function_span(run, 'app.ts', '(anonymous)'),
                     lambda: reopened.function_history('app.ts', '(anonymous)')]:
            with pytest.raises(ToolError, match='ambiguous legacy'):
                read()


def test_collision_group_query_preserves_ordinary_and_scope_copy_rows():
    first = row(1)
    assert keys.ambiguous_groups([first, first._replace(scope='copy')]) == set()
    assert keys.ambiguous_groups([first, row(2)]) == {('app.ts', '(anonymous)')}
    assert keys.ambiguous_groups([first, row(2)], legacy_only=True) == set()
    assert keys.ambiguous_groups([row(0), row(0)]) == {('app.ts', '(anonymous)')}


def test_actual_typescript_callbacks_survive_inventory_scoring_and_store(tmp_path):
    from crapkit.analyze import analyze_source
    from crapkit.snapshot import build_inventory_rows
    from crapkit.score import score_rows

    source = 'const a = values.map((x) => x > 0 ? x : 0).filter((x) => x > 1);'
    inventory = build_inventory_rows({'web': analyze_source('app.ts', source)})
    assert [(r.start, r.end, r.occurrence) for r in inventory] == [(1, 1, 1), (1, 1, 2)]
    scored = score_rows(inventory, {}, lane_scopes={'web'}, target=6)
    store = SnapshotStore(tmp_path / 'state.sqlite')
    with closing(store._conn):
        run = store.write_run(commit='fixture', tool_versions={}, rows=scored)
        assert store.read_scored(run) == scored
        names = keys.key_names(store.read_scored(run))
        assert [keys.key_of(names, r)[1] for r in scored] == ['(anonymous)', '(anonymous)#2']
        assert len(packet.handles(store.read_rows(run))) == 2


def test_historical_groups_do_not_mix_runs_or_scope_copies(tmp_path):
    store = SnapshotStore(tmp_path / 'state.sqlite')
    with closing(store._conn):
        for _ in range(2):
            store.write_run(commit='fixture', tool_versions={},
                            rows=[row(0), row(0, scope='copy')])
        assert store.historical_collision_groups() == set()
        store.write_run(commit='fixture', tool_versions={}, rows=[row(1), row(2)])
        assert store.historical_collision_groups() == {('app.ts', '(anonymous)')}


def test_same_line_numeric_selection_refuses_and_ordinal_claims_stay_separate(tmp_path):
    store = SnapshotStore(tmp_path / 'state.sqlite')
    with closing(store._conn):
        rows = [row(1), row(2)]
        run = store.write_run(commit='fixture', tool_versions={'analysis_version': '10'}, rows=rows)
        with pytest.raises(ToolError, match='multiple functions'):
            store.find_functions('app.ts', '1')
        with pytest.raises(ToolError, match='multiple functions'):
            store.function_key('app.ts', '(anonymous)', '1')
        names, handles = keys.key_names(rows), packet.handles(rows)
        claims = [store.record_claim(path=r.path, long_name=r.long_name, commit='fixture',
                                    handle=handles[keys.lookup(r)], key_name=keys.key_of(names, r)[1],
                                    source_run_id=run)
                  for r in rows]
        assert all(claims) and len(set(claims)) == 2
        assert len(store.open_claims()) == 2


def test_persisted_legacy_claim_keeps_the_whole_group_and_releases_by_saved_handle(tmp_path):
    from crapkit.cli.queue import _claims_to_release

    store = SnapshotStore(tmp_path / 'state.sqlite')
    with closing(store._conn):
        store._conn.executemany(
            'INSERT INTO attempts(path,long_name,commit_sha,handle,key_name) VALUES (?,?,?,?,?)',
            [('app.ts', 'f( )', 'fixture', 'f#2', 'f( )#2'),
             ('other.ts', 'g( )', 'fixture', 'g', 'g( )')])
        store._conn.commit()
        held = store.open_claims()
        assert keys.claim_key(held[0]) is None
        assert store.record_claim(path='app.ts', long_name='f( )', commit='fixture',
                                  handle='f#1', key_name='f( )') is None
        selected = _claims_to_release(held, False, ['app.ts', 'f#2'])
        assert [c['id'] for c in selected] == [held[0]['id']]
        store.close_claims([c['id'] for c in selected])
        assert [c['path'] for c in store.open_claims()] == ['other.ts']
        assert store.record_claim(path='app.ts', long_name='f( )', commit='fixture',
                                  handle='f#1', key_name='f( )') is not None
        fresh = store.open_claims()[-1]
        assert keys.claim_key(fresh) == ('app.ts', 'f( )')
