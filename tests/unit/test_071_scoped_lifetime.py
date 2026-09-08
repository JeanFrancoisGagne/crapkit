"""A scoped test command owns background writers through its real CLI exit."""
import os
from pathlib import Path
import subprocess
import sys
import time

from crapkit.errors import ToolError
from crapkit.locks import exclusive_lock


def fixture(root):
    (root / 'src').mkdir()
    (root / 'src/a.py').write_text('def f():\n    return 1\n', encoding='utf-8')
    (root / 'child.py').write_text(
        'import time\nfrom pathlib import Path\nfrom crapkit.locks import exclusive_lock\n'
        'with exclusive_lock(Path("writer.lock"), label="writer"):\n'
        '    Path("ready").touch()\n'
        '    until=time.monotonic()+30\n'
        '    while not Path("release").exists() and time.monotonic()<until: time.sleep(.01)\n',
        encoding='utf-8')
    (root / 'runner.py').write_text(
        'import subprocess,sys,time\nfrom pathlib import Path\n'
        'subprocess.Popen([sys.executable,"child.py"],stdin=subprocess.DEVNULL, '
        'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
        'until=time.monotonic()+15\n'
        'while not Path("ready").exists() and time.monotonic()<until: time.sleep(.01)\n'
        'assert Path("ready").exists()\nprint("scoped result")\n', encoding='utf-8')
    command = f'"{sys.executable}" runner.py'
    (root / 'crapkit.toml').write_text(
        '[crapkit]\ntarget=6\n[crapkit.scoped_tests]\nsrc = ' + repr(command) + '\n'
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n', encoding='utf-8')


def test_scoped_completion_stops_the_background_writer(tmp_path):
    fixture(tmp_path)
    try:
        done = subprocess.run([sys.executable, '-m', 'crapkit', 'test-scoped', 'src/a.py',
                               '--repo', str(tmp_path)], cwd=tmp_path, capture_output=True,
                              text=True, encoding='utf-8', timeout=30)
        assert done.returncode == 0, done.stderr
        assert done.stdout == 'scoped result\n'
        assert (tmp_path / 'ready').exists()
        with exclusive_lock(tmp_path / 'writer.lock', label='writer'):
            pass
    finally:
        (tmp_path / 'release').touch()
        deadline = time.monotonic() + 5
        while True:
            try:
                with exclusive_lock(tmp_path / 'writer.lock', label='writer'):
                    break
            except ToolError:
                assert time.monotonic() < deadline
                time.sleep(.01)


def test_watch_rescoring_stops_its_background_writer(tmp_path):
    fixture(tmp_path)
    for args in [('init', '-q'), ('add', '.')]:
        subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    (hooks / 'sitecustomize.py').write_text(
        'import sys,runpy,os\n'
        'if "rescore" in sys.orig_argv:\n'
        '    runpy.run_path(os.path.join(os.environ["WATCH_ROOT"],"runner.py"))\n', encoding='utf-8')
    environment = {**os.environ, 'WATCH_ROOT': str(tmp_path),
                   'PYTHONPATH': os.pathsep.join(filter(None, (str(hooks), os.environ.get('PYTHONPATH'))))}
    process = subprocess.Popen([sys.executable, '-m', 'crapkit', 'watch', '--cycles', '1',
                                '--interval', '.2', '--repo', str(tmp_path)], cwd=tmp_path,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', env=environment)
    try:
        assert process.stdout.readline().startswith('watching 1 tracked files')
        source = tmp_path / 'src/a.py'
        stat = source.stat()
        os.utime(source, (stat.st_atime, stat.st_mtime + 10))
        output, errors = process.communicate(timeout=30)
        assert process.returncode == 0, errors
        assert 'scoped result' in output
        assert (tmp_path / 'ready').exists()
        with exclusive_lock(tmp_path / 'writer.lock', label='writer'):
            pass
    finally:
        (tmp_path / 'release').touch()
        process.wait(timeout=10)
