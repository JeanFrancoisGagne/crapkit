"""Real stdio cancellation stops a request and its writers before replying."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

import pytest

from crapkit.errors import ToolError
from crapkit.locks import exclusive_lock
from hang_guard import CHILD_HOLD, CHILD_WAIT, HANG_SECONDS, exited, wait_until


def fixture(root):
    scratch = root / 'tmp'
    scratch.mkdir()
    (root / 'crapkit.toml').write_text('[crapkit]\ntarget=6\n[[scope]]\nname="src"\n'
                                     'paths=["src"]\nlanguages=["python"]\n', encoding='utf-8')
    hooks = root / 'hooks'
    hooks.mkdir()
    (hooks / 'sitecustomize.py').write_text(
        'import os,sys,time,subprocess\nfrom pathlib import Path\n'
        'if "mcp" in sys.orig_argv:\n'
        '    Path(os.environ["REQUEST_ROOT"],"server.pid").write_text(str(os.getpid()))\n'
        'if "runs" in sys.orig_argv and not Path(os.environ["REQUEST_ROOT"], "release").exists():\n'
        '    from crapkit.locks import exclusive_lock\n'
        '    root=Path(os.environ["REQUEST_ROOT"])\n'
        '    with exclusive_lock(root/"request.lock",label="request"):\n'
        '        child=subprocess.Popen([sys.executable,str(root/"descendant.py")])\n'
        '        until=time.monotonic()+' + CHILD_WAIT + '\n'
        '        while not (root/"child.ready").exists() and time.monotonic()<until: time.sleep(.01)\n'
        '        assert (root/"child.ready").exists()\n'
        '        (root/"ready").touch()\n'
        '        until=time.monotonic()+' + CHILD_HOLD + '\n'
        '        while not (root/"release").exists() and time.monotonic()<until: time.sleep(.01)\n',
        encoding='utf-8')
    (root / 'descendant.py').write_text(
        'import os,time\nfrom pathlib import Path\nfrom crapkit.locks import exclusive_lock\n'
        'root=Path(os.environ["REQUEST_ROOT"])\n'
        'with exclusive_lock(root/"child.lock",label="child"):\n'
        '    (root/"child.ready").touch()\n'
        '    until=time.monotonic()+' + CHILD_HOLD + '\n'
        '    while not (root/"release").exists() and time.monotonic()<until: time.sleep(.01)\n',
        encoding='utf-8')
    return {**os.environ, 'REQUEST_ROOT': str(root), 'TMP': str(scratch),
            'TEMP': str(scratch), 'TMPDIR': str(scratch),
            'PYTHONPATH': os.pathsep.join(filter(None, (str(hooks), os.environ.get('PYTHONPATH'))))}


class Client:
    def __init__(self, root):
        self.root = root
        self.errors = (root / 'server-errors').open('w+b')
        self.process = subprocess.Popen([sys.executable, '-m', 'crapkit', 'mcp', '--repo', str(root)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.errors, text=True, encoding='utf-8',
                                        cwd=root, env=fixture(root))
        self.messages = queue.Queue()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        for line in self.process.stdout:
            self.messages.put(json.loads(line))

    def send(self, method, *, msg_id=None, params=None):
        message = {'jsonrpc': '2.0', 'method': method}
        if msg_id is not None:
            message['id'] = msg_id
        if params is not None:
            message['params'] = params
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def receive(self):
        wait_until(lambda: not self.messages.empty(), self.process,
                   log=self.root / 'server-errors', what='a reply from the server')
        return self.messages.get_nowait()

    def ready(self):
        """The request reaches its startup hold, and no reply comes before it."""
        wait_until(lambda: (self.root / 'ready').exists() or not self.messages.empty(),
                   self.process, log=self.root / 'server-errors',
                   what='the real request reach the startup hold')
        assert self.messages.empty(), list(self.messages.queue)

    def close(self):
        (self.root / 'release').touch()
        if not self.process.stdin.closed:
            self.process.stdin.close()
        exited(self.process, log=self.root / 'server-errors')
        self.reader.join(HANG_SECONDS)
        self.process.stdout.close()
        self.errors.close()


def test_matching_cancellation_stops_the_request_and_keeps_the_session_usable(tmp_path):
    client = Client(tmp_path)
    try:
        client.send('tools/call', msg_id=73, params={'name': 'list_runs', 'arguments': {}})
        client.ready()
        client.send('notifications/cancelled', params={'requestId': 73})
        client.send('ping', msg_id=74)
        assert client.receive() == {'jsonrpc': '2.0', 'id': 74, 'result': {}}
        with exclusive_lock(tmp_path / 'request.lock', label='request'):
            pass
        with exclusive_lock(tmp_path / 'child.lock', label='child'):
            pass
        (tmp_path / 'release').touch()
        client.send('tools/call', msg_id=75, params={'name': 'list_runs', 'arguments': {}})
        reply = client.receive()
        assert reply['id'] == 75
        assert 'result' in reply
        client.send('ping', msg_id=76)
        assert client.receive()['id'] == 76
        assert client.messages.empty(), 'a cancelled request has no late response'
    finally:
        client.close()


def test_eof_stops_an_active_request_before_its_hold_is_released(tmp_path):
    client = Client(tmp_path)
    try:
        client.send('tools/call', msg_id=73, params={'name': 'list_runs', 'arguments': {}})
        client.ready()
        client.process.stdin.close()
        assert exited(client.process, log=tmp_path / 'server-errors') == 0
        with exclusive_lock(tmp_path / 'request.lock', label='request'):
            pass
        with exclusive_lock(tmp_path / 'child.lock', label='child'):
            pass
        assert client.messages.empty()
    finally:
        client.close()


def test_unknown_cancellation_does_not_stop_the_active_request(tmp_path):
    client = Client(tmp_path)
    try:
        client.send('tools/call', msg_id=73, params={'name': 'list_runs', 'arguments': {}})
        client.ready()
        client.send('notifications/cancelled', params={'requestId': '73'})
        client.send('ping', msg_id=74)
        assert client.receive()['id'] == 74
        with pytest.raises(ToolError):
            with exclusive_lock(tmp_path / 'request.lock', label='request'):
                pass
        (tmp_path / 'release').touch()
        assert client.receive()['id'] == 73
    finally:
        client.close()


def stop_server(client):
    pid = int((client.root / 'server.pid').read_text())
    if os.name != 'nt':
        assert pid == client.process.pid
        client.process.kill()
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = wintypes.HANDLE(kernel.OpenProcess(0x1001, False, pid))
    assert handle.value
    try:
        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        assert kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(system), ctypes.byref(user))
        assert kernel.TerminateProcess(handle, 1)
    finally:
        kernel.CloseHandle(handle)


def owned_writers_stopped(root):
    try:
        with exclusive_lock(root / 'request.lock', label='request'):
            with exclusive_lock(root / 'child.lock', label='child'):
                return True
    except ToolError:
        return False


def test_server_death_stops_the_request_and_its_descendant(tmp_path):
    client = Client(tmp_path)
    try:
        client.send('tools/call', msg_id=73, params={'name': 'list_runs', 'arguments': {}})
        client.ready()
        stop_server(client)
        wait_until(lambda: owned_writers_stopped(tmp_path),
                   what='the owned writers stop without releasing the fixture')
        assert list((tmp_path / 'tmp').glob('crapkit-command-*')) == [], \
            'hard server death must not leave named capture directories'
    finally:
        client.close()


def test_a_closed_output_pipe_exits_cleanly_without_closing_client_input(tmp_path):
    environment = fixture(tmp_path)
    process = subprocess.Popen([sys.executable, '-m', 'crapkit', 'mcp', '--repo', str(tmp_path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8',
                               cwd=tmp_path, env=environment)
    try:
        process.stdout.close()
        process.stdin.write('{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        process.stdin.flush()
        assert exited(process) == 0
        assert process.stderr.read() == ''
    finally:
        process.stdin.close()
        exited(process)
        process.stderr.close()
