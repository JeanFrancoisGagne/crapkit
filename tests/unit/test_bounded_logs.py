"""Lane logs retain recent output without unbounded files or lost progress."""
import os
import subprocess
import sys

import pytest

from crapkit.logs import command_log


def test_verbose_command_keeps_recent_output_in_bounded_files(tmp_path):
    path = tmp_path / "lane.log"
    with command_log(path, max_bytes=128) as stream:
        stream.write("command header\n")
        subprocess.run(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 2048 + b'\\nfinal failure\\n')"],
            stdout=stream, stderr=stream, check=True,
        )
        stream.write("exit 1\n")
    assert path.stat().st_size <= 128
    assert path.with_suffix(".log.1").stat().st_size <= 128
    assert path.read_bytes().endswith(b"final failure\nexit 1" + os.linesep.encode())
    assert stream.progress_bytes == 2083 + 2 * len(os.linesep)


def test_small_log_is_byte_identical_and_retry_appends(tmp_path):
    path = tmp_path / "lane.log"
    with command_log(path, max_bytes=512) as stream:
        stream.write("first\n")
    with command_log(path, max_bytes=512, append=True) as stream:
        stream.write("retry\n")
    assert path.read_bytes() == ("first" + os.linesep + "retry" + os.linesep).encode()
    assert not path.with_suffix(".log.1").exists()


def test_unlimited_log_uses_a_direct_file(tmp_path):
    path = tmp_path / "lane.log"
    with command_log(path, max_bytes=0) as stream:
        stream.write("unlimited\n")
        assert not hasattr(stream, "progress_bytes")
    assert path.read_bytes() == ("unlimited" + os.linesep).encode()


def test_new_attempt_discards_old_rotation(tmp_path):
    path = tmp_path / "lane.log"
    with command_log(path, max_bytes=8) as stream:
        stream.write("123456789abcdef")
    with command_log(path, max_bytes=8) as stream:
        stream.write("new")
    assert path.read_bytes() == b"new"
    assert not path.with_suffix(".log.1").exists()


def test_log_flushes_output_when_command_raises(tmp_path):
    path = tmp_path / "lane.log"
    with pytest.raises(RuntimeError, match="runner"):
        with command_log(path, max_bytes=128) as stream:
            stream.write("failure evidence\n")
            raise RuntimeError("runner")
    assert path.read_bytes() == ("failure evidence" + os.linesep).encode()


def test_failed_reader_start_closes_every_opened_stream(tmp_path, monkeypatch):
    from pathlib import Path
    from crapkit import logs
    opened = []
    original_path_open, original_fdopen = Path.open, os.fdopen

    def path_open(path, *args, **kwargs):
        stream = original_path_open(path, *args, **kwargs)
        opened.append(stream)
        return stream

    def fdopen(*args, **kwargs):
        stream = original_fdopen(*args, **kwargs)
        opened.append(stream)
        return stream

    def refused_start(thread):
        raise RuntimeError("no reader thread available")

    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(os, "fdopen", fdopen)
    monkeypatch.setattr(logs.threading.Thread, "start", refused_start)
    with pytest.raises(RuntimeError, match="no reader thread"):
        with command_log(tmp_path / "lane.log"):
            raise AssertionError("command must not start")
    try:
        assert opened and all(stream.closed for stream in opened)
    finally:
        for stream in opened:
            stream.close()


def test_rotating_output_keeps_the_no_progress_watch_alive(tmp_path):
    from crapkit.procs import _progress
    path = tmp_path / "progress.log"
    with command_log(path, max_bytes=64) as stream:
        size = 0
        for step in range(8):
            stream.write("x" * 256)
            stream.flush()
            size, since = _progress(stream, size, -1)
            assert size == 256 * (step + 1)
            assert since > 0
            assert _progress(stream, size, since) == (size, since)
        stream.write("completed")
    assert path.stat().st_size <= 64
    assert path.with_suffix(".log.1").stat().st_size <= 64
    assert b"completed" in path.read_bytes()
