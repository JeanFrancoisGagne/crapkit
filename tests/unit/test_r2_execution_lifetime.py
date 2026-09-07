"""Resource ownership ends only after the command's writers have stopped."""
import json
from pathlib import Path
import sys
import time

from crapkit.config import Lane
from crapkit.lanes import measurement_owner, run_lane
from crapkit.locks import exclusive_lock
from crapkit.procs import run_bounded


def wait_for(path):
    deadline = time.monotonic() + 15
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert path.exists(), f"process did not reach {path.name}"


def late_writer(root):
    script = root / 'late.py'
    script.write_text(
        'from pathlib import Path\nimport time\n'
        'from crapkit.locks import exclusive_lock\n'
        'with exclusive_lock(Path("writer.lock"), label="writer"):\n'
        '    Path("started").touch()\n'
        '    deadline = time.monotonic() + 30\n'
        '    while not Path("release").exists() and time.monotonic() < deadline:\n'
        '        time.sleep(.02)\n'
        '    Path("cov.json").write_text("OLD_DESCENDANT_WRITE")\n'
        'Path("finished").touch()\n', encoding='utf-8')
    artifact = json.dumps({'src/a.py': {'fnMap': {}, 'f': {}, 'branchMap': {}, 'b': {}}})
    (root / 'runner.py').write_text(
        'from pathlib import Path\nimport subprocess, sys, time\n'
        'subprocess.Popen([sys.executable, "late.py"], stdin=subprocess.DEVNULL, '
        'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n'
        'deadline = time.monotonic() + 15\n'
        'while not Path("started").exists() and time.monotonic() < deadline: time.sleep(.02)\n'
        'assert Path("started").exists()\n'
        f'Path("cov.json").write_text({artifact!r})\n', encoding='utf-8')
    return Lane('probe', f'"{sys.executable}" runner.py', 'cov.json', 'istanbul', ())


def test_a_lane_root_exit_stops_its_writer_before_another_owner_can_publish(tmp_path):
    lane = late_writer(tmp_path)
    writer_stopped = False
    try:
        outcome = run_lane(tmp_path, lane)
        assert outcome.provenance['exit_code'] == 0
        assert (tmp_path / 'started').exists()
        with measurement_owner(tmp_path, [lane]):
            with exclusive_lock(tmp_path / 'writer.lock', label='writer'):
                writer_stopped = True
                (tmp_path / 'cov.json').write_text('NEW_OWNER_WRITE')
                (tmp_path / 'release').touch()
        assert (tmp_path / 'cov.json').read_text() == 'NEW_OWNER_WRITE'
    finally:
        (tmp_path / 'release').touch()
        if not writer_stopped:
            wait_for(tmp_path / 'finished')


def test_a_standalone_command_stops_its_descendant_before_returning(tmp_path):
    lane = late_writer(tmp_path)
    stopped = False
    try:
        assert run_bounded(lane.command, None, cwd=tmp_path) == 0
        assert (tmp_path / 'started').exists()
        with exclusive_lock(tmp_path / 'writer.lock', label='writer'):
            stopped = True
    finally:
        (tmp_path / 'release').touch()
        if not stopped:
            wait_for(tmp_path / 'finished')
