"""Two CLI processes cannot publish each other's measurement artifacts."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_measurement_inputs_e2e import measured_repo, run_cli  # noqa: F401


@pytest.fixture(autouse=True)
def private_coordination_home(tmp_path, monkeypatch):
    home = tmp_path / "coordination-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


PAUSE = """
def pause_measurement(folder):
    (folder/'ready').write_text('ready')
    while not (folder/'release').exists():
        time.sleep(.02)
"""

PAUSED = PAUSE + '''import sys, time
from pathlib import Path
import crapkit.lanes as lanes
import crapkit.cli.scoring as scoring
from crapkit.cli import main
root = Path(sys.argv[1])
module, name = (lanes, '_read_and_parse') if sys.argv[2] == 'parse' else (scoring, '_collect_lanes')
original = getattr(module, name)
def pause(*args, **kwargs):
    pause_measurement(root/'.crapkit')
    return original(*args, **kwargs)
setattr(module, name, pause)
raise SystemExit(main(['coverage', '--repo', str(root), '--json']))
'''
WAIT = '''
import time
from crapkit.locks import exclusive_lock
if os.environ.get('CRAPKIT_PAUSE_MEASUREMENT'):
    with exclusive_lock(folder/'suite-writer.lock', label='old suite'):
        pause_measurement(folder)
        (folder/'late-write').write_text('suite was still alive')
if (folder/'check-writer').exists():
    with exclusive_lock(folder/'suite-writer.lock', label='old suite'):
        (folder/'writer-released').write_text('released before measurement')
'''
RETEST = '''import json, sys
from pathlib import Path
from crapkit.config import load_config_text
from crapkit.lanes import retest_lane
root = Path(sys.argv[1])
lane = load_config_text((root/'crapkit.toml').read_text(encoding='utf-8')).lanes[0]
lane = lane._replace(retest_command=lane.command)
print(json.dumps(sorted(retest_lane(root, lane, {'tests.test_app::test_f'}))))
'''


def wait_for(path, process):
    until = time.monotonic() + 15
    while not path.exists() and process.poll() is None and time.monotonic() < until:
        time.sleep(.02)
    assert path.exists(), process.communicate(timeout=10)


def drain(process):
    try:
        process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=10)
        raise


@pytest.fixture
def paused_measurement(measured_repo):
    processes = []
    def start(phase):
        environment = dict(os.environ)
        if phase in ('execute', 'retest'):
            script = measured_repo / 'measure.py'
            script.write_text(PAUSE + script.read_text().replace("count = folder /", WAIT + "\ncount = folder /"))
            environment['CRAPKIT_PAUSE_MEASUREMENT'] = '1'
            command = [sys.executable, '-m', 'crapkit', 'coverage', '--repo', str(measured_repo), '--json']
            if phase == 'retest':
                command = [sys.executable, '-c', RETEST, str(measured_repo)]
        else:
            command = [sys.executable, '-c', PAUSED, str(measured_repo), phase]
        process = subprocess.Popen(command, cwd=measured_repo, env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8')
        processes.append(process)
        wait_for(measured_repo / '.crapkit/ready', process)
        return process
    yield start
    (measured_repo / '.crapkit/release').touch()
    with ExitStack() as cleanup:
        for process in processes:
            cleanup.callback(drain, process)


@pytest.mark.parametrize('phase', ['execute', 'parse', 'stamp'])
def test_one_owner_spans_execution_parsing_and_stamp_publication(measured_repo, paused_measurement, phase):
    first = paused_measurement(phase)
    second = run_cli(measured_repo, 'coverage', '--json')
    assert second.returncode == 5, second.stdout + second.stderr
    assert 'measurement already in use' in second.stderr
    (measured_repo / '.crapkit/release').touch()
    out, err = first.communicate(timeout=30)
    assert first.returncode == 0, out + err
    assert json.loads(out)['crap_load'] == 1
    if phase == 'execute':
        assert (measured_repo/'.crapkit/late-write').exists()


@pytest.mark.parametrize('different_temp', [False, True])
@pytest.mark.parametrize('different_resources', [False, True])
def test_shared_absolute_artifacts_have_one_owner_across_checkouts(measured_repo, paused_measurement, tmp_path, different_temp, different_resources):
    other = tmp_path / 'other'
    shutil.copytree(measured_repo, other)
    config = other / 'crapkit.toml'
    text = config.read_text()
    for name in ('cov.json', 'junit.xml'):
        text = text.replace(json.dumps('.crapkit/' + name), json.dumps(str(measured_repo / '.crapkit' / name)))
    config.write_text(text, encoding='utf-8')
    paused_measurement('execute')
    alternative = tmp_path / 'another-temp'
    alternative.mkdir()
    environment = {'TEMP': str(alternative), 'TMP': str(alternative)} if different_temp else {}
    if different_resources:
        environment['CRAPKIT_RESOURCE_DIR'] = str(tmp_path / 'another-resource-domain')
    second = run_cli(other, 'coverage', '--json', env_extra=environment)
    assert second.returncode == 5, second.stdout + second.stderr
    assert 'measurement already in use' in second.stderr


def test_independent_checkout_artifacts_can_run_at_the_same_time(measured_repo, paused_measurement, tmp_path):
    other = tmp_path / 'independent'
    subprocess.run(['git', 'worktree', 'add', '--detach', str(other), 'HEAD'],
                   cwd=measured_repo, check=True, capture_output=True)
    paused_measurement('execute')
    second = run_cli(other, 'coverage', '--json')
    assert second.returncode == 0, second.stdout + second.stderr
    assert json.loads(second.stdout)['crap_load'] == 1


def test_retest_owns_the_same_junit_that_measurement_reads(measured_repo, paused_measurement):
    first = paused_measurement('retest')
    second = run_cli(measured_repo, 'coverage', '--reuse-artifacts', '--json')
    assert second.returncode == 5, second.stdout + second.stderr
    assert 'measurement already in use' in second.stderr
    (measured_repo / '.crapkit/release').touch()
    out, err = first.communicate(timeout=30)
    assert first.returncode == 0, out + err
    assert json.loads(out) == ['tests.test_app::test_f']


def coverage_after_owner_exit(root):
    until = time.monotonic() + 15
    while True:
        result = run_cli(root, 'coverage', '--json')
        if result.returncode != 5:
            return result
        expected = 'measurement already in use; wait for its owner to finish'
        assert json.loads(result.stdout)['error']['message'] == expected, result.stdout + result.stderr
        assert time.monotonic() < until, result.stdout + result.stderr
        time.sleep(.02)


def test_a_killed_cli_stops_its_suite_before_releasing_ownership(measured_repo, paused_measurement):
    first = paused_measurement('execute')
    (measured_repo/'.crapkit/check-writer').touch()
    first.kill()
    first.wait(timeout=10)
    second = coverage_after_owner_exit(measured_repo)
    assert second.returncode == 0, second.stdout + second.stderr
    assert json.loads(second.stdout)['lane_failures'] == {}
    assert (measured_repo/'.crapkit/writer-released').exists(), 'old suite still owns its writer lock'
    assert not (measured_repo/'.crapkit/late-write').exists(), 'the suite outlived its measurement owner'


def test_a_clock_deadline_cannot_release_the_fixture(tmp_path):
    class WaitingForRelease(Exception):
        pass

    ticks = iter((0, 3600))
    sleep = Mock(side_effect=WaitingForRelease)
    namespace = {'time': SimpleNamespace(monotonic=lambda: next(ticks), sleep=sleep)}
    exec(PAUSE, namespace)
    with pytest.raises(WaitingForRelease):
        namespace['pause_measurement'](tmp_path)
    assert (tmp_path/'ready').exists()
    (tmp_path/'release').touch()
    namespace['pause_measurement'](tmp_path)
    sleep.assert_called_once_with(.02)
