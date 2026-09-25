from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import crapkit
from crapkit import gitio, churn, coupling, mutate_pool

ROOT = Path(r"C:\Users\jfgag\crapkit")
assert Path(crapkit.__file__).resolve() == ROOT / 'src/crapkit/__init__.py'


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, text=True, encoding='utf-8',
                          capture_output=True, check=True).stdout


with TemporaryDirectory() as directory:
    root = Path(directory)
    git(root, 'init', '-q')
    path = root / ' leading.py'
    path.write_text('def f():\n    return 1\n', encoding='utf-8')
    (root / 'ordinary.py').write_text('def f():\n    return 1\n', encoding='utf-8')
    git(root, 'add', '--', '.')
    git(root, '-c', 'user.name=Probe', '-c', 'user.email=probe@example.test',
        'commit', '-qm', 'fixture')
    path.write_text('def f():\n    return 2\n', encoding='utf-8')
    log = ''.join(gitio.churn_log_lines(root, 12))
    print(json.dumps({'tracked': gitio.ls_files(root), 'raw_diff_names': git(root, 'diff', '--name-only'),
                      'unstaged_paths': sorted(gitio.unstaged_paths(root)),
                      'status_names': gitio.status_names(root), 'raw_log': log,
                      'churn': {key: list(value) for key, value in churn.parse_git_log(log).items()},
                      'coupling_unfiltered': coupling.change_coupling(log, min_support=1),
                      'coupling_tracked': coupling.change_coupling(log, min_support=1, tracked=set(gitio.ls_files(root))),
                      'snapshot': {key: None if value is None else value[0].decode('utf-8')
                                   for key, value in mutate_pool._input_snapshot(root, ['ordinary.py'], Path('.crapkit'))[1].items()}}, indent=2))
