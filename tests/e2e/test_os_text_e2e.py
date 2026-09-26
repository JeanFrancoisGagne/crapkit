"""Text the OS hands crapkit (argv, the environment, a host name, the checkout's
directory) and text crapkit hands back (the console, a pasted command), fed
through the real CLI in the bytes each can hold.

Python decodes a POSIX byte that is not UTF-8 as a lone surrogate (U+DC80 to
U+DCFF), and a Windows command line or environment can carry a lone surrogate
too. sqlite and `str.encode("utf-8")` refuse one, so `explain` and `brief` on
such a path argument, an override reason in such bytes, and a host or checkout
directory named in Latin-1 ended in a UnicodeEncodeError, and `verify --base`
on such a ref ended in a UnicodeDecodeError. crapkit now reads argv and the
override reason through repotext.os_text, keys host and directory names on
repotext.os_bytes, and reads git's echo of a ref leniently.

A str holding U+DCE9 stands for b"\\xe9" throughout: subprocess hands it to a
POSIX child as that byte, and to a Windows child as the lone surrogate itself.
One parametrized test per site, the red rows and their green controls; the rows
no input reaches are listed at the bottom with the reason.
"""
from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import hang_guard
import pytest

from conftest import CRAPKIT, child_env, cli_runner
from foreign_bytes import (APP, LINUX, SCAFFOLD, TOML, WINDOWS, answered, clean, commit, git, repository,
                           scored_repo, shown)

run_cli = cli_runner(encoding="utf-8", errors="replace")
spawned = cli_runner(encoding="utf-8", errors="replace", spawn=True)

LATIN1 = "caf\udce9"  # b"caf\xe9" as Python holds it
WINDOWS_SURROGATE = "caf\ud800"  # broken UTF-16 a Windows command line can carry
ONLY_WINDOWS = pytest.mark.skipif(not WINDOWS, reason="only a Windows command line or environment carries "
                                                      "a lone surrogate outside U+DC80..U+DCFF")
ONLY_LINUX = pytest.mark.skipif(not LINUX, reason="a name whose bytes are not UTF-8 exists only on a "
                                                  "POSIX filesystem, command line or environment")
ASCII_LOCALE = {"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}


def _measured(root: Path) -> Path:
    repo = scored_repo(root)
    commit(repo, {"src/café.py".encode(): APP})
    answered(run_cli(repo, "coverage"))
    return repo


@pytest.fixture(scope="module")
def measured(tmp_path_factory) -> Path:
    return _measured(tmp_path_factory.mktemp("argv") / "repo")


# --- a path argument: cli/_shared._repo_relative under explain and brief ------
#
# utf8-author-shape-22 and -boundary-18. The argument names no file crapkit
# reads, so each command answers with its own sentence for a missing file.
# rescore, ratchet move and a path that exists are the controls.

def _argv_row(row_id, args, code, said, *marks):
    return pytest.param(args, code, said, {}, id=row_id, marks=marks)


ARGV_ROWS = [
    _argv_row("argv-invalid-utf8-explain", ["explain", f"src/{LATIN1}.py", "pick"], 1, "crapkit: no function"),
    _argv_row("argv-invalid-utf8-brief", ["brief", f"src/{LATIN1}.py", "pick"], 1, "crapkit: no function"),
    _argv_row("argv-lone-surrogate-explain", ["explain", f"src/{WINDOWS_SURROGATE}.py", "pick"], 1,
              "crapkit: no function", ONLY_WINDOWS),
    _argv_row("argv-lone-surrogate-brief", ["brief", f"src/{WINDOWS_SURROGATE}.py", "pick"], 1,
              "crapkit: no function", ONLY_WINDOWS),
    _argv_row("argv-invalid-utf8-rescore-control", ["rescore", f"src/{LATIN1}.py"], 3, "does not exist"),
    _argv_row("argv-invalid-utf8-rescore-gate-control", ["rescore", "--gate", f"src/{LATIN1}.py"], 3,
              "does not exist"),
    _argv_row("argv-invalid-utf8-ratchet-move-control", ["ratchet", "move", f"src/{LATIN1}.py", "src/new.py"], 3,
              "no mark under"),
    _argv_row("argv-valid-accent-explain-control", ["explain", "src/café.py", "pick"], 0, "pick"),
    _argv_row("argv-valid-accent-brief-control", ["brief", "src/café.py", "pick"], 0, "pick"),
]


@pytest.mark.parametrize("args, code, said, env", ARGV_ROWS)
def test_a_path_argument_in_any_bytes_gets_the_commands_own_answer(measured, args, code, said, env):
    res = spawned(measured, *args, env_extra=env)

    answered(res, code)
    assert said in res.stdout + res.stderr, shown(res)


@ONLY_LINUX
@pytest.mark.parametrize("command", ["explain", "brief"])
def test_a_utf8_argument_under_an_ascii_locale_names_the_file(measured, command):
    """utf8-author-shape-22: with C-locale coercion and UTF-8 mode off, Python
    holds each byte of `café` as a surrogate; the bytes it stands for are UTF-8,
    so the argument names src/café.py and the command finds the function."""
    res = spawned(measured, command, "src/café.py", "pick", env_extra=ASCII_LOCALE)

    answered(res)
    assert "pick" in res.stdout


# --- a ref argument: gitio.merge_base under verify --base ----------------------
#
# utf8-author-shape-24. git echoes a ref it cannot resolve on stderr, in the
# bytes it was given; the answer is the exit-4 sentence, not a traceback.

@pytest.mark.parametrize("ref", [LATIN1, pytest.param(WINDOWS_SURROGATE, marks=ONLY_WINDOWS), "café", "nosuchref"],
                         ids=["ref-invalid-utf8", "ref-lone-surrogate", "ref-valid-accent-control",
                              "ref-ascii-control"])
def test_verify_base_on_a_ref_no_commit_holds_says_so_in_a_sentence(measured, ref):
    res = spawned(measured, "verify", "--base", ref)

    answered(res, 4)
    assert "git merge-base" in res.stderr, shown(res)


@ONLY_LINUX
def test_a_branch_named_in_latin1_is_a_base_like_any_other(tmp_path):
    """HEAD on a branch whose name is not UTF-8, then verify --base naming it
    (utf8-author-shape-24 head-on-latin1-branch, green on both trees)."""
    repo = scored_repo(tmp_path / "repo")
    git(repo, "checkout", "-q", "-b", LATIN1)
    answered(run_cli(repo, "coverage"))

    answered(run_cli(repo, "worklist"))
    answered(spawned(repo, "verify", "--base", LATIN1))


# --- an override reason: override.record_override ------------------------------
#
# utf8-author-shape-25 and -boundary-17. The reason reaches the alert first and
# the store after; a reason in bytes that are not UTF-8 sent the alert, then
# failed to store the audit. Every row must grant, alert and record, from
# CRAPKIT_OVERRIDE_REASON under hook-precommit and from argv under verify.

BREACH = b"def sprawl(n):\n" + b"".join(b"    if n == %d:\n        n += %d\n" % (i, i) for i in range(1, 8)) + b"    return n\n"
ALERT = b"import sys\nopen('alert.bin', 'ab').write(sys.stdin.buffer.read())\n"
# What the alert holds for a broken character. crapkit reads a lone surrogate
# as the bytes it stands for: on POSIX the one byte, one U+FFFD; on Windows
# three bytes of broken UTF-8, three U+FFFD. The environment carries the
# surrogate itself. A command line may not: the python.exe of a uv venv on
# Windows is a launcher that re-reads its command line and hands the
# interpreter one U+FFFD before crapkit runs, while a plain interpreter or a
# CPython venv hands over the surrogate. _argv_read_as asks the interpreter.
READ_AS = "\ufffd" * (3 if WINDOWS else 1)
BROKEN = "{}"  # where a row's broken character lands in the alert

REASON_ROWS = [
    pytest.param(f"urgent {LATIN1} fix", f"urgent caf{BROKEN} fix", id="reason-invalid-utf8-bytes"),
    pytest.param(f"urgent {WINDOWS_SURROGATE} fix", f"urgent caf{BROKEN} fix", marks=ONLY_WINDOWS,
                 id="reason-lone-surrogate"),
    pytest.param("hotfix 123", "hotfix 123", id="reason-ascii"),
    pytest.param("correctif café", "correctif café", id="reason-valid-accent"),
    pytest.param("修正 \U0001f680", "修正 \U0001f680", id="reason-cjk-emoji"),
]


@functools.cache
def _argv_read_as() -> str:
    """What one broken character of a command-line argument becomes by the
    time crapkit reads it, asked of the interpreter the CLI rows start."""
    probe = subprocess.run([CRAPKIT[0], "-c", "import sys; print(ascii(sys.argv[1]))", LATIN1],
                           capture_output=True, text=True, timeout=hang_guard.HANG_SECONDS, check=True)
    return "\ufffd" if probe.stdout.strip() == ascii("caf\ufffd") else READ_AS


def _override_repo(root: Path) -> Path:
    toml = TOML.replace(b"[crapkit]\n", b'[crapkit]\nalert_command = "python alert.py"\n', 1)
    repo = scored_repo(root)
    commit(repo, {b"crapkit.toml": toml, b"alert.py": ALERT})
    answered(run_cli(repo, "coverage"))
    return repo


def _alerted(repo: Path, kept: str) -> None:
    assert f"OVERRIDE ({kept})".encode() in (repo / "alert.bin").read_bytes()


@pytest.mark.parametrize("reason, kept", REASON_ROWS)
def test_a_hook_override_reason_in_any_bytes_alerts_and_records(tmp_path, reason, kept):
    repo = _override_repo(tmp_path / "repo")
    (repo / "src" / "breach.py").write_bytes(BREACH)
    git(repo, "add", "-A")

    res = spawned(repo, "hook-precommit", env_extra={"CRAPKIT_OVERRIDE_REASON": reason})

    answered(res)
    assert "override granted with full audit" in res.stdout, shown(res)
    _alerted(repo, kept.format(READ_AS))


@pytest.mark.parametrize("reason, kept", REASON_ROWS)
def test_a_verify_override_reason_in_any_bytes_alerts_and_records(tmp_path, reason, kept):
    repo = _override_repo(tmp_path / "repo")
    commit(repo, {b"src/breach.py": BREACH}, message=b"breach")

    res = spawned(repo, "verify", "--override", reason, env_extra={"CRAPKIT_OVERRIDE_REASON": None})

    answered(res)
    _alerted(repo, kept.format(_argv_read_as()))


# --- the host name: resources._budget_directory and lanes._output_lock ----------
#
# utf8-author-shape-30 and -shape-31. Both hash the host name, doctor to size
# the worker budget and every lane run to take its output lock. A real host
# rename needs a user namespace on Linux and admin rights plus a reboot on
# Windows, so the crapkit process here gets the name through
# socket.gethostname, in the str Python's own decode of the host's bytes gives.

HOST_SHIM = ("import socket, sys\n"
             "host = sys.argv.pop(1)\n"
             "socket.gethostname = lambda: host\n"
             "from crapkit.cli import main\n"
             "sys.exit(main(sys.argv[1:]))\n")


def _under_host(repo: Path, host: str, *args: str) -> subprocess.CompletedProcess:
    res = hang_guard.run([sys.executable, "-c", HOST_SHIM, host, *args], cwd=repo, env=child_env())
    return subprocess.CompletedProcess(res.args, res.returncode, res.stdout.decode("utf-8", "replace"),
                                       res.stderr.decode("utf-8", "replace"))


@pytest.mark.parametrize("host", [LATIN1, pytest.param(WINDOWS_SURROGATE, marks=ONLY_WINDOWS), "café", "build-01"],
                         ids=["hostname-invalid-utf8", "hostname-lone-surrogate", "hostname-valid-accent",
                              "hostname-ascii"])
def test_a_host_name_in_any_bytes_sizes_the_budget_and_locks_the_lane_output(tmp_path, host):
    repo = scored_repo(tmp_path / "repo")
    control = run_cli(repo, "doctor")

    answered(_under_host(repo, host, "coverage"))
    doctor = _under_host(repo, host, "doctor")
    answered(doctor, control.returncode)
    assert "resources:" in doctor.stdout, shown(doctor)


# --- the checkout's own directory: gitio.worktree_root, lanes._output_lock -----
#
# utf8-author-shape-9, -shape-31 and -boundary-26. The absolute paths git and
# the lanes hand back hold the directory's name. Every command answers under
# an accented, a CJK and emoji, and (Linux) a Latin-1 parent as it does under
# an ASCII one. A lane that runs coverage.py itself is another matter:
# coverage.py's own combine step fails under a parent that is not UTF-8, and
# crapkit reports that as the lane's failure (docs/configuration.md).

MUT_TOML = b'mutation_command = "python t.py"\nmutation_workers = 2\n'
MUT_TEST = b"import sys\nsys.path.insert(0, 'src')\nimport app\nassert app.pick('a') == 1\n"


def _commands_under(parent: Path) -> dict:
    repo = repository(parent / "repo")
    commit(repo, {**SCAFFOLD, b"crapkit.toml": TOML.replace(b"[crapkit]\n", b"[crapkit]\n" + MUT_TOML, 1),
                  b"t.py": MUT_TEST, b"src/app.py": APP})
    absolute = str(repo / "src" / "app.py")  # the form tab completion hands over
    codes = {}
    for args in (["init"], ["coverage"], ["inventory"], ["worklist"], ["brief", "src/app.py", "pick"],
                 ["doctor"], ["verify"], ["mutate", "--files", "src/app.py"], ["explain", absolute, "pick"],
                 ["brief", absolute, "pick"], ["rescore", "--gate", absolute]):
        res = run_cli(repo, *args)
        assert clean(res), shown(res)
        codes[" ".join(args).replace(absolute, "ABSOLUTE")] = (res.returncode, "outside the repo" in res.stderr)
    return codes


@pytest.mark.parametrize("parent", [pytest.param(LATIN1, marks=ONLY_LINUX), "José François", "李雷 \U0001f600"],
                         ids=["dir-invalid-utf8", "dir-valid-accent", "dir-cjk-emoji"])
def test_every_command_answers_the_same_under_any_directory_name(tmp_path, parent):
    """An absolute path argument under a Latin-1 parent read as outside the
    repo at exit 3 (utf8-author U20), where the ASCII parent's answer is 0."""
    assert _commands_under(tmp_path / parent) == _commands_under(tmp_path / "ascii")


PYTEST_COV_TOML = (b'[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
                   b'[[lane]]\nname = "unit"\n'
                   b'command = "python -m pytest -q -p no:cacheprovider --cov=src --cov-branch '
                   b'--cov-report=json:cov.json t_app.py"\n'
                   b'artifact = "cov.json"\nparser = "coveragepy"\nscopes = ["src"]\nfull_suite = false\n')
PYTEST_COV_TEST = b"import sys\nsys.path.insert(0, 'src')\nimport app\n\n\ndef test_pick():\n    assert app.pick('a') == 1\n"
NEEDS_PYTEST_COV = pytest.mark.skipif(not LINUX or not shutil.which("python"),
                                      reason="a Latin-1 directory exists only on a POSIX file system, and the "
                                             "lane runs `python -m pytest --cov` from PATH")


@NEEDS_PYTEST_COV
@pytest.mark.parametrize("parent", [LATIN1, "café"], ids=["dir-invalid-utf8", "dir-valid-accent"])
def test_a_pytest_cov_lane_under_a_latin1_directory_names_the_rename(tmp_path, parent):
    """coverage.py stores every measured path as UTF-8, so under a Latin-1
    parent its combine fails and leaves a shard. crapkit called that shard what
    a killed parallel run leaves and handed over a `coverage combine` that fails
    the same way (utf8-author shape-31); it now names the directory."""
    pytest.importorskip("pytest_cov")
    repo = repository(tmp_path / parent / "repo")
    commit(repo, {b"crapkit.toml": PYTEST_COV_TOML, b".gitignore": b".crapkit/\ncov.json\n.coverage*\n",
                  b"t_app.py": PYTEST_COV_TEST, b"src/app.py": APP})

    res = run_cli(repo, "coverage")

    if parent == LATIN1:
        assert res.returncode == 5, shown(res)
        assert "caf\\xe9/repo holds bytes that are not UTF-8" in res.stderr, shown(res)
        assert "Rename it to UTF-8 and run the lane again" in res.stderr, shown(res)
        assert "killed parallel run" not in res.stderr, shown(res)
    else:
        answered(res)


# --- crapkit's own output: cli/parser._reconfigure_streams ---------------------
#
# utf8-author-shape-21, -boundary-21 and -history-14, all green on both trees.
# A pipe gets UTF-8 whatever PYTHONIOENCODING or PYTHONUTF8 says; a console
# keeps its own code page and prints what it cannot hold as `?`.

WIDE = "src/渡辺.py"
WIDE_SOURCE = ("def f(x):\n" + "".join(f"    if x == {i}:\n        return '\U0001f680 café 渡辺 {i}'\n"
                                     for i in range(7)) + "    return -1\n").encode()
COMMANDS = {"brief": ("brief", WIDE, "f"), "worklist": ("worklist",), "explain-missing": ("explain", WIDE, "nope")}
PIPES = {"pipe-default": {}, "pipe-pythonioencoding-cp1252": {"PYTHONIOENCODING": "cp1252"},
         "pipe-pythonioencoding-ascii": {"PYTHONIOENCODING": "ascii"}, "pipe-pythonutf8-1": {"PYTHONUTF8": "1"},
         "pipe-pythonutf8-0": {"PYTHONUTF8": "0"}}


@pytest.fixture(scope="module")
def wide(tmp_path_factory) -> Path:
    repo = scored_repo(tmp_path_factory.mktemp("wide") / "repo")
    commit(repo, {WIDE.encode(): WIDE_SOURCE})
    answered(run_cli(repo, "coverage"))
    return repo


def _raw(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return hang_guard.run([*CRAPKIT, *args], cwd=repo, env=child_env(env))


@pytest.mark.parametrize("command", list(COMMANDS))
@pytest.mark.parametrize("pipe", list(PIPES))
def test_a_pipe_gets_repo_text_as_utf8_whatever_the_environment_says(wide, pipe, command):
    control = _raw(wide, *COMMANDS[command])
    res = _raw(wide, *COMMANDS[command], env=PIPES[pipe])

    text = (res.stderr if command == "explain-missing" else res.stdout).decode("utf-8")
    assert res.returncode == control.returncode and b"Traceback" not in res.stderr, text
    assert "渡辺" in text or "\U0001f680" in text, text


# The object _reconfigure_streams meets on a Windows console: a stream over the
# real descriptor that answers isatty() True, in a legacy code page.
TTY_HARNESS = r'''
import io, sys
class Console(io.TextIOWrapper):
    def isatty(self):
        return True
code_page = sys.argv[1]
sys.stdout = Console(open(1, "wb", closefd=False), encoding=code_page, errors="strict", line_buffering=True)
sys.stderr = Console(open(2, "wb", closefd=False), encoding=code_page, errors="strict", line_buffering=True)
from crapkit.cli import main
code = main(sys.argv[2:])
sys.stdout.flush()
sys.exit(code)
'''


@pytest.mark.parametrize("code_page, args", [
    ("cp1252", ("explain", WIDE, "f")), ("ascii", ("explain", WIDE, "f")),
    ("cp1252", ("report",)), ("cp1252", ("explain", WIDE, "f", "--json")), ("cp1252", ("worklist",)),
], ids=["cp1252-console-explain", "ascii-console-explain", "cp1252-console-report", "cp1252-console-json",
        "cp1252-console-worklist"])
def test_a_legacy_code_page_console_prints_repo_text_without_a_traceback(wide, code_page, args):
    res = hang_guard.run([sys.executable, "-c", TTY_HARNESS, code_page, *args], cwd=wide, env=child_env())

    assert res.returncode == 0 and b"Traceback" not in res.stderr, res.stderr.decode("utf-8", "replace")


@pytest.mark.skipif(not LINUX or not shutil.which("script"), reason="a real pty with an ASCII locale needs "
                    "util-linux `script`; a Windows console writes through WriteConsoleW and encodes nothing")
@pytest.mark.parametrize("args", [("worklist",), ("explain", "src/app.py", "pick")], ids=["worklist", "explain"])
def test_a_real_terminal_with_an_ascii_locale_prints_without_a_traceback(wide, args):
    inner = " ".join([sys.executable, "-m", "crapkit", *args])
    res = hang_guard.run(["script", "-qec", inner, "/dev/null"], cwd=wide, env=child_env(ASCII_LOCALE))

    out = res.stdout.decode("ascii", "replace")
    assert res.returncode == 0 and "Traceback" not in out, out


# --- a command crapkit hands a shell: brief --json `commands.gate` -------------
#
# utf8-author-boundary-27. The gate command names a path with an accent and
# CJK; pasted into PowerShell 5.1, cmd.exe or sh, it must reach that file.

def _launcher(bin_dir: Path) -> dict:
    """A `crapkit` first on PATH that runs this interpreter's crapkit."""
    bin_dir.mkdir()
    if WINDOWS:
        (bin_dir / "crapkit.cmd").write_text(f'@"{sys.executable}" -m crapkit %*\r\n', encoding="ascii")
    else:
        script = bin_dir / "crapkit"
        script.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m crapkit "$@"\n', encoding="utf-8")
        script.chmod(0o755)
    return {"PATH": os.pathsep.join([str(bin_dir), str(Path(sys.executable).parent), os.environ["PATH"]])}


SHELLS = [
    pytest.param(["powershell", "-NoProfile", "-Command"], id="powershell-5.1",
                 marks=pytest.mark.skipif(not (WINDOWS and shutil.which("powershell")),
                                          reason="Windows PowerShell 5.1 ships with Windows only")),
    pytest.param(["cmd", "/d", "/c"], id="cmd", marks=pytest.mark.skipif(not WINDOWS, reason="cmd.exe is Windows's")),
    pytest.param(["sh", "-c"], id="sh", marks=pytest.mark.skipif(WINDOWS, reason="sh is the POSIX shell")),
]


@pytest.mark.parametrize("shell", SHELLS)
def test_a_pasted_gate_command_reaches_the_file_it_names(tmp_path, shell):
    repo = scored_repo(tmp_path / "repo")
    commit(repo, {"src/café_李.py".encode(): APP})
    answered(run_cli(repo, "coverage"))
    gate = json.loads(run_cli(repo, "brief", "src/café_李.py", "pick", "--json").stdout)["commands"]["gate"]
    assert "café_李.py" in gate, gate

    # One string, as a paste reaches the shell: cmd.exe does not read list2cmdline's \" escapes.
    line = subprocess.list2cmdline(shell) + " " + gate if WINDOWS else [*shell, gate]
    res = hang_guard.run(line, cwd=repo, env=child_env(_launcher(tmp_path / "bin")))

    out = (res.stdout + res.stderr).decode("utf-8", "replace")
    assert res.returncode in (0, 6) and "Traceback" not in out and "does not exist" not in out, out


# --- rows no input reaches ------------------------------------------------------
#
# utf8-author-shape-21 on a real Windows console: Python writes a console
# through WriteConsoleW (PEP 528), so no code page encode happens there; the
# harness above meets the same object on a pipe.
# utf8-author-shape-30 on win32 with a renamed host: GetComputerNameExW returns
# UTF-16, and a rename needs admin rights and a reboot. Not run; the
# hostname-lone-surrogate row feeds the one str a Windows name could produce.
# utf8-author-shape-31 on win32 under a directory holding a lone surrogate: git
# refuses the path at `git init` (`Invalid path`), before crapkit runs.
