"""Mutation pool reuse cannot overlap a previous command's file ownership."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

from crapkit.errors import ToolError
from crapkit.locks import exclusive_lock
from crapkit.mutate import file_mutants
from crapkit.mutate_pool import run_mutants
from test_r2_execution_lifetime import wait_for


SOURCE = 'def enabled():\n    return True\n'


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'user.name=Test', '-c',
                                    'user.email=test@example.invalid', *args], cwd=root)


def fixture_repo(tmp_path, runner):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / '.gitignore').write_text('.crapkit/\n__pycache__/\n', encoding='utf-8')
    (root / 'm.py').write_text(SOURCE, encoding='utf-8')
    (root / 'runner.py').write_text(runner, encoding='utf-8')
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'mutation fixture')
    return root


def mutation(root):
    mutant = file_mutants(SOURCE, None, 'python')[0]._replace(path='m.py')
    cfg = SimpleNamespace(mutation_workers=1, mutation_timeout_seconds=None,
                          mutation_command=f'"{sys.executable}" runner.py')
    return cfg, mutant


def crashed_writer(tmp_path):
    runner = (
        'from pathlib import Path\nimport os, time\nimport m\n'
        'from crapkit.locks import exclusive_lock\n'
        f'control = Path({str(tmp_path)!r})\n'
        'if not m.enabled():\n'
        '    with exclusive_lock(control / "writer.lock", label="writer"):\n'
        '        (control / "started.part").write_text(str(Path.cwd()))\n'
        '        (control / "started.part").replace(control / "started")\n'
        '        deadline = time.monotonic() + 30\n'
        '        while not (control / "release").exists() and time.monotonic() < deadline:\n'
        '            time.sleep(.02)\n'
        '        Path("m.py").write_text("CORRUPTED_BY_OLD_SUITE\\n")\n'
        '    (control / "finished").touch()\n')
    root = fixture_repo(tmp_path, runner)
    script = tmp_path / 'caller.py'
    script.write_text(
        'from pathlib import Path\nfrom types import SimpleNamespace\nimport os, sys\n'
        'from crapkit.mutate import file_mutants\nfrom crapkit.mutate_pool import run_mutants\n'
        f'root = Path({str(root)!r})\n'
        f'Path({str(tmp_path / "caller-pid")!r}).write_text(str(os.getpid()))\n'
        'mutant = file_mutants((root / "m.py").read_text(), None, "python")[0]._replace(path="m.py")\n'
        'cfg = SimpleNamespace(mutation_workers=1, mutation_timeout_seconds=None, '
        'mutation_command=f\'"{sys.executable}" runner.py\')\n'
        'run_mutants(root, cfg, [mutant], lambda *a: None)\n', encoding='utf-8')
    return root, script


def wait_for_pool(root):
    deadline = time.monotonic() + 15
    while True:
        try:
            with exclusive_lock(root / '.crapkit' / 'mutate-pool.lock', label='pool'):
                return
        except ToolError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.02)


def test_a_dead_mutation_caller_stops_the_suite_before_the_pool_can_be_reused(tmp_path):
    root, script = crashed_writer(tmp_path)
    stopped = False
    with (tmp_path / 'caller.log').open('w') as log:
        caller = subprocess.Popen([sys.executable, str(script)], stdout=log, stderr=log)
        try:
            wait_for(tmp_path / 'started')
            os.kill(int((tmp_path / 'caller-pid').read_text()), signal.SIGTERM)
            caller.wait(timeout=15)
            wait_for_pool(root)
            with exclusive_lock(tmp_path / 'writer.lock', label='writer'):
                stopped = True
            old_tree = Path((tmp_path / 'started').read_text())
            (root / 'runner.py').write_text('import m\nassert m.enabled()\n', encoding='utf-8')
            cfg, mutant = mutation(root)
            assert run_mutants(root, cfg, [mutant], lambda *a: None) == [True]
            assert old_tree == root / '.crapkit' / 'mutate-pool' / 'w0'
            assert (old_tree / 'm.py').read_text() == SOURCE
            assert (root / 'm.py').read_text() == SOURCE
        finally:
            (tmp_path / 'release').touch()
            if not stopped and (tmp_path / 'started').exists():
                wait_for(tmp_path / 'finished')
            if caller.poll() is None:
                caller.kill()
                caller.wait(timeout=15)


def test_wait_for_returns_the_moment_a_marker_exists_not_when_it_is_filled(tmp_path):
    """Why every handshake marker here publishes through a rename.

    wait_for polls exists(), and write_text creates the file before it writes the
    bytes. A reader that lands in that gap reads '', and Path('') is Path('.'),
    which is how a killed caller's pool tree once came back as the working
    directory and failed a run whose product code was correct.
    """
    marker = tmp_path / 'started'
    handle = marker.open('w')
    try:
        wait_for(marker)
        assert marker.read_text() == ''
    finally:
        handle.close()


def test_a_marker_published_through_a_rename_carries_its_content(tmp_path):
    """The rename is atomic, so the name appears already holding its payload."""
    part = tmp_path / 'started.part'
    part.write_text(str(tmp_path))
    part.replace(tmp_path / 'started')

    wait_for(tmp_path / 'started')

    assert Path((tmp_path / 'started').read_text()) == tmp_path
