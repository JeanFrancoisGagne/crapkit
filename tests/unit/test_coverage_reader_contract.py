"""Artifact admission and projections use the same streaming reader."""
import json

import pytest

from crapkit import covstream
from crapkit.errors import ToolError


def _report():
    fn = {'start_line': 1, 'executed_lines': [1, 2], 'missing_lines': [3],
          'summary': {'num_branches': 2, 'covered_branches': 1,
                      'num_statements': 3, 'covered_lines': 2}}
    files = {'src/f.py': {'functions': {'f': fn}, 'missing_lines': [3],
                         'contexts': {'2': ['test_f|run', '', 'test_f|setup']}},
             'src/other.py': {'functions': {}, 'missing_lines': [],
                              'contexts': {'1': ['unrelated|run']}}}
    return {'files': files, 'meta': {'branch_coverage': True}}


@pytest.mark.parametrize('chunk', [1, 7, 1024])
def test_python_reader_returns_functions_missing_lines_and_digest_in_one_walk(tmp_path, monkeypatch, chunk):
    import hashlib
    from crapkit.coverage_istanbul import FnCoverage

    path = tmp_path / 'coverage.json'
    raw = json.dumps(_report(), sort_keys=True).encode('utf-8')
    path.write_bytes(raw)
    walks = []
    walk = covstream.walk_report

    def counted(*args):
        walks.append(1)
        return walk(*args)

    monkeypatch.setattr(covstream, 'walk_report', counted)
    functions, dead, digest = covstream.parse_coveragepy_both_file(
        path, path_prefix='backend', chunk=chunk)
    assert walks == [1]
    assert functions == {'backend/src/f.py': [FnCoverage('f', 1, 3, True, 2, 1, 3, 2)],
                         'backend/src/other.py': []}
    assert dead == {'backend/src/f.py': {3}, 'backend/src/other.py': set()}
    assert digest == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('chunk', [1, 7, 1024])
def test_context_reader_projects_only_the_requested_repo_path(tmp_path, chunk):
    path = tmp_path / 'coverage.json'
    path.write_text(json.dumps(_report()), encoding='utf-8')
    assert covstream.parse_coveragepy_contexts_file(
        path, path_prefix='backend/', source_path='backend/src/f.py', chunk=chunk
    ) == {2: ['test_f']}
    assert covstream.parse_coveragepy_contexts_file(
        path, path_prefix='', source_path='absent.py', chunk=chunk
    ) == {}


def test_context_reader_validates_the_document_after_the_requested_file(tmp_path):
    path = tmp_path / 'coverage.json'
    path.write_text(json.dumps(_report()) + 'garbage', encoding='utf-8')
    with pytest.raises(ToolError, match='unparseable coverage.py'):
        covstream.parse_coveragepy_contexts_file(path, path_prefix='', source_path='src/f.py')


@pytest.mark.parametrize('chunk', [1, 4, 1024])
@pytest.mark.parametrize('text', ['{,"a": {}}', '{"a": {} "b": {}}', '{"a": {},}'])
def test_istanbul_rejects_invalid_member_separators(tmp_path, chunk, text):
    path = tmp_path / 'coverage.json'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ToolError, match='unparseable istanbul'):
        covstream.parse_istanbul_file(path, repo_root='', chunk=chunk)


@pytest.mark.parametrize('text', ['\u00a0{"a":{}}', '{\v"a":{}}', '{"a":\f{}}', '{"a":{}}\u00a0'])
def test_istanbul_rejects_whitespace_outside_the_json_grammar(tmp_path, text):
    path = tmp_path / 'coverage.json'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ToolError, match='unparseable istanbul'):
        covstream.parse_istanbul_file(path, repo_root='', chunk=1)


@pytest.mark.parametrize('chunk', [1, 4, 1024])
@pytest.mark.parametrize('files', ['{,"a": {}}', '{"a": {} "b": {}}', '{"a": {},}'])
def test_python_missing_lines_rejects_invalid_nested_separators(tmp_path, chunk, files):
    path = tmp_path / 'coverage.json'
    path.write_text('{"files":' + files + '}', encoding='utf-8')
    with pytest.raises(ToolError, match='unparseable coverage.py'):
        covstream.parse_coveragepy_missing_file(path, path_prefix='', chunk=chunk)
