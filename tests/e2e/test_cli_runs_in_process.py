"""`run_cli` answers in the worker unless a file asks for a child process.

A spawn cost the interpreter start, crapkit's imports and coverage's startup
hook on every one of the suite's CLI calls. In process, the call has to come
back as the spawned child would have reported it: the same exit code, the same
stdout and stderr, output from commands crapkit starts included. And it must
leave the worker as it found it, because the next test in that worker is not
expecting a changed cwd, environment or cache.
"""
import gc
import logging
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from conftest import git_commit_all, git_init_repo, run_cli

SCOPED = """[crapkit]
target = 6

[crapkit.scoped_tests]
core = "python -c \\"import sys; print('runner out'); print('runner err', file=sys.stderr)\\""

[[scope]]
name = "core"
paths = ["core"]
languages = ["python"]
"""


@pytest.fixture()
def spawns(monkeypatch) -> list[list[str]]:
    """Every argv this test's process starts, recorded on the way through."""
    started = []
    real = subprocess.Popen.__init__

    def recording(self, args, *rest, **kwargs):
        started.append([str(a) for a in args] if isinstance(args, (list, tuple)) else [args])
        real(self, args, *rest, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", recording)
    return started


def _crapkit_children(started: list[list[str]]) -> list[list[str]]:
    return [argv for argv in started if argv[1:3] == ["-m", "crapkit"]]


@pytest.fixture()
def scoped_repo(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(SCOPED, encoding="utf-8")
    git_commit_all(git_init_repo(tmp_path), "init")
    return tmp_path


def _same(a: subprocess.CompletedProcess, b: subprocess.CompletedProcess) -> None:
    assert (a.returncode, a.stdout, a.stderr) == (b.returncode, b.stdout, b.stderr)


def test_a_call_runs_in_the_worker_and_spawns_no_crapkit(tmp_path, spawns):
    done = run_cli(tmp_path, "--version")

    assert done.returncode == 0 and done.stdout.startswith("crapkit ")
    assert _crapkit_children(spawns) == []


def test_spawn_true_still_starts_python_dash_m_crapkit(tmp_path, spawns):
    done = run_cli(tmp_path, "--version", spawn=True)

    assert done.returncode == 0
    assert len(_crapkit_children(spawns)) == 1


@pytest.mark.parametrize("args", [
    ("--version",),
    ("no-such-command",),
    ("worklist", "--json"),
    ("brief", "--batch", "0", "--json"),
])
def test_in_process_answers_what_the_child_answers(tmp_path, args):
    """Exit code, stdout and stderr, byte for byte: argparse's usage exit, a
    refusal with its --json error object, and a plain success."""
    git_init_repo(tmp_path)

    _same(run_cli(tmp_path, *args, encoding="utf-8"),
          run_cli(tmp_path, *args, encoding="utf-8", spawn=True))


def test_a_non_ascii_message_decodes_as_the_childs_would(tmp_path):
    """crapkit writes UTF-8 to a pipe; a caller that names no encoding decodes
    with the locale's, and both paths hand back that same text."""
    (tmp_path / "crapkit.toml").write_text("[crapkit]\ntarget = 'é'\n", encoding="utf-8")

    _same(run_cli(tmp_path, "worklist", errors="replace"),
          run_cli(tmp_path, "worklist", errors="replace", spawn=True))


def test_output_from_a_command_crapkit_starts_is_captured(scoped_repo):
    """test-scoped runs its template with inherited stdio, so the template's
    output only reaches the result if the worker's own descriptors were caught."""
    done = run_cli(scoped_repo, "test-scoped", "core/mod.py", encoding="utf-8")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "runner out" in done.stdout and "runner err" in done.stderr
    _same(done, run_cli(scoped_repo, "test-scoped", "core/mod.py", encoding="utf-8", spawn=True))


def _cache_sizes(module_name: str, module) -> dict[str, int]:
    return {f"{module_name}.{name}": value.cache_info().currsize
            for name, value in vars(module).items() if hasattr(value, "cache_info")}


def _crapkit_cache_sizes() -> dict[str, int]:
    sizes = {}
    for name, module in list(sys.modules.items()):
        if name.split(".")[0] == "crapkit":
            sizes.update(_cache_sizes(name, module))
    return sizes


def _worker_state() -> dict:
    loggers = [logging.getLogger(), *[logging.getLogger(name) for name in
                                      logging.Logger.manager.loggerDict]]
    return {"cwd": os.getcwd(), "env": dict(os.environ), "argv": list(sys.argv),
            "streams": (sys.stdin, sys.stdout, sys.stderr),
            "handlers": {logger.name: list(logger.handlers) for logger in loggers},
            "threads": threading.active_count(),
            "fds": tuple(os.fstat(fd).st_ino for fd in (1, 2))}


def test_a_call_leaves_the_worker_as_it_found_it(scoped_repo):
    """env_extra sets a key, doctor loads the admin commands, test-scoped
    starts a child. None of it may outlive the call."""
    run_cli(scoped_repo, "--version")
    before = _worker_state()

    doctor = run_cli(scoped_repo, "doctor", env_extra={"CRAPKIT_PROBE_MARK": "1"})
    scoped = run_cli(scoped_repo, "test-scoped", "core/mod.py")

    assert "runner out" in scoped.stdout and doctor.stdout
    assert _worker_state() == before
    assert "CRAPKIT_PROBE_MARK" not in os.environ


def test_crapkits_caches_hold_nothing_after_a_call(scoped_repo):
    """A start probe cached under one PATH would answer the next test under
    another. A new process starts with every cache empty, and so does the next
    in-process call."""
    from crapkit.cli import admin

    admin._start_probe("python")
    assert admin._start_probe.cache_info().currsize == 1

    run_cli(scoped_repo, "doctor")

    sizes = _crapkit_cache_sizes()
    assert "crapkit.cli.admin._start_probe" in sizes and set(sizes.values()) == {0}


def test_an_uncaught_exception_is_exit_1_with_its_traceback(tmp_path, monkeypatch):
    import crapkit.cli

    def broken(argv):
        raise RuntimeError("the command broke")

    monkeypatch.setattr(crapkit.cli, "main", broken)
    here = os.getcwd()

    done = run_cli(tmp_path, "worklist", env_extra={"CRAPKIT_PROBE_MARK": "1"})

    assert done.returncode == 1 and done.stdout == ""
    assert "Traceback" in done.stderr and "RuntimeError: the command broke" in done.stderr
    assert os.getcwd() == here and "CRAPKIT_PROBE_MARK" not in os.environ


def test_stdin_reaches_the_command_as_a_child_would_read_it(tmp_path, monkeypatch):
    import crapkit.cli

    def echo(argv):
        print(repr(sys.stdin.read()))
        return 4

    monkeypatch.setattr(crapkit.cli, "main", echo)

    done = run_cli(tmp_path, "claude-hook", stdin="{\"a\": \"é\"}\nsecond\n", encoding="utf-8")

    assert (done.returncode, done.stdout) == (4, repr("{\"a\": \"é\"}\nsecond\n") + "\n")


def test_a_call_past_its_bound_ends_the_worker_instead_of_hanging_it(tmp_path):
    """No thread can stop a call running in the worker's main thread, so the
    bound ends the process: xdist then names the test the worker died in."""
    import cli_in_process

    script = tmp_path / "hang.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path[:0] = [{str(Path(cli_in_process.__file__).parent)!r}]\n"
        "import crapkit.cli, cli_in_process\n"
        "crapkit.cli.main = lambda argv: time.sleep(60)\n"
        "cli_in_process.run(sys.argv[1], ['worklist'], env={**__import__('os').environ},"
        " timeout=1)\n", encoding="utf-8")

    done = subprocess.run([sys.executable, str(script), str(tmp_path)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)

    assert done.returncode == 1, done.stdout + done.stderr
    assert "Timeout" in done.stderr and "time.sleep" not in done.stdout


def test_a_second_call_while_one_runs_refuses_instead_of_sharing_the_process(
        tmp_path, monkeypatch):
    import crapkit.cli

    entered, release = threading.Event(), threading.Event()

    def held(argv):
        entered.set()
        release.wait(60)
        return 0

    monkeypatch.setattr(crapkit.cli, "main", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(run_cli, tmp_path, "worklist")
        assert entered.wait(60)
        try:
            with pytest.raises(RuntimeError, match="cannot overlap"):
                run_cli(tmp_path, "worklist")
        finally:
            release.set()
        assert first.result(timeout=60).returncode == 0


def _live_connections() -> int:
    return sum(isinstance(item, sqlite3.Connection) for item in gc.get_objects())


@pytest.fixture()
def no_automatic_gc():
    """The automatic collector frees a cycle whenever allocation counts say so,
    which makes a leak pass or fail by timing. Off, only an explicit collection
    frees one."""
    gc.disable()
    try:
        yield
    finally:
        gc.enable()


def test_the_store_is_closed_when_the_call_returns(scoped_repo, no_automatic_gc):
    """Exit closes a process's store. In the worker the connection sits in a
    reference cycle, since sqlite3's statement cache wraps the connection, and
    Windows refuses to rename a directory with a file open inside it."""
    before = _live_connections()

    assert run_cli(scoped_repo, "inventory").returncode == 0

    assert _live_connections() == before
    (scoped_repo / ".crapkit").rename(scoped_repo / "moved")


class _Cycle:
    def __init__(self):
        self.me = self


def test_the_calls_collection_scans_only_what_the_call_made(scoped_repo, no_automatic_gc):
    """A worker holds about 100,000 objects, and scanning them all cost most of
    an in-process call. Garbage the worker made before the call is the worker's
    collector's business, so the call leaves it where it is."""
    import weakref

    older = _Cycle()
    alive = weakref.ref(older)
    del older

    assert run_cli(scoped_repo, "--version").returncode == 0

    assert alive() is not None, "the call's collection reached the worker's own garbage"
    gc.collect()
    assert alive() is None
