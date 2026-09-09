"""The expression reader's typed mode belongs to cache identity."""
from crapkit.analyze import analyze_files
from crapkit.merge import UnanalyzableFile


def test_jsx_cache_cannot_bypass_a_cold_tsx_refusal(tmp_path):
    """Same bytes, different reader: the jsx entry must not answer for the tsx.

    A refusal is a record now rather than a raise, which moves the risk this
    guards. A cache keyed loosely enough to serve the jsx rows for case.tsx would
    score two functions for a file no reader ever read, and the typed refusal
    would go quiet behind them.
    """
    source = 'const f = [x => x < 0, x => x + 1];\n'
    for path in ('case.jsx', 'case.tsx'):
        (tmp_path / path).write_text(source, encoding='utf-8')
    jsx, hits, cache = analyze_files(tmp_path, ['case.jsx'], cache={})
    assert hits == 0
    assert len(jsx['case.jsx']) == 2

    cold, _, _ = analyze_files(tmp_path, ['case.tsx'], cache={})
    warm, _, _ = analyze_files(tmp_path, ['case.tsx'], cache=cache)

    for records in (cold['case.tsx'], warm['case.tsx']):
        assert isinstance(records, UnanalyzableFile)
        assert list(records) == []
        assert 'expression-arrow body' in records.reason


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
