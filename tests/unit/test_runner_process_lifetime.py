"""A completed test suite cannot leave writers behind for the next suite."""
import subprocess
import sys
import time

from test_suite_schedule import SCRIPT, fixture_env, fixture_repo


def test_runner_stops_background_writer_before_starting_next_suite(tmp_path):
    fixture_repo(tmp_path, "")
    writer = tmp_path / "writer.py"
    writer.write_text(
        "from pathlib import Path\nimport os, time\n"
        "stream = open('writer.lock', 'a+b')\nstream.write(b'0'); stream.flush(); stream.seek(0)\n"
        "if os.name == 'nt':\n"
        "    import msvcrt\n    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)\n"
        "else:\n    import fcntl\n    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "Path('writer-ready').touch()\n"
        "deadline = time.monotonic() + 60\n"
        "while not Path('writer-stop').exists() and time.monotonic() < deadline:\n"
        "    time.sleep(.02)\n", encoding="utf-8")
    (tmp_path / "tests/unit/test_one.py").write_text(
        "from pathlib import Path\nimport subprocess, sys, time\n"
        "def test_launch_writer():\n"
        "    subprocess.Popen([sys.executable, 'writer.py'], stdin=subprocess.DEVNULL, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "    deadline = time.monotonic() + 10\n"
        "    while not Path('writer-ready').exists() and time.monotonic() < deadline:\n"
        "        time.sleep(.02)\n"
        "    assert Path('writer-ready').exists()\n", encoding="utf-8")
    (tmp_path / "tests/e2e/test_two.py").write_text(
        "import os\n"
        "def test_previous_writer_has_stopped():\n"
        "    with open('writer.lock', 'r+b') as stream:\n"
        "        if os.name == 'nt':\n"
        "            import msvcrt\n"
        "            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)\n"
        "        else:\n"
        "            import fcntl\n"
        "            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)\n", encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(tmp_path), "--workers", "1",
             "--unit-workers", "1"], env=fixture_env(tmp_path), capture_output=True,
            text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        runs = list((tmp_path / ".crapkit/test-runs").glob("run-*/.crapkit-test-run.json"))
        assert len(runs) == 1
        assert '"finished_at"' in runs[0].read_text(encoding="utf-8")
    finally:
        (tmp_path / "writer-stop").touch()
        # The pre-fix reproduction leaves only this private, bounded writer.
        time.sleep(.1)
