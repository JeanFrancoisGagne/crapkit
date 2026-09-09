"""Public mutation cancellation stops work before releasing its checkout."""
from concurrent.futures import Future, TimeoutError
from contextlib import nullcontext
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

import pytest

from crapkit.cli import main
from crapkit import mutate_pool, procs
from crapkit.locks import exclusive_lock
from mutation_fixtures import holding_suite, running_mutation, stop_caller, wait_for


SOURCE = 'def choose(x):\n    if x > 10:\n        return 10\n    return x\n'

# One suite's evidence record, as the miniature runner below writes it. The pid
# carries identity: `time.time_ns()` is a 15.625 ms tick on Windows before
# CPython 3.13, so two shards recording inside one tick shared a filename and the
# second `replace()` destroyed the first. The ns stays first to keep `sorted()`
# in the reader chronological.
RECORD = ('record = events / (str(time.time_ns()) + "-" + str(os.getpid()) + ".tmp")\n'
          'record.write_text(json.dumps(dict(phase=phase,pid=os.getpid(),cwd=str(Path.cwd()))))\n'
          'record.replace(record.with_suffix(".json"))\n')


def git(root, *args):
    return subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=t@t',
                           *args], cwd=root, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def mutation_repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    events = tmp_path / 'events'
    events.mkdir()
    (root / 'app.py').write_text(SOURCE, encoding='utf-8')
    runner = ('from pathlib import Path\nimport json,os,time\n'
              f'events = Path({str(events)!r})\n'
              f'phase = "baseline" if Path("app.py").read_text() == {SOURCE!r} else "mutant"\n'
              + RECORD
              + 'time.sleep(.1)\n')
    (root / 'suite.py').write_text(runner, encoding='utf-8')
    command = f'"{sys.executable}" suite.py'
    config = ('[crapkit]\nmutation_workers=1\nmutation_timeout_seconds=10\n'
              f'mutation_command={json.dumps(command)}\n'
              '[[scope]]\nname="py"\npaths=["app.py"]\nlanguages=["python"]\n')
    (root / 'crapkit.toml').write_text(config, encoding='utf-8')
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'fixture')
    return root, events


def recorded(events):
    return [json.loads(path.read_text()) for path in sorted(events.glob('*.json'))]


def interrupt_future_when(monkeypatch, ready):
    wait = Future.result
    interrupted = []

    def result(future, timeout=None):
        while True:
            if not interrupted and ready():
                interrupted.append(True)
                raise KeyboardInterrupt
            try:
                return wait(future, timeout=.02)
            except TimeoutError:
                pass

    monkeypatch.setattr(Future, 'result', result)
    return interrupted


def invoke(root):
    return main(['mutate', '--repo', str(root), '--files', 'app.py', '--json'])


def test_cancelling_mutation_starts_no_later_suite(mutation_repo, monkeypatch):
    root, events = mutation_repo
    interrupted = interrupt_future_when(
        monkeypatch, lambda: any(row['phase'] == 'mutant' for row in recorded(events)))
    with pytest.raises(KeyboardInterrupt):
        invoke(root)
    assert interrupted
    assert [row['phase'] for row in recorded(events)] == ['baseline', 'mutant']
    assert (root / 'app.py').read_text() == SOURCE
    assert (root / '.crapkit/mutate-pool/w0/app.py').read_text() == SOURCE


def test_cancellation_stops_the_active_writer_before_returning_the_pool(mutation_repo, monkeypatch):
    root, events = mutation_repo
    holding_suite(root, events)
    interrupt_future_when(monkeypatch, lambda: (events / 'started').exists())
    try:
        with pytest.raises(KeyboardInterrupt):
            invoke(root)
        assert [row['phase'] for row in recorded(events)] == ['baseline', 'mutant']
        with exclusive_lock(events / 'writer.lock', label='writer stopped'):
            assert not (events / 'finished').exists()
        with exclusive_lock(root / '.crapkit/mutate-pool.lock', label='pool returned'):
            assert (root / '.crapkit/mutate-pool/w0/app.py').read_text() == SOURCE
    finally:
        (events / 'release').touch()


def test_cancellation_stops_both_shards_before_either_starts_its_next_suite(mutation_repo, monkeypatch):
    root, events = mutation_repo
    source = SOURCE.replace('    return x\n', '    if x < 0:\n        return 0\n    return x\n')
    (root / 'app.py').write_text(source)
    holding_suite(root, events)
    script = (root / 'suite.py').read_text().replace(repr(SOURCE), repr(source))
    for name in ['writer.lock', 'started', 'finished']:
        script = script.replace(f'"{name}"', f'(Path.cwd().name + "-{name}")')
    (root / 'suite.py').write_text(script)
    config = root / 'crapkit.toml'
    config.write_text(config.read_text().replace('mutation_workers=1', 'mutation_workers=2'))
    interrupt_future_when(monkeypatch, lambda: all((events / (name + '-started')).exists()
                                                   for name in ['w0', 'w1']))
    try:
        with pytest.raises(KeyboardInterrupt):
            invoke(root)
        assert [row['phase'] for row in recorded(events)] == ['baseline', 'mutant', 'mutant']
        for name in ['w0', 'w1']:
            with exclusive_lock(events / (name + '-writer.lock'), label='writer stopped'):
                assert not (events / (name + '-finished')).exists()
            assert (root / '.crapkit/mutate-pool' / name / 'app.py').read_text() == source
    finally:
        (events / 'release').touch()


def test_cancellation_during_baseline_never_starts_a_mutant(mutation_repo, monkeypatch):
    root, events = mutation_repo
    holding_suite(root, events, 'baseline')
    baseline = mutate_pool.require_live_suite

    def interrupted_wait(process, timeout):
        wait_for(events / 'started')
        assert process.poll() is None
        raise KeyboardInterrupt

    def interrupted_baseline(tree, cfg, *, owner=None):
        with monkeypatch.context() as scoped:
            scoped.setattr(procs, '_wait_command', interrupted_wait)
            return baseline(tree, cfg, owner=owner)

    monkeypatch.setattr(mutate_pool, 'require_live_suite', interrupted_baseline)
    try:
        with pytest.raises(KeyboardInterrupt):
            invoke(root)
        assert [row['phase'] for row in recorded(events)] == ['baseline']
        with exclusive_lock(events / 'writer.lock', label='writer stopped'):
            assert not (events / 'finished').exists()
    finally:
        (events / 'release').touch()


@pytest.mark.parametrize('pool_busy', [False, True])
def test_cancellation_during_preparation_joins_builders_before_removal(mutation_repo, monkeypatch, pool_busy):
    root, events = mutation_repo
    built = threading.Event()
    release = threading.Event()
    add = mutate_pool.worktree_add

    def finishing_add(repo, tree, *, owner=None):
        add(repo, tree, owner=owner)
        built.set()
        assert release.wait(30)
        (tree / 'builder-finished').touch()

    def ready():
        if not built.is_set():
            return False
        release.set()
        return True

    monkeypatch.setattr(mutate_pool, 'worktree_add', finishing_add)
    interrupt_future_when(monkeypatch, ready)
    peer = exclusive_lock(root / '.crapkit/mutate-pool.lock', label='other run') if pool_busy else nullcontext()
    with peer:
        try:
            with pytest.raises(KeyboardInterrupt):
                invoke(root)
            assert recorded(events) == []
            assert not (root / '.crapkit/mutate-pool/w0').exists()
            assert list((root / '.crapkit/mutate-tmp').glob('*')) == []
            assert len(git(root, 'worktree', 'list', '--porcelain').split('worktree ')) == 2
        finally:
            release.set()


@pytest.mark.skipif(os.name == 'nt', reason='native SIGINT delivery is POSIX; Windows uses the wait boundary above')
def test_native_sigint_stops_dispatch_and_the_writer_before_exit(mutation_repo):
    root, events = mutation_repo
    holding_suite(root, events)
    with running_mutation(root, events) as caller:
        stop_caller(caller, events, signal.SIGINT)
        assert caller.wait(timeout=15) == 130
        assert (events / 'interrupted').exists()
        assert [row['phase'] for row in recorded(events)] == ['baseline', 'mutant']
        with exclusive_lock(events / 'writer.lock', label='writer stopped'):
            assert not (events / 'finished').exists()
        assert (root / '.crapkit/mutate-pool/w0/app.py').read_text() == SOURCE


def test_two_shards_recording_in_one_clock_tick_keep_both_records(tmp_path):
    """Two shards that record inside one clock tick must leave two records.

    `time.time_ns()` is a 15.625 ms tick on Windows before CPython 3.13, which
    reads GetSystemTimeAsFileTime, so naming a record after the instant gave two
    shards the same filename and the second `replace()` destroyed the first. The
    reader then counted one mutant where two ran, and only windows-3.11 and
    windows-3.12 ever saw it. Identity belongs to the writer, not the clock.
    """
    events = tmp_path / 'events'
    events.mkdir()
    script = tmp_path / 'writer.py'
    script.write_text('from pathlib import Path\nimport json,os,time\n'
                      'time.time_ns = lambda: 1700000000000000000\n'
                      f'events = Path({str(events)!r})\n'
                      'phase = "mutant"\n' + RECORD, encoding='utf-8')

    for _ in range(2):
        subprocess.run([sys.executable, str(script)], check=True, cwd=tmp_path)

    assert len(list(events.glob('*.json'))) == 2, [p.name for p in events.iterdir()]
