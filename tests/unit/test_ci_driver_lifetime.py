"""The isolated CI driver owns the revision runner through caller death."""
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from crapkit.errors import ToolError
from crapkit.locks import exclusive_lock
from test_ci_verdict import ROOT, driver


def wait_for(path):
    deadline = time.monotonic() + 20
    while not path.exists():
        assert time.monotonic() < deadline, path
        time.sleep(.02)


def runner_fixture(root, detached):
    runner = root / "tools/testing/run.py"
    runner.parent.mkdir(parents=True)
    (root / "writer.py").write_text(
        "from pathlib import Path\nimport os,time\n"
        "from crapkit.locks import exclusive_lock\n"
        "assert os.environ['CI_PRIVATE_MARKER'] == 'target environment'\n"
        "with exclusive_lock(Path('writer.lock'), label='private runner'):\n"
        "    Path('started.part').write_text(str(os.getpid()))\n"
        "    Path('started.part').replace(Path('started'))\n"
        "    deadline = time.monotonic() + 30\n"
        "    while not Path('release').exists() and time.monotonic() < deadline:\n"
        "        time.sleep(.02)\n"
        "    Path('finished').touch()\n", encoding="utf-8")
    runner.write_text(
        "from pathlib import Path\nimport os,subprocess,sys\n"
        "Path('runner-pid').write_text(str(os.getpid()))\n"
        f"raise SystemExit(subprocess.call([sys.executable, '-B', 'writer.py'], start_new_session={detached!r}))\n",
        encoding="utf-8")
    caller = root / "caller.py"
    caller.write_text(
        "from pathlib import Path\nimport importlib.util,os,sys\n"
        f"spec = importlib.util.spec_from_file_location('ci', {str(ROOT / 'tools/testing/ci.py')!r})\n"
        "ci = importlib.util.module_from_spec(spec); spec.loader.exec_module(ci)\n"
        "Path('caller-pid').write_text(str(os.getpid()))\n"
        "env = dict(os.environ, CI_PRIVATE_MARKER='target environment')\n"
        "ci._measure(Path.cwd(), Path(sys.executable), env, {'package': 'private-package'})\n",
        encoding="utf-8")
    return caller


@contextmanager
def running_driver(root, detached=False):
    script = runner_fixture(root, detached)
    with (root / "caller.log").open("w") as log:
        caller = subprocess.Popen([sys.executable, "-B", str(script)], cwd=root,
                                  stdout=log, stderr=log)
        try:
            wait_for(root / "started")
            yield caller
        finally:
            (root / "release").touch()
            if caller.poll() is None:
                os.kill(int((root / "caller-pid").read_text()), signal.SIGTERM)
            caller.wait(timeout=15)


def writer_stopped(root):
    try:
        with exclusive_lock(root / "writer.lock", label="private runner stopped"):
            return True
    except ToolError:
        return False


@pytest.mark.parametrize("detached", [False, True])
def test_killed_ci_driver_stops_its_revision_runner(tmp_path, detached):
    if detached and sys.platform != "linux":
        pytest.skip("Linux subreaper covers the historical detached command")
    with running_driver(tmp_path, detached) as caller:
        assert not writer_stopped(tmp_path)
        os.kill(int((tmp_path / "caller-pid").read_text()), signal.SIGTERM)
        caller.wait(timeout=15)
        deadline = time.monotonic() + 5
        while not writer_stopped(tmp_path) and time.monotonic() < deadline:
            time.sleep(.02)
        assert writer_stopped(tmp_path), "CI driver died but its revision runner still owns output"
        assert not (tmp_path / "finished").exists()


@pytest.mark.parametrize("refused", [False, True])
def test_comparison_stops_commands_before_retaining_and_removing_scratch(tmp_path, monkeypatch, refused):
    ci = driver()
    events, paths = [], []

    @contextmanager
    def ownership(*args):
        events.append("own")
        try:
            yield object()
        finally:
            events.append("stopped")

    def compare(repo, base, scratch, evidence):
        paths.append(scratch)
        if refused:
            raise ValueError("private comparison refused")
        return 0

    def retain(scratch, output, evidence):
        assert events == ["own", "stopped"]
        assert scratch.is_dir()
        events.append("retained")

    monkeypatch.setattr(ci, "_processes", lambda: SimpleNamespace(own_processes=ownership), raising=False)
    monkeypatch.setattr(ci, "_compare_checkouts", compare)
    monkeypatch.setattr(ci, "_retain_evidence", retain)
    if refused:
        with pytest.raises(ValueError, match="private comparison refused"):
            ci.compare(tmp_path, "HEAD", tmp_path / "evidence")
    else:
        assert ci.compare(tmp_path, "HEAD", tmp_path / "evidence") == 0
    assert events == ["own", "stopped", "retained"]
    assert paths and not paths[0].exists()


def test_ci_commands_keep_exit_status_and_separate_output(tmp_path):
    ci = driver()
    command = [sys.executable, "-c", "import subprocess,sys; "
               "subprocess.run([sys.executable, '-c', 'pass'], check=True); "
               "assert sys.stdin.read() == ''; "
               "print('standard output'); print('standard error',file=sys.stderr); sys.exit(7)"]
    with ci._commands():
        result = ci._run(command, cwd=tmp_path, capture_output=True, text=True)
        assert (result.returncode, result.stdout, result.stderr) == (7, "standard output\n", "standard error\n")
        with pytest.raises(subprocess.CalledProcessError) as failure:
            ci._run(command, cwd=tmp_path, capture_output=True, text=True, check=True)
    assert failure.value.returncode == 7
    assert failure.value.stdout == "standard output\n"
    assert failure.value.stderr == "standard error\n"


def test_ci_launch_failure_keeps_the_next_command_usable(tmp_path):
    ci = driver()
    missing = tmp_path / "missing-executable"
    with pytest.raises(FileNotFoundError) as native:
        subprocess.run([str(missing)], check=True)
    with ci._commands():
        with pytest.raises(FileNotFoundError) as failure:
            ci._run([str(missing)], capture_output=True, text=True)
        fields = ("errno", "strerror", "filename", "filename2", "winerror")
        assert [getattr(failure.value, name, None) for name in fields] == [
            getattr(native.value, name, None) for name in fields]
        result = ci._run([sys.executable, "-c", "print('next command')"], capture_output=True, text=True)
        assert (result.returncode, result.stdout, result.stderr) == (0, "next command\n", "")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper owns detached historical children")
def test_completed_ci_command_stops_detached_writer_before_returning(tmp_path):
    ci = driver()
    runner_fixture(tmp_path, True)
    runner = tmp_path / "tools/testing/run.py"
    runner.write_text(
        "from pathlib import Path\nimport subprocess,sys,time\n"
        "subprocess.Popen([sys.executable, '-B', 'writer.py'], start_new_session=True)\n"
        "until = time.monotonic() + 20\n"
        "while not Path('started').exists() and time.monotonic() < until:\n"
        "    time.sleep(.02)\n"
        "assert Path('started').exists()\nraise SystemExit(7)\n", encoding="utf-8")
    try:
        environment = dict(os.environ, CI_PRIVATE_MARKER="target environment")
        assert ci._measure(tmp_path, Path(sys.executable), environment, {"package": "private"}) == 7
        assert (tmp_path / "started").exists()
        assert writer_stopped(tmp_path)
        assert not (tmp_path / "finished").exists()
    finally:
        (tmp_path / "release").touch()
