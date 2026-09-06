"""A cache hit must describe the same reader and valid on-disk data."""
import json

import pytest

from crapkit.analyze import analyze_files, load_cache


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


@pytest.mark.parametrize('stamps', [[], {'same.sh': 1}, {'same.sh': ['bad', 1, 'hash']}])
def test_malformed_stat_index_shapes_do_not_break_analysis(tmp_path, stamps):
    (tmp_path / 'same.sh').write_text(SOURCE, encoding='utf-8')
    directory = tmp_path / '.crapkit'
    directory.mkdir()
    (directory / 'stat-stamps.json').write_text(
        json.dumps({'v': 1, 'stamps': stamps}), encoding='utf-8')
    records, hits, _ = analyze_files(tmp_path, ['same.sh'], cache={})
    assert hits == 0
    assert records['same.sh'][0].long_name == 'f()'
