"""One run's parsed artifact facts cannot affect another run or a read command."""
import os
from types import SimpleNamespace

import pytest

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


# --- boundary-25: the fold serves the walked lines only while the bytes are the walked ones

def _same_size_same_time(path) -> None:
    """[7] becomes [9]: same length, and the old modification time put back."""
    stat = path.stat()
    path.write_text(path.read_text(encoding='utf-8').replace('[7]', '[9]'), encoding='utf-8')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def _rewrite_new_time(path) -> None:
    stat = path.stat()
    path.write_text(path.read_text(encoding='utf-8').replace('[7]', '[9]'), encoding='utf-8')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 50_000_000))


REWRITES = {'untouched': (lambda path: None, {3}),
            'same-size-one-tick': (_same_size_same_time, {9}),
            'rewrite-new-mtime': (_rewrite_new_time, {9})}


@pytest.mark.parametrize('name', sorted(REWRITES))
def test_a_rewrite_after_the_walk_is_read_off_the_file(name, tmp_path):
    """The fold keyed on (path, mtime, size), so a same-size rewrite under the
    old time served the walked lines [3] where the file said [9]. It keys on
    the walk's sha256 now."""
    path, cfg = _artifact(tmp_path)
    folded = uncovered.DeadLineFold()
    folded.add(path, {'src/f.py': {3}})
    rewrite, truth = REWRITES[name]
    rewrite(path)

    assert uncovered.missing_by_path(tmp_path, cfg, folded=folded) == {'src/f.py': truth}


def test_the_walk_s_own_digest_keys_the_fold_without_a_second_read(tmp_path):
    from crapkit.lane_stamps import file_sha256

    path, cfg = _artifact(tmp_path)
    folded = uncovered.DeadLineFold()
    folded.add(path, {'src/f.py': {3}}, file_sha256(path))

    assert uncovered.missing_by_path(tmp_path, cfg, folded=folded) == {'src/f.py': {3}}
