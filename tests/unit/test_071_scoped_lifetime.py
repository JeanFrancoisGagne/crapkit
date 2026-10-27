"""A scoped test command owns background writers through its real CLI exit."""
import os
from pathlib import Path
import subprocess
import sys

from crapkit.errors import ToolError
from crapkit.locks import exclusive_lock
from hang_guard import CHILD_HOLD, CHILD_WAIT, communicate, exited, next_line, run, wait_until


def fixture(root):
    (root / 'src').mkdir()
    (root / 'src/a.py').write_text('def f():\n    return 1\n', encoding='utf-8')
    (root / 'child.py').write_text(
        'import os,time\nfrom pathlib import Path\nfrom crapkit.locks import exclusive_lock\n'
        'with exclusive_lock(Path("writer.lock"), label="writer"):\n'
        '    Path("ready").touch()\n'
        '    until=time.monotonic()+' + CHILD_HOLD + '\n'
        '    while not Path("release").exists() and time.monotonic()<until: time.sleep(.01)\n',
        encoding='utf-8')
    (root / 'runner.py').write_text(
        'import os,subprocess,sys,time\nfrom pathlib import Path\n'
        'subprocess.Popen([sys.executable,"child.py"],stdin=subprocess.DEVNULL, '
        'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
        'until=time.monotonic()+' + CHILD_WAIT + '\n'
        'while not Path("ready").exists() and time.monotonic()<until: time.sleep(.01)\n'
        'assert Path("ready").exists()\nprint("scoped result")\n', encoding='utf-8')
    command = f'"{sys.executable}" runner.py'
    (root / 'crapkit.toml').write_text(
        '[crapkit]\ntarget=6\n[crapkit.scoped_tests]\nsrc = ' + repr(command) + '\n'
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n', encoding='utf-8')


def writer_stopped(root):
    try:
        with exclusive_lock(root / 'writer.lock', label='writer'):
            return True
    except ToolError:
        return False


def test_scoped_completion_stops_the_background_writer(tmp_path):
    fixture(tmp_path)
    try:
        done = run([sys.executable, '-m', 'crapkit', 'test-scoped', 'src/a.py',
                    '--repo', str(tmp_path)], cwd=tmp_path, text=True, encoding='utf-8')
        assert done.returncode == 0, done.stderr
        assert done.stdout == 'scoped result\n'
        assert (tmp_path / 'ready').exists()
        with exclusive_lock(tmp_path / 'writer.lock', label='writer'):
            pass
    finally:
        (tmp_path / 'release').touch()
        wait_until(lambda: writer_stopped(tmp_path), what='the background writer stop')


def test_watch_rescoring_stops_its_background_writer(tmp_path):
    fixture(tmp_path)
    for args in [('init', '-q'), ('add', '.')]:
        subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    # `--cycles 1` polls once, .2 s after the banner. A test the host scheduled
    # later than that edited after the only poll, and watch exited 0 with no
    # rescore, so the poll waits (at most CHILD_WAIT) for the test's `edited` mark.
    (hooks / 'sitecustomize.py').write_text(
        'import sys,runpy,os,time\n'
        'if "rescore" in sys.orig_argv:\n'
        '    runpy.run_path(os.path.join(os.environ["WATCH_ROOT"],"runner.py"))\n'
        'elif "watch" in sys.orig_argv:\n'
        '    from crapkit.cli import admin\n'
        '    poll = admin.poll\n'
        '    def after_the_edit(*args):\n'
        '        until = time.monotonic() + ' + CHILD_WAIT + '\n'
        '        edited = os.path.join(os.environ["WATCH_ROOT"], "edited")\n'
        '        while not os.path.exists(edited) and time.monotonic() < until: time.sleep(.01)\n'
        '        return poll(*args)\n'
        '    admin.poll = after_the_edit\n', encoding='utf-8')
    environment = {**os.environ, 'WATCH_ROOT': str(tmp_path),
                   'PYTHONPATH': os.pathsep.join(filter(None, (str(hooks), os.environ.get('PYTHONPATH'))))}
    process = subprocess.Popen([sys.executable, '-m', 'crapkit', 'watch', '--cycles', '1',
                                '--interval', '.2', '--repo', str(tmp_path)], cwd=tmp_path,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', env=environment)
    try:
        assert next_line(process).startswith('watching 1 file(s) in scope')
        source = tmp_path / 'src/a.py'
        stat = source.stat()
        source.write_text('def f():\n    return 2\n', encoding='utf-8')  # watch rescores new bytes, not a touch
        os.utime(source, (stat.st_atime, stat.st_mtime + 10))
        (tmp_path / 'edited').touch()
        output, errors = communicate(process)
        assert process.returncode == 0, errors
        assert 'scoped result' in output
        assert (tmp_path / 'ready').exists()
        with exclusive_lock(tmp_path / 'writer.lock', label='writer'):
            pass
    finally:
        (tmp_path / 'edited').touch()  # a test that failed before its edit frees the poll
        (tmp_path / 'release').touch()
        exited(process)
