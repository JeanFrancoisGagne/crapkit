from pathlib import Path
import json
import os
import subprocess
import sys
import crapkit

assert Path(crapkit.__file__).resolve() == Path(r'<repo>\src\crapkit\__init__.py')
evidence = Path(__file__).resolve().parent
data = json.loads((evidence/'advisory-latency.json').read_text())
probe = '''import json, sys
from pathlib import Path
calls = []
def observe(frame, event, arg):
    filename = frame.f_code.co_filename.replace('\\\\', '/')
    if event == 'call' and '/crapkit/' in filename:
        calls.append([filename.split('/crapkit/', 1)[1], frame.f_code.co_name])
sys.setprofile(observe)
import crapkit
assert Path(crapkit.__file__).resolve() == Path(sys.argv[1]).resolve()
from crapkit.cli import main
code = main(['claude-hook', '--protocol', '1'])
sys.setprofile(None)
print(json.dumps({'code': code, 'modules': sorted(sys.modules), 'calls': calls}))
'''
results = {}
for label in ('current', 'release'):
    tree = Path(data['snapshots'][label])
    env = dict(os.environ, PYTHONPATH=str(tree/'src'))
    env.pop('CRAPKIT_OVERRIDE_REASON', None)
    child = subprocess.run([sys.executable, '-c', probe, str(tree/'src/crapkit/__init__.py')],
        env=env, cwd=data['cwd'], input=data['stdin'], capture_output=True, text=True,
        encoding='utf-8', errors='replace', check=True)
    assert not child.stderr
    results[label] = json.loads(child.stdout)
    assert results[label]['code'] == 0
    assert not {'lizard', 'crapkit.store', 'crapkit.analyze', 'crapkit.ratchet'} & set(results[label]['modules'])
results['added_modules'] = sorted(set(results['current']['modules'])-set(results['release']['modules']))
results['removed_modules'] = sorted(set(results['release']['modules'])-set(results['current']['modules']))
results['added_calls'] = sorted(set(map(tuple, results['current']['calls']))-set(map(tuple, results['release']['calls'])))
results['removed_calls'] = sorted(set(map(tuple, results['release']['calls']))-set(map(tuple, results['current']['calls'])))
(evidence/'advisory-imports.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
for label in ('current', 'release'):
    print(label, [name for name in results[label]['modules'] if name.startswith('crapkit')])
for key in ('added_modules', 'removed_modules', 'added_calls', 'removed_calls'):
    print(key, results[key])
