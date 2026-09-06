"""Synthetic before/after samples using the released algorithms and actual changed code."""
import gc
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import tracemalloc

import crapkit.dup as current_dup
from crapkit import covstream, uncovered
from crapkit.snapshot import InventoryRow

BASELINE = '20f00e1371334f84aa70bba6f7b23bfc4bdae0f6'
evidence = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd() / '.crapkit/architecture-benchmark'
evidence.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path.cwd() / 'tests/unit'))
from coverage_oracles import parse_coveragepy_contexts


def released(name):
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader('crapkit._old_' + name, loader=None))
    source = subprocess.check_output(['git', 'show', f'{BASELINE}:src/crapkit/{name}.py'], encoding='utf-8')
    exec(compile(source, f'HEAD:{name}.py', 'exec'), module.__dict__)
    return module


def one_sample(work):
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    result = work()
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, {'seconds': round(elapsed, 6), 'peak_python_mib': round(peak / 2**20, 4)}


def compare(before, after):
    initial_before, cold_before = one_sample(before)
    initial_after, cold_after = one_sample(after)
    assert initial_before == initial_after
    samples_before, samples_after = [], []
    for _ in range(5):
        old, old_sample = one_sample(before)
        new, new_sample = one_sample(after)
        assert old == new == initial_before
        samples_before.append(old_sample)
        samples_after.append(new_sample)
    return {'output_equal_every_sample': True, 'cold_before': cold_before, 'cold_after': cold_after,
            'warm_before': samples_before, 'warm_after': samples_after,
            'median_warm_before': {key: statistics.median(s[key] for s in samples_before) for key in cold_before},
            'median_warm_after': {key: statistics.median(s[key] for s in samples_after) for key in cold_after}}


def main():
    artifact = evidence / 'contexts-5000.json'
    data = {'contexts': {str(i): [f'tests/test_a.py::test_{j}|run' for j in range(3)] for i in range(1, 7)}}
    artifact.write_text(json.dumps({'meta': {'branch_coverage': True},
                                   'files': {f'src/f{i}.py': data for i in range(5000)}}), encoding='utf-8')
    contexts = compare(
        lambda: parse_coveragepy_contexts(artifact.read_text(encoding='utf-8'), path_prefix='').get('src/f0.py', {}),
        lambda: covstream.parse_coveragepy_contexts_file(artifact, path_prefix='', source_path='src/f0.py'))
    contexts['fixture'] = {'files': 5000, 'artifact_bytes': artifact.stat().st_size}

    old_dup = released('dup')
    source = 'def target():\n' + '\n'.join(f'    value{i} = call({i})' for i in range(10)) + '\n'
    target = InventoryRow('src', 'target.py', 'target', 1, 11, 1, 1, 1, 11, 0, 0)
    rows = [InventoryRow('src', f'other{i}.py', f'other{i}', 1, 11, 1, 1, 1, 11, 0, 0) for i in range(25000)]
    index = current_dup.FunctionIndex(8, [(row, {i}) for i, row in enumerate(rows)])
    twins = compare(lambda: old_dup.find_twins(target, rows, {'target.py': source}, indexed=index),
                    lambda: current_dup.find_twins(target, rows, {'target.py': source}, indexed=index))
    twins['fixture'] = {'indexed_functions': 25000, 'qualifying_twins': 0}

    old_uncovered = released('uncovered')
    artifacts = [evidence / f'fold-{i}.json' for i in range(13)]
    for path in artifacts:
        path.write_text('{}', encoding='utf-8')
    wanted = {uncovered._artifact_key(path) for path in artifacts}

    def maps(index):
        return {f'src/f{file}.py': set(range(index, 80 + index)) for file in range(1500)}

    def old_fold():
        old_uncovered._folded, old_uncovered._folded_from = {}, set()
        for i, path in enumerate(artifacts):
            old_uncovered.fold_dead_lines(path, maps(i))
        return old_uncovered._take_folded(wanted)[0]

    def new_fold():
        fold = uncovered.DeadLineFold()
        for i, path in enumerate(artifacts):
            fold.add(path, maps(i))
        return fold.take(wanted)[0]

    folding = compare(old_fold, new_fold)
    folding['fixture'] = {'lanes': 13, 'files_per_lane': 1500, 'missing_lines_per_file': 80}
    result = {'kind': 'synthetic; first invocation then five warmed in-process samples; tracemalloc enabled',
              'contexts': contexts, 'twins': twins, 'folding': folding}
    (evidence / 'measurements.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({name: {key: value for key, value in item.items() if not key.startswith('warm_')}
                      for name, item in result.items() if isinstance(item, dict)}, indent=2))


if __name__ == '__main__':
    main()
