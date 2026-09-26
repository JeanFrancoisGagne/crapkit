"""Owned commands stop writers before cancellation returns."""
from concurrent.futures import ThreadPoolExecutor
import subprocess
import os
import sys

import pytest

from crapkit import procs
from crapkit.errors import ToolError
from crapkit.locks import exclusive_lock
from hang_guard import CHILD_HOLD, HANG_SECONDS, wait_for
from legacy_locale import latin1_env


def test_cancel_stops_live_commands_and_retains_the_lease(tmp_path):
    script = tmp_path / 'writer.py'
    script.write_text(
        'from pathlib import Path\nimport os, time\n'
        'from crapkit.locks import exclusive_lock\n'
        'with exclusive_lock(Path("writer.lock"), label="writer"):\n'
        '    Path("ready").touch()\n    time.sleep(' + CHILD_HOLD + ')\n', encoding='utf-8')
    with ThreadPoolExecutor(1) as pool:
        with procs.own_processes([tmp_path / 'lease']) as owner:
            future = pool.submit(procs.run_bounded, f'"{sys.executable}" "{script}"',
                                 None, owner=owner, cwd=tmp_path)
            wait_for(tmp_path / 'ready')
            owner.cancel()
            with pytest.raises(procs.CommandCancelled):
                future.result(timeout=HANG_SECONDS)
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
            closed.result(timeout=HANG_SECONDS)
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
        'from pathlib import Path\nimport os, time\nfrom crapkit.locks import exclusive_lock\n'
        'with exclusive_lock(Path("writer.lock"),label="writer"):\n'
        '    Path("ready").touch()\n    until=time.monotonic()+' + CHILD_HOLD + '\n'
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
                wait_for(tmp_path / 'ready')
                owner.cancel()
                with pytest.raises(procs.CommandCancelled):
                    result.result(timeout=HANG_SECONDS)
                with exclusive_lock(tmp_path / 'writer.lock', label='writer'):
                    pass
    finally:
        (tmp_path / 'release').touch()


# crapkit's own helpers, the POSIX start gate and the measurement owner, are
# Python processes that take the command and the lease paths as text and hand
# them to the OS themselves. Started under a Latin-1 locale while crapkit runs
# in UTF-8 mode (the restart cli.main makes), each spelled `café` as b"caf\xe9":
# the command got an argument naming no file, which is how the MCP server's
# brief on pkg/café.py answered "no function", and the owner locked a file
# beside the one crapkit meant, so the lease guarded nothing.
HELPER = '''import os, sys
from pathlib import Path
from crapkit import procs
from crapkit.errors import ToolError
from crapkit.locks import exclusive_lock

where = Path(sys.argv[1])
lease = where / "caf\u00e9" / "lease"
probe = "import os, sys; sys.stdout.write(os.fsencode(sys.argv[1]).hex())"
with procs.own_processes([lease]) as owner:
    passed = procs.run_owned([sys.executable, "-c", probe, "caf\u00e9"], owner=owner,
                             capture_output=True).stdout
    try:
        with exclusive_lock(lease, label="lease"):
            held = False
    except ToolError:
        held = True
print(passed, os.fsencode("caf\u00e9").hex(), held, sorted(os.listdir(os.fsencode(where))))
'''
MODES = {"parent-utf8-mode": ["-X", "utf8"], "parent-locale-mode": ["-X", "utf8=0"]}


@pytest.mark.parametrize("locale", ["latin1", "c-utf8"])
@pytest.mark.parametrize("mode", list(MODES))
def test_a_helper_hands_the_os_the_bytes_its_parent_would(tmp_path, locale, mode):
    env = dict(os.environ, PYTHONUTF8="0", LC_ALL="C.UTF-8", LANG="C.UTF-8")
    if locale == "latin1":
        env.update(latin1_env(tmp_path / "locales"))
    where = tmp_path / "where"
    where.mkdir()

    result = subprocess.run([sys.executable, *MODES[mode], "-c", HELPER, str(where)], env=env,
                            capture_output=True, text=True, encoding="ascii", errors="replace",
                            timeout=HANG_SECONDS)

    assert result.returncode == 0, result.stderr
    passed, meant, held, names = result.stdout.strip().split(" ", 3)
    assert passed == meant, result.stdout
    assert held == "True", result.stdout
    assert names == repr([bytes.fromhex(meant)]), result.stdout
