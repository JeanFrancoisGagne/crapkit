"""A cache hit must describe the same reader and valid on-disk data."""
import json
import platform

import lizard
import pytest

import crapkit
import crapkit.analyze as analyze
from crapkit.analyze import analyze_files, fingerprint, load_cache, save_cache


SOURCE = 'function f() {\n    return 0\n}\n'


def test_identical_bytes_in_different_languages_keep_their_cold_records(tmp_path):
    paths = ['same.ps1', 'same.sh']
    for path in paths:
        (tmp_path / path).write_text(SOURCE, encoding='utf-8')
    cold, _, cache = analyze_files(tmp_path, paths, cache={})
    warm, hits, _ = analyze_files(tmp_path, paths, cache=cache)
    assert hits == 2
    assert cold['same.ps1'][0].long_name == 'f'
    assert cold['same.sh'][0].long_name == 'f()'
    assert warm == cold


def test_a_rename_that_changes_reader_reanalyzes_identical_bytes(tmp_path):
    old = tmp_path / 'same.sh'
    old.write_text(SOURCE, encoding='utf-8')
    _, _, cache = analyze_files(tmp_path, ['same.sh'], cache={})
    old.rename(tmp_path / 'same.ps1')
    renamed, hits, _ = analyze_files(tmp_path, ['same.ps1'], cache=cache)
    cold, _, _ = analyze_files(tmp_path, ['same.ps1'], cache={})
    assert hits == 0
    assert renamed == cold


def test_same_reader_suffixes_share_a_cache_entry_after_rename(tmp_path):
    old = tmp_path / 'same.sh'
    old.write_text(SOURCE, encoding='utf-8')
    _, _, cache = analyze_files(tmp_path, ['same.sh'], cache={})
    old.rename(tmp_path / 'same.bash')
    renamed, hits, _ = analyze_files(tmp_path, ['same.bash'], cache=cache)
    cold, _, _ = analyze_files(tmp_path, ['same.bash'], cache={})
    assert hits == 1
    assert renamed == cold


@pytest.mark.parametrize('data', [[], {'fp': 'v', 'entries': [1]},
                                {'fp': 'v', 'entries': {'hash': [None]}}])
def test_malformed_cache_shapes_are_cold(tmp_path, data):
    path = tmp_path / 'cache.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    assert load_cache(path) == {}


@pytest.fixture
def persisted_cache(tmp_path):
    (tmp_path / 'same.sh').write_text(SOURCE, encoding='utf-8')
    cold, hits, cache = analyze_files(tmp_path, ['same.sh'], cache={})
    assert hits == 0
    path = tmp_path / 'cache.json'
    save_cache(path, cache)
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['fp'] == fingerprint()
    return path, data, cold


def _assert_cold_recovery(path, data, cold):
    path.write_text(json.dumps(data), encoding='utf-8')
    loaded = load_cache(path)
    assert loaded == {}
    rebuilt, hits, cache = analyze_files(path.parent, ['same.sh'], cache=loaded)
    assert hits == 0
    assert rebuilt == cold
    save_cache(path, cache)
    warm, hits, _ = analyze_files(path.parent, ['same.sh'], cache=load_cache(path))
    assert hits == 1
    assert warm == cold


def test_valid_persisted_cache_hits_with_current_fingerprint(persisted_cache):
    path, _, cold = persisted_cache
    loaded = load_cache(path)
    assert loaded['fp'] == fingerprint()
    warm, hits, _ = analyze_files(path.parent, ['same.sh'], cache=loaded)
    assert hits == 1
    assert warm == cold


@pytest.mark.parametrize('field,value', [
    ('path', None), ('long_name', 1), ('start', '1'), ('ccn', 1.0),
    ('start', True), ('occurrence', False), ('occurrence', -1),
])
def test_invalid_persisted_record_fields_rebuild_cold_records(persisted_cache, field, value):
    path, data, cold = persisted_cache
    rows = next(iter(data['entries'].values()))
    rows[0][cold['same.sh'][0]._fields.index(field)] = value
    _assert_cold_recovery(path, data, cold)


@pytest.mark.parametrize('record', [None, {}, 'record', [], ['too', 'short']])
def test_invalid_persisted_record_shapes_rebuild_cold_records(persisted_cache, record):
    path, data, cold = persisted_cache
    rows = next(iter(data['entries'].values()))
    rows[0] = record
    _assert_cold_recovery(path, data, cold)


@pytest.mark.parametrize('rows', [None, {}, 'rows', 1, True])
def test_non_list_persisted_rows_rebuild_cold_records(persisted_cache, rows):
    path, data, cold = persisted_cache
    key = next(iter(data['entries']))
    data['entries'][key] = rows
    _assert_cold_recovery(path, data, cold)


@pytest.mark.parametrize('stamps', [[], {'same.sh': 1}, {'same.sh': ['bad', 1, 'hash']},
                                    {'same.sh': [1, '2', 'hash']}])
def test_malformed_stat_index_shapes_do_not_break_analysis(tmp_path, stamps):
    (tmp_path / 'same.sh').write_text(SOURCE, encoding='utf-8')
    directory = tmp_path / '.crapkit'
    directory.mkdir()
    (directory / 'stat-stamps.json').write_text(
        json.dumps({'v': 1, 'stamps': stamps}), encoding='utf-8')
    records, hits, _ = analyze_files(tmp_path, ['same.sh'], cache={})
    assert hits == 0
    assert records['same.sh'][0].long_name == 'f()'


# --- what the fingerprint holds ------------------------------------------------
#
# Every input that can move a record turns its entries cold: crapkit's version
# and lizard's version every entry, the analysis number of a file's language
# (in the entry's key) that language's entries. The Python version is left out
# on purpose, because lizard tokenizes with its own regular expressions and no
# record moves with it; the golden below holds that on every Python the CI
# matrix runs.


def _raise_shell(mp):
    """A raise of the shell number, with the revision raised with it."""
    mp.setitem(analyze.ANALYSIS_VERSIONS, 'shell', analyze.ANALYSIS_VERSIONS['shell'] + 1)
    mp.setattr(analyze, 'ANALYSIS_VERSION', analyze.ANALYSIS_VERSION + 1)


@pytest.mark.parametrize('bump', [
    _raise_shell,
    lambda mp: mp.setattr(lizard, 'version', lizard.version + '.post1'),
    lambda mp: mp.setattr(crapkit, '__version__', crapkit.__version__ + '.post1'),
], ids=['analysis-version', 'lizard-version', 'crapkit-version'])
def test_a_moved_input_turns_every_entry_cold(persisted_cache, monkeypatch, bump):
    path, _, cold = persisted_cache
    bump(monkeypatch)

    warm, hits, _ = analyze_files(path.parent, ['same.sh'], cache=load_cache(path))

    assert hits == 0
    assert warm == cold


def test_the_fingerprint_leaves_the_python_version_out():
    assert platform.python_version() not in fingerprint()
    assert 'python' not in fingerprint().lower()


GOLDEN_CORPUS = {
    'app.py': ("def pick(kind, n):\n    if kind == 'a' and n:\n        return 1\n"
               "    for i in range(n):\n        if i % 2:\n            return i\n    return 0\n\n\n"
               "class Box:\n    def open(self, x):\n        return x or 0\n"),
    'app.ts': ("export function dispatch(kind: string): number {\n  switch (kind) {\n"
               "    case 'a': return 1;\n    case 'b': return 2;\n    default: return 0;\n  }\n}\n\n"
               "export const twice = (n: number): number => (n > 0 ? n * 2 : -n);\n"),
    'main.go': ("package main\n\nfunc clamp(n int) int {\n\tif n < 0 {\n\t\treturn 0\n"
                "\t} else if n > 9 {\n\t\treturn 9\n\t}\n\treturn n\n}\n"),
    'lib.rs': ("pub fn sign(n: i32) -> i32 {\n    match n {\n        0 => 0,\n"
               "        x if x > 0 => 1,\n        _ => -1,\n    }\n}\n"),
    'calc.cpp': ("int total(int *xs, int n) {\n    int t = 0;\n    for (int i = 0; i < n; ++i) {\n"
                 "        if (xs[i] > 0 && xs[i] < 100) t += xs[i];\n    }\n    return t;\n}\n"),
}

# Measured under CPython 3.11.2, 3.11.16, 3.12.14, 3.13.15 and 3.14.7 with
# lizard 1.24.0 at analysis version 13: the same tuples on all five. Against
# version 12 one value moved: calc.cpp's nesting reads 2 where lizard's ND
# column read 3, since the cognitive pass opens a level for the for's body and
# the if's body and none for `&&`. A bump of ANALYSIS_VERSION re-measures
# this table on every Python the CI runs and moves GOLDEN_ANALYSIS_VERSION with
# it; until then the test below it fails, so a bump cannot leave the golden
# skipped. A row that moves on one Python only means the fingerprint needs the
# Python version.
GOLDEN_ANALYSIS_VERSION = 13
GOLDEN_LIZARD_VERSION = '1.24.0'
GOLDEN_RECORDS = [
    ('app.py', 'pick( kind , n )', 1, 7, 5, 5, 5, 7, 2, 2, 5, 1, 0),
    ('app.py', 'open( self , x )', 11, 12, 2, 2, 2, 2, 2, 0, 1, 1, 0),
    ('app.ts', 'dispatch ( kind )', 1, 9, 3, 2, 2, 8, 1, 1, 1, 1, 0),
    ('app.ts', 'twice ( n )', 9, 9, 2, 2, 2, 1, 1, 1, 1, 1, 0),
    ('calc.cpp', 'total( int * xs , int n)', 1, 7, 4, 4, 4, 7, 2, 2, 4, 1, 0),
    ('lib.rs', 'sign n : i32', 1, 7, 4, 4, 4, 7, 1, 1, 3, 1, 0),
    ('main.go', 'clamp n int', 3, 10, 3, 3, 3, 8, 1, 1, 2, 1, 0),
]


def _flat(records: dict) -> list[tuple]:
    return [tuple(r) for path in sorted(records) for r in records[path]]


def test_the_golden_was_measured_at_the_running_analysis_version():
    """A bump that leaves the table behind fails here instead of skipping it."""
    assert analyze.ANALYSIS_VERSION == GOLDEN_ANALYSIS_VERSION, (
        f'ANALYSIS_VERSION is {analyze.ANALYSIS_VERSION}: re-measure GOLDEN_RECORDS '
        f'on every Python the CI runs and set GOLDEN_ANALYSIS_VERSION to it')


@pytest.mark.skipif(lizard.version != GOLDEN_LIZARD_VERSION,
                    reason=f'the golden was measured with lizard {GOLDEN_LIZARD_VERSION}')
def test_every_python_the_ci_runs_computes_the_records_a_cache_would_serve(tmp_path):
    """A cache written under one Python is served under another, so the
    records have to be the ones that Python would compute itself."""
    for name, text in GOLDEN_CORPUS.items():
        (tmp_path / name).write_text(text, encoding='utf-8', newline='\n')

    records, _, _ = analyze_files(tmp_path, sorted(GOLDEN_CORPUS), cache={})

    assert _flat(records) == GOLDEN_RECORDS
