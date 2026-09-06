"""Public CLI reproduction of collision history lost by runs prune."""
from contextlib import closing, redirect_stdout, redirect_stderr
import subprocess
import sys
from pathlib import Path
import json
from tempfile import TemporaryDirectory

import crapkit
from crapkit.cli import main
from crapkit.score import score_rows
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore

assert Path(crapkit.__file__).resolve() == Path(__file__).resolve().parents[5] / 'src/crapkit/__init__.py'

def invoke(root, *args):
    result = subprocess.run([sys.executable, '-B', '-m', 'crapkit', *args, '--repo', str(root)], capture_output=True, text=True, encoding='utf-8')
    return {'command': ' '.join(args), 'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}

def rows(starts, occurrence):
    return score_rows([InventoryRow('app', 'app.ts', '(anonymous)', line, line, 9, 9, 9, 1, 1, 0,
                                    occurrence=occurrence) for line in starts], {},
                      lane_scopes={'app'}, target=6)

with TemporaryDirectory(prefix='crapkit-prune-key-') as temp:
    root = Path(temp)
    (root / 'crapkit.toml').write_text('[crapkit]\ntarget=6\n[[scope]]\nname="app"\npaths=["."]\nlanguages=["typescript"]\ncoverage_optional=true\n', encoding='utf-8')
    marks = root / 'crapkit-ratchet.tsv'
    marks.write_text('path\tlong_name\tcrap\napp.ts\t(anonymous)#2\t99.0000\n', encoding='utf-8')
    (root / '.crapkit').mkdir()
    store = SnapshotStore(root / '.crapkit/crap.sqlite')
    with closing(store._conn):
        store.write_run(commit='old', tool_versions={}, rows=rows([1,1,10], 0))
        for commit in ('new1', 'new2'):
            store.write_run(commit=commit, tool_versions={}, rows=rows([1,2,10], 1))
        before = sorted(store.historical_collision_groups())
    output = {'collisions_before': before, 'commands': [invoke(root, 'ratchet', 'seed')]}
    output['commands'].append(invoke(root, 'runs', 'prune', '--keep', '1', '--json'))
    store = SnapshotStore(root / '.crapkit/crap.sqlite')
    with closing(store._conn):
        output['collisions_after'] = sorted(store.historical_collision_groups())
        output['kept_runs'] = [run['id'] for run in store.list_runs()]
    output['commands'].append(invoke(root, 'ratchet', 'seed'))
    output['marks_after'] = marks.read_text(encoding='utf-8')
    print(json.dumps(output, indent=2))

