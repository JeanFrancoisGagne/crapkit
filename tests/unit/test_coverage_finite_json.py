"""No coverage projection can admit a nonfinite JSON number."""
import hashlib
import json

import pytest

from crapkit import covstream
from crapkit.errors import ToolError
from crapkit.score import score_rows
from crapkit.snapshot import InventoryRow


def report(number):
    region = {'start_line': 1, 'executed_lines': [1], 'missing_lines': [],
              'summary': {'covered_lines': 1, 'num_statements': 1,
                          'num_branches': 2, 'covered_branches': '__NUMBER__'}}
    data = {'meta': {'branch_coverage': True}, 'files': {'app.py': {'functions': {'f': region}}}}
    return json.dumps(data).replace('"__NUMBER__"', number)


@pytest.mark.parametrize('number', ['NaN', 'Infinity', '-Infinity', '1e999', '-1e999'])
@pytest.mark.parametrize('chunk', [1, 7, 1024])
def test_nonfinite_coverage_is_refused_before_scoring(tmp_path, number, chunk):
    artifact = tmp_path / 'coverage.json'
    artifact.write_text(report(number), encoding='utf-8')
    row = InventoryRow('core', 'app.py', 'f', 1, 1, 7, 7, 7, 1, 0, 0, 0, 1)
    with pytest.raises(ToolError, match='non-finite JSON number'):
        coverage, _ = covstream.parse_coveragepy_file(artifact, path_prefix='', chunk=chunk)
        score_rows([row], coverage, lane_scopes={'core'})


@pytest.mark.parametrize('reader', ['istanbul', 'istanbul_missing', 'istanbul_both',
                                   'python_missing', 'python_contexts'])
@pytest.mark.parametrize('number', ['NaN', 'Infinity', '-Infinity', '1e999'])
def test_every_projection_rejects_nonfinite_numbers(tmp_path, reader, number):
    artifact = tmp_path / 'coverage.json'
    if reader.startswith('istanbul'):
        artifact.write_text('{"app.ts":{"metadata":' + number + '}}', encoding='utf-8')
        calls = {'istanbul': covstream.parse_istanbul_file,
                 'istanbul_missing': covstream.parse_istanbul_missing_file,
                 'istanbul_both': covstream.parse_istanbul_both_file}
        with pytest.raises(ToolError, match='non-finite JSON number'):
            calls[reader](artifact, repo_root='', chunk=1)
    else:
        artifact.write_text(report(number), encoding='utf-8')
        with pytest.raises(ToolError, match='non-finite JSON number'):
            if reader == 'python_missing':
                covstream.parse_coveragepy_missing_file(artifact, path_prefix='', chunk=1)
            else:
                covstream.parse_coveragepy_contexts_file(
                    artifact, path_prefix='', source_path='absent.py', chunk=1)


def test_finite_exponents_and_strings_preserve_values_and_digest(tmp_path):
    artifact = tmp_path / 'coverage.json'
    raw = report('1e0').replace('"f"', '"NaN Infinity"').encode('utf-8')
    artifact.write_bytes(raw)
    coverage, digest = covstream.parse_coveragepy_file(artifact, path_prefix='', chunk=1)
    assert coverage['app.py'][0].name == 'NaN Infinity'
    assert coverage['app.py'][0].coverage == 0.5
    assert digest == hashlib.sha256(raw).hexdigest()
