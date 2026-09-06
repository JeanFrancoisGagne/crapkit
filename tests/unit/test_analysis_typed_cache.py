"""The expression reader's typed mode belongs to cache identity."""
import pytest

from crapkit.analyze import analyze_files
from crapkit.errors import ToolError


def test_jsx_cache_cannot_bypass_a_cold_tsx_refusal(tmp_path):
    source = 'const f = [x => x < 0, x => x + 1];\n'
    for path in ('case.jsx', 'case.tsx'):
        (tmp_path / path).write_text(source, encoding='utf-8')
    jsx, hits, cache = analyze_files(tmp_path, ['case.jsx'], cache={})
    assert hits == 0
    assert len(jsx['case.jsx']) == 2
    with pytest.raises(ToolError, match='expression-arrow body'):
        analyze_files(tmp_path, ['case.tsx'], cache={})
    with pytest.raises(ToolError, match='expression-arrow body'):
        analyze_files(tmp_path, ['case.tsx'], cache=cache)


def test_jsx_to_tsx_rename_reanalyzes_and_same_mode_rename_hits(tmp_path):
    source = 'const f = [(x) => x + 1, (x) => x + 2];\n'
    old = tmp_path / 'case.jsx'
    old.write_text(source, encoding='utf-8')
    _, _, cache = analyze_files(tmp_path, ['case.jsx'], cache={})
    old.rename(tmp_path / 'case.tsx')
    renamed, hits, cache = analyze_files(tmp_path, ['case.tsx'], cache=cache)
    cold, _, _ = analyze_files(tmp_path, ['case.tsx'], cache={})
    assert hits == 0
    assert renamed == cold
    (tmp_path / 'case.tsx').rename(tmp_path / 'next.tsx')
    renamed, hits, _ = analyze_files(tmp_path, ['next.tsx'], cache=cache)
    assert hits == 1
    assert [row._replace(path='case.tsx') for row in renamed['next.tsx']] == cold['case.tsx']
