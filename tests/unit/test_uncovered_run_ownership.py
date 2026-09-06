"""One run's parsed artifact facts cannot affect another run or a read command."""
from types import SimpleNamespace

from crapkit import uncovered
from crapkit.config import Lane


def _artifact(tmp_path):
    path = tmp_path / 'coverage.json'
    path.write_text('{"files":{"src/f.py":{"missing_lines":[7]}}}', encoding='utf-8')
    lane = Lane(name='py', command='', artifact='coverage.json', parser='coveragepy', scopes=('src',))
    return path, SimpleNamespace(lanes=[lane])


def test_two_runs_own_independent_missing_line_folds(tmp_path):
    path, cfg = _artifact(tmp_path)
    first, second = uncovered.DeadLineFold(), uncovered.DeadLineFold()
    first.add(path, {'src/f.py': {3}})
    second.add(path, {'src/f.py': {5}})
    assert uncovered.missing_by_path(tmp_path, cfg, folded=first) == {'src/f.py': {3}}
    assert uncovered.missing_by_path(tmp_path, cfg, folded=second) == {'src/f.py': {5}}
    assert uncovered.missing_by_path(tmp_path, cfg) == {'src/f.py': {7}}


def test_a_collector_hands_its_owned_map_over_once(tmp_path):
    path, cfg = _artifact(tmp_path)
    folded = uncovered.DeadLineFold()
    folded.add(path, {'src/f.py': {3}})
    assert uncovered.missing_by_path(tmp_path, cfg, folded=folded) == {'src/f.py': {3}}
    assert uncovered.missing_by_path(tmp_path, cfg, folded=folded) == {'src/f.py': {7}}


def test_parallel_lanes_intersect_inside_their_run(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path, cfg = _artifact(tmp_path)
    folded = uncovered.DeadLineFold()
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(folded.add, path, {'src/f.py': lines})
                for lines in ({3, 5}, {5, 7})]
        for job in jobs:
            job.result()
    assert uncovered.missing_by_path(tmp_path, cfg, folded=folded) == {'src/f.py': {5}}
