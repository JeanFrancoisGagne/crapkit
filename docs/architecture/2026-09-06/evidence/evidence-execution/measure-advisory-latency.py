"""Fixed-count interleaved public CLI samples; no code or threshold changes."""
from pathlib import Path
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import time
import zipfile

import crapkit

ROOT = Path(r'<repo>')
assert Path(crapkit.__file__).resolve() == ROOT/'src/crapkit/__init__.py'
REF = '20f00e1371334f84aa70bba6f7b23bfc4bdae0f6'
EVIDENCE = Path(__file__).resolve().parent
WORK = EVIDENCE.parent/'advisory-latency'
WORK.mkdir(exist_ok=False)
CURRENT, RELEASE = WORK/'current', WORK/'release'
for source in (ROOT/'src').rglob('*'):
    if source.is_file() and '__pycache__' not in source.parts:
        target = CURRENT/source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
archive = subprocess.run(['git', 'archive', '--format=zip', REF, 'src'], cwd=ROOT,
                         capture_output=True, check=True).stdout
with zipfile.ZipFile(BytesIO(archive)) as zipped:
    for member in zipped.infolist():
        if member.is_dir():
            continue
        target = (RELEASE/member.filename).resolve()
        assert target.is_relative_to(RELEASE.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zipped.read(member))

repo = WORK/'fixture'/'repo'
repo.mkdir(parents=True)
subprocess.run(['git', 'init', '-q', str(repo)], check=True, capture_output=True)
(repo/'calc').mkdir()
(repo/'calc/grade.py').write_text('def grade(n):\n    if n > 1:\n        return n + 1\n    return n\n')
golden = json.loads((ROOT/'tests/goldens/claude_hook/04_no_toml.json').read_text())


def resolved(value):
    if isinstance(value, str):
        return value.replace('{REPO}', str(repo))
    if isinstance(value, dict):
        return {key: resolved(item) for key, item in value.items()}
    return value


payload = json.dumps(resolved(golden['payload']))
trees = {'current': CURRENT, 'release': RELEASE, 'live_root': ROOT}
environments = {}
proofs = {}
for label, tree in trees.items():
    env = dict(os.environ, PYTHONPATH=str(tree/'src'))
    env.pop('CRAPKIT_OVERRIDE_REASON', None)
    environments[label] = env
    code = ('from pathlib import Path; import crapkit,sys; '
            'assert Path(crapkit.__file__).resolve() == Path(sys.argv[1]).resolve(); '
            'print(crapkit.__file__); print(sys.version)')
    proofs[label] = subprocess.run([sys.executable, '-c', code, str(tree/'src/crapkit/__init__.py')],
        cwd=repo.parent, capture_output=True, text=True, env=env, check=True).stdout

commands = {'hook': [sys.executable, '-m', 'crapkit', *golden['argv']],
            'floor': [sys.executable, '-c', 'pass']}
samples = []


def call(label, kind, block, phase):
    env = environments[label]
    start = time.perf_counter_ns()
    child = subprocess.run(commands[kind], cwd=repo.parent, input=payload if kind == 'hook' else None,
        capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=30)
    elapsed = (time.perf_counter_ns() - start)/1_000_000
    assert (child.returncode, child.stdout, child.stderr) == (0, '', ''), child
    if phase != 'warmup':
        samples.append(dict(phase=phase, block=block, source=label, kind=kind, ms=elapsed))
    return elapsed


start_utc = datetime.now(timezone.utc).isoformat()
for _ in range(3):
    for label in trees:
        for kind in commands:
            call(label, kind, -1, 'warmup')

for block in range(50):
    labels = ('current', 'release') if block % 2 == 0 else ('release', 'current')
    kinds = ('hook', 'floor') if block % 4 < 2 else ('floor', 'hook')
    for label in labels:
        for kind in kinds:
            call(label, kind, block, 'source')

# The equal-depth snapshots control path cost; this second fixed batch checks the live root.
for block in range(12):
    labels = ('live_root', 'current') if block % 2 == 0 else ('current', 'live_root')
    for label in labels:
        for kind in ('hook', 'floor') if block % 4 < 2 else ('floor', 'hook'):
            call(label, kind, block, 'path')
end_utc = datetime.now(timezone.utc).isoformat()


def values(phase, label, kind):
    return [row['ms'] for row in samples
            if (row['phase'], row['source'], row['kind']) == (phase, label, kind)]


def stats(items):
    return {'min': min(items), 'median': statistics.median(items), 'mean': statistics.mean(items),
            'max': max(items), 'stdev': statistics.stdev(items)}


def overhead(phase, label):
    return [hook-floor for hook, floor in zip(values(phase, label, 'hook'), values(phase, label, 'floor'))]


summary = {}
for phase, labels in [('source', ('current', 'release')), ('path', ('live_root', 'current'))]:
    for label in labels:
        hook, floor = values(phase, label, 'hook'), values(phase, label, 'floor')
        summary[phase+'/'+label] = {'hook': stats(hook), 'floor': stats(floor),
            'paired_overhead': stats(overhead(phase, label)), 'min_hook_minus_min_floor': min(hook)-min(floor)}
        if phase == 'source':
            summary[phase+'/'+label]['five_sample_min_deltas'] = [
                min(hook[i:i+5])-min(floor[i:i+5]) for i in range(0, 50, 5)]
    difference = [a-b for a, b in zip(overhead(phase, labels[0]), overhead(phase, labels[1]))]
    rng = random.Random(20260906)
    boot = sorted(statistics.median(rng.choices(difference, k=len(difference))) for _ in range(5000))
    summary[phase+'/difference'] = {**stats(difference), 'median_bootstrap_95': [boot[125], boot[4874]],
                                  'raw_hook_difference': stats([a-b for a,b in zip(
                                      values(phase, labels[0], 'hook'), values(phase, labels[1], 'hook'))])}

manifests = {}
for label, tree in trees.items():
    entries = {str(path.relative_to(tree)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((tree/'src').rglob('*')) if path.is_file() and '__pycache__' not in path.parts}
    manifests[label] = {'aggregate_sha256': hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest(),
                        'files': entries}
assert manifests['current'] == manifests['live_root'], 'root source changed during measurement'
result = {'source_ref': REF, 'root': str(ROOT), 'snapshots': {key: str(value) for key,value in trees.items()},
    'start_utc': start_utc, 'end_utc': end_utc, 'python': sys.executable, 'import_proofs': proofs,
    'cwd': str(repo.parent), 'commands': commands, 'stdin': payload,
    'method': '3 warmups per command/source; 50 interleaved source blocks; 12 path-control blocks; fixed counts',
    'samples': samples, 'summary': summary, 'source_manifests': manifests}
(EVIDENCE/'advisory-latency.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({'start_utc': start_utc, 'end_utc': end_utc, 'summary': summary}, indent=2))
