"""A lane or mutation child writes UTF-8, the encoding crapkit reads its output in.

lanes._log_lines reads .crapkit/lane-<name>.log as UTF-8 and quotes its tail
when a lane fails. A Python child below 3.15 writes a pipe or a file in the
ANSI code page on Windows (cp1252) and in the locale's encoding on POSIX, and
an inherited PYTHONIOENCODING overrides both. So `No module named 'café'` came
back as `caf�`, and a test that printed an emoji under `pytest -s` raised
UnicodeEncodeError under crapkit alone: the lane still exited 0 and the score
moved. crapkit now sets PYTHONIOENCODING=utf-8 for lane and mutation children,
unless the lane's own env sets it.

Each row forces PYTHONUTF8=0 unless it sets PYTHONUTF8 itself, so Python
3.15's UTF-8 default (PEP 686) cannot make a row pass on its own.
"""
import os
import sys
from types import SimpleNamespace

import pytest

from crapkit.config import Lane
from crapkit.lane_command import launch_spec
from crapkit.mutate_pool import _suite_env, require_live_suite
from crapkit.procs import run_bounded
from hang_guard import HANG_SECONDS
from legacy_locale import latin1_env

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="a POSIX locale row: Windows reads no LANG or LC_ALL")

CHILD = ("import sys\n"
         "for stream in (sys.stdout, sys.stderr):\n"
         "    stream.write(stream.encoding.lower() + ' caf\\u00e9 cr\\u00e8me\\n')\n"
         "sys.stdout.write('\\U0001f680\\n')\n")

# What each row inherits from the shell that runs crapkit. The first four were
# red: the child wrote cp1252, ISO-8859-1 or ASCII, or raised on the emoji.
INHERITED = [
    pytest.param({}, id="no-env"),
    pytest.param({"PYTHONIOENCODING": "cp1252"}, id="inherited-PYTHONIOENCODING-cp1252"),
    pytest.param({"LC_ALL": "C", "PYTHONCOERCECLOCALE": "0"}, id="LC_ALL-C", marks=POSIX_ONLY),
    pytest.param("latin1", id="LANG-en_US.ISO-8859-1"),
    pytest.param({"PYTHONUTF8": "1"}, id="control-PYTHONUTF8-1"),
    pytest.param({"PYTHONIOENCODING": "utf-8"}, id="control-PYTHONIOENCODING-utf-8"),
]


@pytest.fixture
def inherited(request, tmp_path, monkeypatch):
    """Put the row's variables in this process's environment, which a lane
    child inherits, over PYTHONUTF8=0 and no PYTHONIOENCODING."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.setenv("PYTHONUTF8", "0")
    row = request.param
    pairs = latin1_env(tmp_path / "locales") if row == "latin1" else row
    for key, value in pairs.items():
        monkeypatch.setenv(key, value)
    return pairs


def _lane(env=()) -> Lane:
    return Lane(name="py", command="child", artifact="cov.json", parser="coveragepy",
                scopes=("src",), env=env)


def _run_lane_child(tmp_path, lane: Lane) -> tuple[int | None, bytes]:
    """Start CHILD the way lanes.py starts a lane, both streams into one log."""
    (tmp_path / "child.py").write_text(CHILD, encoding="utf-8")
    log = tmp_path / "lane-py.log"
    with open(log, "wb") as stream:
        code = run_bounded(f'"{sys.executable}" child.py', HANG_SECONDS, stream=stream,
                           **launch_spec(tmp_path, lane).popen_kwargs())
    return code, log.read_bytes()


@pytest.mark.parametrize("inherited", INHERITED, indirect=True)
def test_a_lane_child_writes_utf8_whatever_the_shell_hands_it(tmp_path, inherited):
    code, written = _run_lane_child(tmp_path, _lane())

    text = written.decode("utf-8")
    assert code == 0, text
    assert text.count("utf-8 café crème") == 2 and "\U0001f680" in text, text


@pytest.mark.parametrize("key, inherited", [
    pytest.param("PYTHONIOENCODING", {"PYTHONIOENCODING": "utf-8"}, id="over-an-inherited-utf-8"),
    pytest.param("PythonIOEncoding", {}, id="mis-cased-key", marks=pytest.mark.skipif(
        os.name != "nt", reason="one variable only where the env block is case-insensitive")),
], indirect=["inherited"])
def test_a_lane_that_sets_pythonioencoding_keeps_its_own(tmp_path, inherited, key):
    """The lane's env is the user's word on its child. cp1252 cannot write the
    emoji, so the child raises after its two lines, as it would in a console."""
    _code, written = _run_lane_child(tmp_path, _lane(env=((key, "cp1252"),)))

    assert written.count(b"cp1252 caf\xe9 cr\xe8me") == 2, written


@pytest.mark.parametrize("windows, added", [(True, None), (False, "utf-8")])
def test_only_windows_reads_a_mis_cased_key_as_the_lanes_own(tmp_path, monkeypatch, windows, added):
    """`PythonIOEncoding` and `PYTHONIOENCODING` are one name to Windows and two
    to POSIX, where Python reads only the upper-case one. Adding the default
    beside a Windows lane's own spelling would put two values of one variable
    in the env block. Both branches on one machine."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    spec = launch_spec(tmp_path, _lane(env=(("PythonIOEncoding", "cp1252"),)))

    assert spec.child_env(windows=windows).get("PYTHONIOENCODING") == added


def test_what_a_caller_adds_goes_over_the_default(tmp_path):
    """doctor's version probe and the flake retest add their own pairs last."""
    spec = launch_spec(tmp_path, _lane())

    assert spec.child_env({"PYTHONIOENCODING": "ascii"})["PYTHONIOENCODING"] == "ascii"


MUTATION_CHILD = "import sys\nsys.stdout.write('caf\\u00e9 \\U0001f680\\n')\n"


@pytest.mark.parametrize("inherited", INHERITED, indirect=True)
def test_the_mutation_baseline_runs_a_suite_that_prints_non_ascii(tmp_path, inherited):
    """mutate refuses a suite that fails on the unmutated tree. A suite that
    printed an emoji failed there under cp1252 and passed in a console."""
    (tmp_path / "suite.py").write_text(MUTATION_CHILD, encoding="utf-8")
    cfg = SimpleNamespace(mutation_command=f'"{sys.executable}" suite.py',
                          mutation_timeout_seconds=HANG_SECONDS)

    require_live_suite(tmp_path, cfg)


def test_the_mutation_env_keeps_its_bytecode_rule(monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")

    env = _suite_env()

    assert (env["PYTHONDONTWRITEBYTECODE"], env["PYTHONIOENCODING"]) == ("1", "utf-8")
