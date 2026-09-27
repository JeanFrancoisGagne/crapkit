"""Public CLI replay on a disposable repository; no root tests or source writes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import crapkit

ROOT = Path(r'C:\Users\jfgag\crapkit')
assert Path(crapkit.__file__).resolve() == ROOT / 'src/crapkit/__init__.py'
print('VERIFIED_IMPORT', crapkit.__file__, flush=True)
CLI = ("import crapkit; from pathlib import Path; "
       f"assert Path(crapkit.__file__).resolve()==Path({str(ROOT / 'src/crapkit/__init__.py')!r}); "
       "from crapkit.cli import main; raise SystemExit(main())")

def git(root, *args):
    return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                    *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()

def cli(root, *args):
    proc = subprocess.run([sys.executable, '-c', CLI, *args, '--repo', str(root), '--json'],
                          env={**os.environ, 'PYTHONPATH': str(ROOT/'src'), 'PYTHONDONTWRITEBYTECODE':'1'},
                          cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
    return {'argv': list(args), 'returncode': proc.returncode, 'stdout': json.loads(proc.stdout),
            'stderr': proc.stderr}

RUNNER = r"""
import json
from pathlib import Path
state = Path('tests/state.txt').read_text().strip()
hit = int(state == 'pass')
folder = Path('.crapkit')
folder.mkdir(exist_ok=True)
count = folder / 'counter.txt'
count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))
loc = {'start': {'line': 1, 'column': 0}, 'end': {'line': 3, 'column': 1}}
data = {'src/app.js': {'path': 'src/app.js',
    'fnMap': {'0': {'name': 'f', 'decl': loc, 'loc': loc}}, 'f': {'0': hit},
    'statementMap': {'0': {'start': {'line': 2, 'column': 2}, 'end': {'line': 2, 'column': 11}}},
    's': {'0': hit}, 'branchMap': {}, 'b': {}}}
(folder/'cov.json').write_text(json.dumps(data), encoding='utf-8')
failure = '' if hit else '<failure message="changed test fails"/>'
(folder/'junit.xml').write_text('<testsuites><testsuite tests="1"><testcase classname="tests.test_app" name="test_f">'
                              +failure+'</testcase></testsuite></testsuites>', encoding='utf-8')
"""
with tempfile.TemporaryDirectory(prefix='reuse-fixture-', dir=Path(__file__).parent) as folder:
    root = Path(folder)
    git(root, 'init', '--quiet')
    (root/'src').mkdir()
    (root/'tests').mkdir()
    (root/'src/app.js').write_text('function f() {\n  return 1;\n}\n')
    (root/'tests/state.txt').write_text('pass')
    (root/'measure.py').write_text(RUNNER, encoding='utf-8')
    (root/'.gitignore').write_text('.crapkit/\n__pycache__/\n')
    command = f'"{sys.executable}" measure.py'
    config = ('[[scope]]\nname="src"\npaths=["src"]\nlanguages=["javascript"]\n'
              '[[lane]]\nname="js"\nparser="istanbul"\nscopes=["src"]\n'
              f'command={json.dumps(command)}\nartifact=".crapkit/cov.json"\n'
              'results_artifact=".crapkit/junit.xml"\n')
    (root/'crapkit.toml').write_text(config)
    git(root, 'add', '.')
    git(root, 'commit', '--quiet', '-m', 'fixture')
    first = cli(root, 'coverage')
    assert first['returncode'] == 0, first
    (root/'tests/state.txt').write_text('fail')
    reused = cli(root, 'verify', '--reuse-unchanged', '--no-tighten')
    count_reused = int((root/'.crapkit/counter.txt').read_text())
    fresh = cli(root, 'verify', '--no-tighten')
    count_fresh = int((root/'.crapkit/counter.txt').read_text())
    assert reused['returncode'] == 0 and count_reused == 1, (reused, count_reused)
    assert fresh['returncode'] == 8 and count_fresh == 2, (fresh, count_fresh)
    (root/'crapkit.toml').write_text(config.replace('paths=["src"]', 'paths="src"'))
    bad_paths = cli(root, 'inventory')
    assert bad_paths['returncode'] == 0 and bad_paths['stdout']['functions'] == 0, bad_paths
    output = {'initial': first, 'reused_after_test_edit': reused, 'fresh_after_same_test_edit': fresh,
              'runner_calls_after_reuse': count_reused, 'runner_calls_after_fresh': count_fresh,
              'bare_string_scope_paths': bad_paths}
(Path(__file__).parent/'execution-reuse-result.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
print(json.dumps(output, indent=2))
