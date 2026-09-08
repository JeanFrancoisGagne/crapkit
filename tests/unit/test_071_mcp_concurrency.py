"""Controlled ownership events drive the public stdio loop's startup races."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import io
import json
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from crapkit import _mcp_stdio, mcp_server


class Input:
    def __init__(self):
        self.frames = queue.Queue()

    def readline(self):
        return self.frames.get(timeout=10)

    def send(self, value):
        self.frames.put(json.dumps(value) + '\n')


def streams(monkeypatch, root):
    (root / 'crapkit.toml').write_text('[crapkit]\ntarget=6\n', encoding='utf-8')
    source, target = Input(), io.StringIO()
    monkeypatch.setattr(sys, 'stdin', source)
    monkeypatch.setattr(sys, 'stdout', target)
    return source, target


def call(source):
    source.send({'jsonrpc': '2.0', 'id': 73, 'method': 'tools/call',
                 'params': {'name': 'list_runs', 'arguments': {}}})


def test_cancellation_during_owner_startup_never_dispatches_the_cli(monkeypatch, tmp_path):
    source, target = streams(monkeypatch, tmp_path)
    entered, release, waiting, closed = (threading.Event() for _ in range(4))
    calls = []

    class Lock:
        def __init__(self):
            self.actual = threading.Lock()

        def __enter__(self):
            if self.actual.locked():
                waiting.set()
            self.actual.acquire()

        def __exit__(self, *_):
            self.actual.release()

    @contextmanager
    def owner(*_):
        entered.set()
        assert release.wait(5)
        try:
            yield SimpleNamespace(cancel=lambda: None)
        finally:
            closed.set()

    def run(argv, **_):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{}', '')

    monkeypatch.setattr(_mcp_stdio, 'threading', SimpleNamespace(**{**vars(threading), 'Lock': Lock}))
    monkeypatch.setattr(_mcp_stdio, 'own_processes', owner)
    monkeypatch.setattr(mcp_server, 'run_owned', run)
    with ThreadPoolExecutor(1) as workers:
        served = workers.submit(mcp_server.serve, tmp_path)
        try:
            call(source)
            assert entered.wait(5)
            source.send({'jsonrpc': '2.0', 'method': 'notifications/cancelled',
                         'params': {'requestId': 73}})
            assert waiting.wait(5), 'the input loop must reach cancellation during startup'
        finally:
            release.set()
            source.frames.put('')
        assert served.result(timeout=5) == 0
    assert calls == [], 'a cancelled startup must never dispatch its CLI'
    assert closed.is_set()
    assert target.getvalue() == ''


def test_failed_cancellation_still_joins_work_and_closes_ownership(monkeypatch, tmp_path):
    source, _ = streams(monkeypatch, tmp_path)
    running, released, closed = (threading.Event() for _ in range(3))
    timeouts = []

    def cancel():
        raise RuntimeError('cancel-fault')

    @contextmanager
    def owner(*_):
        try:
            yield SimpleNamespace(cancel=cancel)
        finally:
            released.set()
            closed.set()

    def run(argv, **_):
        running.set()
        if not released.wait(1):
            timeouts.append('command remained alive before owner teardown')
        return subprocess.CompletedProcess(argv, 0, '{}', '')

    monkeypatch.setattr(_mcp_stdio, 'own_processes', owner)
    monkeypatch.setattr(mcp_server, 'run_owned', run)
    with ThreadPoolExecutor(1) as workers:
        served = workers.submit(mcp_server.serve, tmp_path)
        call(source)
        assert running.wait(5)
        source.frames.put('')
        with pytest.raises(RuntimeError, match='cancel-fault'):
            served.result(timeout=5)
    assert closed.is_set(), 'ownership teardown still runs after a cancellation failure'
    assert timeouts == [], 'failed cancellation must close ownership before joining the live request'


def test_a_cancellation_request_with_an_id_remains_an_unknown_rpc(monkeypatch, tmp_path):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'notifications/cancelled',
               'params': {'requestId': 73}}
    target = io.StringIO()
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request) + '\n'))
    monkeypatch.setattr(sys, 'stdout', target)
    assert mcp_server.serve(tmp_path) == 0
    reply = json.loads(target.getvalue())
    assert reply['id'] == 1
    assert reply['error']['code'] == -32601
