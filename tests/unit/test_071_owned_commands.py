"""Owned commands stop writers before cancellation returns."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import os
import sys
import time

import pytest

from crapkit import procs
from crapkit.errors import ToolError
from crapkit.locks import exclusive_lock


def ready(path):
    until = time.monotonic() + 30
    while not path.exists() and time.monotonic() < until:
        time.sleep(.01)
    assert path.exists(), "the private writer must start before cancellation"


def test_cancel_stops_live_commands_and_retains_the_lease(tmp_path):
    script = tmp_path / 'writer.py'
    script.write_text(
        'from pathlib import Path\nimport time\n'
        'from crapkit.locks import exclusive_lock\n'
        'with exclusive_lock(Path("writer.lock"), label="writer"):\n'
        '    Path("ready").touch()\n    time.sleep(30)\n', encoding='utf-8')
    with ThreadPoolExecutor(1) as pool:
        with procs.own_processes([tmp_path / 'lease']) as owner:
            future = pool.submit(procs.run_bounded, f'"{sys.executable}" "{script}"',
                                 None, owner=owner, cwd=tmp_path)
            ready(tmp_path / 'ready')
            owner.cancel()
            with pytest.raises(procs.CommandCancelled):
                future.result(timeout=5)
            with exclusive_lock(tmp_path / 'writer.lock', label='writer'):
                pass
            with pytest.raises(ToolError):
                with exclusive_lock(tmp_path / 'lease', label='lease'):
                    pass
            with pytest.raises(procs.CommandCancelled):
                procs.run_bounded(f'"{sys.executable}" "{script}"', None, owner=owner)
        with exclusive_lock(tmp_path / 'lease', label='lease'):
            pass


def test_owned_argv_preserves_literals_streams_and_exit_status(tmp_path):
    words = ['', 'a&echo BAD', '雪', '!literal!', 'two words']
    script = 'import json,sys; print(json.dumps(sys.argv[1:],ensure_ascii=False)); print("diagnostic",file=sys.stderr); sys.exit(7)'
    result = procs.run_owned([sys.executable, '-c', script, *words],
                             capture_output=True, cwd=tmp_path,
                             env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    assert result.returncode == 7
    assert result.stdout == '["", "a&echo BAD", "雪", "!literal!", "two words"]\n'
    assert result.stderr == 'diagnostic\n'


def test_owned_command_keeps_shell_operators_and_an_explicit_deadline(tmp_path):
    result = procs.run_owned('echo first && echo second', capture_output=True, cwd=tmp_path)
    assert result.returncode == 0
    assert [line.strip() for line in result.stdout.splitlines()] == ['first', 'second']
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        procs.run_owned([sys.executable, '-c', 'import time; time.sleep(30)'], .1)
    assert caught.value.timeout == .1


def test_owned_argv_preserves_real_launch_errors(tmp_path):
    missing = str(tmp_path / 'missing-command')
    with pytest.raises(FileNotFoundError):
        procs.run_owned([missing], capture_output=True)


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='fork inheritance is POSIX-only')
def test_forked_child_cannot_keep_an_unrelated_owners_lease_alive(tmp_path):
    context = procs.own_processes([tmp_path / 'lease'])
    context.__enter__()
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(writer)
        os.read(reader, 1)
        os._exit(0)
    os.close(reader)
    with ThreadPoolExecutor(1) as pool:
        closed = pool.submit(context.__exit__, None, None, None)
        try:
            closed.result(timeout=3)
            with exclusive_lock(tmp_path / 'lease', label='lease'):
                pass
        finally:
            os.write(writer, b'x')
            os.close(writer)
            os.waitpid(pid, 0)


def test_cancel_before_registration_never_releases_a_start_gate():
    released = []
    with procs.own_processes(()) as owner:
        owner.cancel()
        owner.cancel()
        with pytest.raises(procs.CommandCancelled):
            owner.register_then(0, lambda: released.append(True))
        assert released == []


def test_outer_cancellation_waits_for_a_nested_owners_writer(tmp_path):
    (tmp_path / 'writer.py').write_text(
        'from pathlib import Path\nimport time\nfrom crapkit.locks import exclusive_lock\n'
        'with exclusive_lock(Path("writer.lock"),label="writer"):\n'
        '    Path("ready").touch()\n    until=time.monotonic()+30\n'
        '    while not Path("release").exists() and time.monotonic()<until: time.sleep(.01)\n',
        encoding='utf-8')
    (tmp_path / 'nested.py').write_text(
        'import sys\nfrom crapkit.procs import run_owned\n'
        'run_owned([sys.executable,"writer.py"])\n', encoding='utf-8')
    try:
        with ThreadPoolExecutor(1) as pool:
            with procs.own_processes([tmp_path / 'outer.lease']) as owner:
                result = pool.submit(procs.run_owned, [sys.executable, 'nested.py'],
                                     owner=owner, cwd=tmp_path)
                ready(tmp_path / 'ready')
                owner.cancel()
                with pytest.raises(procs.CommandCancelled):
                    result.result(timeout=5)
                with exclusive_lock(tmp_path / 'writer.lock', label='writer'):
                    pass
    finally:
        (tmp_path / 'release').touch()
