"""The alert command reads the same bytes on every OS: UTF-8, lines ended by LF.

`[crapkit] alert_command` gets the override line and the digest body on stdin.
Both writers opened the pipe in text mode with no newline control, so on
Windows Python turned every `\\n` into `\\r\\n` on the way in: a `cat >>
alerts.log` or `curl --data-binary @-` downstream stored CR LF from a Windows
committer and LF from everyone else. Every file crapkit writes for a person or
a diff is pinned to LF; the alert is text handed to another program, and it now
follows the same rule. What the command prints back comes the other way under
the same rule: a failed alert's refusal quotes it as plain text with LF line
ends, whatever colour or OS it was printed under.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import hang_guard
from crapkit.cli.reports import _send_digest_alert
from crapkit.errors import ToolError
from crapkit.override import _alert_or_refuse
from crapkit.verify import GateViolation

# Appends its raw stdin bytes to alert.bin, as `cat >> alerts.log` would. The
# double quotes read the same in cmd.exe and sh, and nothing inside them is
# rewritten by either shell.
SINK = "import sys; open('alert.bin', 'ab').write(sys.stdin.buffer.read())"
FAILS = "import sys; sys.stderr.buffer.write(b'refus\\xc3\\xa9 par le relais'); sys.exit(3)"


def _command(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


VIOLATION = GateViolation("pkg/café.py", "grade( score )", 1, 7, 0.0, 42.0, "decompose")


def test_the_override_line_reaches_the_alert_as_utf8_with_one_lf(tmp_path):
    _alert_or_refuse(_command(SINK), tmp_path, [VIOLATION], "hotfix déjà vu")

    assert (tmp_path / "alert.bin").read_bytes() == (
        "crapkit OVERRIDE (hotfix déjà vu): pkg/café.py:1 grade( score ) crap=42.0\n").encode("utf-8")


def test_the_digest_body_reaches_the_alert_as_utf8_with_lf_line_ends(tmp_path):
    lines = ["CRAP load 25.87 -> 28.56", "new over ceiling: pkg/café.py grade( score )"]

    _send_digest_alert(tmp_path, SimpleNamespace(alert_command=_command(SINK)),
                       {"id": 1}, {"id": 3}, lines)

    assert (tmp_path / "alert.bin").read_bytes() == (
        "crapkit digest (runs 1 -> 3):\n"
        "CRAP load 25.87 -> 28.56\n"
        "new over ceiling: pkg/café.py grade( score )\n").encode("utf-8")


def test_a_failed_override_alert_quotes_what_the_command_printed(tmp_path):
    with pytest.raises(ToolError) as refused:
        _alert_or_refuse(_command(FAILS), tmp_path, [VIOLATION], "hotfix")

    assert "override alert command failed (exit 3): refusé par le relais" in str(refused.value)


# --- what the alert command printed, as the refusal quotes it -----------------
#
# The refusal quotes the command's own output on stderr and in --json's error
# object, where a person or an agent reads it as text. A Python alert script
# colours its traceback under FORCE_COLOR from 3.13 on, a runner colours its
# error line, and on Windows every line a child prints ends in CR LF.

# What the refusal says after the quote: the outcome, and the field to fix.
NEXT = " - no alert, no override; rerun once [crapkit] alert_command in crapkit.toml exits 0"
PRINTED = {
    "plain": (b"relay down\n", "relay down"),
    "coloured": (b"\x1b[1;31mrelay\x1b[0m down\n", "relay down"),
    "window-title": (b"\x1b]0;alert\x07relay down\n", "relay down"),
    "crlf-lines": (b"relay down\r\nretry at 09:00\r\n", "relay down\nretry at 09:00"),
    "progress-cr": (b"50%\rrelay down\n", "50%\nrelay down"),
    "coloured-crlf": (b"\x1b[31mrelay down\x1b[0m\r\n", "relay down"),
}


def _printing(root: Path, data: bytes, stream: str = "stderr") -> str:
    """An alert command that reads its stdin, prints `data` raw and exits 3."""
    (root / "alert.py").write_text(
        f"import sys\nsys.stdin.buffer.read()\nsys.{stream}.buffer.write({data!r})\nsys.exit(3)\n",
        encoding="utf-8")
    return f'"{sys.executable}" alert.py'


@pytest.mark.parametrize("stream", ["stderr", "stdout"])
@pytest.mark.parametrize("printed", list(PRINTED))
def test_a_failed_override_alert_is_quoted_as_plain_lf_text(tmp_path, printed, stream):
    data, quoted = PRINTED[printed]

    with pytest.raises(ToolError) as refused:
        _alert_or_refuse(_printing(tmp_path, data, stream), tmp_path, [VIOLATION], "hotfix")

    assert str(refused.value) == f"override alert command failed (exit 3): {quoted}{NEXT}"


def _printing_both(root: Path, err: bytes, out: bytes) -> str:
    """An alert command that prints `err` on stderr and `out` on stdout, then exits 3."""
    (root / "alert.py").write_text(
        "import sys\nsys.stdin.buffer.read()\n"
        f"sys.stderr.buffer.write({err!r})\nsys.stdout.buffer.write({out!r})\nsys.exit(3)\n",
        encoding="utf-8")
    return f'"{sys.executable}" alert.py'


# stderr is quoted when it holds text once the escape codes are gone. A stderr
# that held only a colour reset, a window title or blank lines says nothing, so
# the quote falls back to stdout, where the command put its message.
BOTH_STREAMS = {
    "reset-only-stderr": (b"\x1b[0m", b"relay down\n", "relay down"),
    "reset-crlf-stderr": (b"\x1b[0m\r\n", b"relay down\r\n", "relay down"),
    "window-title-stderr": (b"\x1b]0;alert\x07", b"relay down\n", "relay down"),
    "blank-stderr": (b"\r\n\n", b"\x1b[31mrelay down\x1b[0m\n", "relay down"),
    "text-on-both": (b"relay down\n", b"sent 0 of 1\n", "relay down"),
}


@pytest.mark.parametrize("printed", list(BOTH_STREAMS))
def test_a_failed_alert_quotes_the_stream_that_holds_text_once_colour_is_gone(tmp_path, printed):
    err, out, quoted = BOTH_STREAMS[printed]

    with pytest.raises(ToolError) as refused:
        _alert_or_refuse(_printing_both(tmp_path, err, out), tmp_path, [VIOLATION], "hotfix")

    assert str(refused.value) == f"override alert command failed (exit 3): {quoted}{NEXT}"


TRACEBACK = "import sys; sys.stdin.read(); 1/0"


@pytest.mark.skipif(sys.version_info < (3, 13), reason="Python colours a traceback from 3.13 on")
def test_an_alert_script_really_colours_its_traceback_under_force_color(tmp_path, monkeypatch):
    """The row below is only worth something if the child colours here."""
    monkeypatch.setenv("FORCE_COLOR", "1")

    done = subprocess.run(_command(TRACEBACK), shell=True, cwd=tmp_path, input=b"",
                          capture_output=True)

    assert b"\x1b[" in done.stderr, done.stderr


@pytest.mark.parametrize("colour", [{"FORCE_COLOR": "1"}, {"PYTHON_COLORS": "1"},
                                    {"TERM": "dumb", "FORCE_COLOR": "1"}, {}],
                         ids=["FORCE_COLOR=1", "PYTHON_COLORS=1", "TERM=dumb FORCE_COLOR=1", "no-colour"])
def test_an_alert_scripts_traceback_is_quoted_as_plain_lf_text(tmp_path, monkeypatch, colour):
    for name, value in colour.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ToolError) as refused:
        _alert_or_refuse(_command(TRACEBACK), tmp_path, [VIOLATION], "hotfix")

    message = str(refused.value)
    assert "\x1b" not in message and "\r" not in message, repr(message)
    assert message.startswith("override alert command failed (exit 1): Traceback (most recent call last):\n")
    assert message.endswith(f"ZeroDivisionError: division by zero{NEXT}"), repr(message)


def test_a_failed_digest_alert_names_the_exit_code(tmp_path):
    with pytest.raises(ToolError, match=r"digest alert command failed \(exit 3\)"):
        _send_digest_alert(tmp_path, SimpleNamespace(alert_command=_command(FAILS)),
                           {"id": 1}, {"id": 2}, ["CRAP load 1.0 -> 2.0"])


# The digest's lines are on stdout before the alert runs; the refusal says the
# alert did not go out, quotes why, and names the field, as the override's does.
DIGEST_NEXT = (" - the digest above was not alerted; "
               "rerun once [crapkit] alert_command in crapkit.toml exits 0")


@pytest.mark.parametrize("printed", list(PRINTED))
def test_a_failed_digest_alert_quotes_what_the_command_printed_as_plain_text(tmp_path, printed):
    data, quoted = PRINTED[printed]
    cfg = SimpleNamespace(alert_command=_printing(tmp_path, data))

    with pytest.raises(ToolError) as refused:
        _send_digest_alert(tmp_path, cfg, {"id": 1}, {"id": 2}, ["CRAP load 1.0 -> 2.0"])

    assert str(refused.value) == f"digest alert command failed (exit 3): {quoted}{DIGEST_NEXT}"


# --- the same bytes through the CLI, under what a shell leaves in the environment -------

ENVS = [{}, {"PYTHONIOENCODING": "cp1252"}, {"LANG": "C", "LC_ALL": "C"}, {"PYTHONUTF8": "1"}]
ENV_IDS = [" ".join(f"{k}={v}" for k, v in env.items()) or "no-env" for env in ENVS]

TOML = ('[crapkit]\ntarget = 4\nalert_command = {alert}\n\n'
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
        '[[lane]]\nname = "unit"\ncommand = "python make_cov.py"\n'
        'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')
MAKE_COV = (
    "import json, os\n"
    "app = os.path.join(os.getcwd(), 'src', 'app.ts')\n"
    "cov = {app: {'path': app,\n"
    "             'fnMap': {'0': {'name': 'tiny', 'decl': {'start': {'line': 1}},\n"
    "                             'loc': {'start': {'line': 1}, 'end': {'line': 1}}}},\n"
    "             'f': {'0': 1}, 'branchMap': {}, 'b': {}}}\n"
    "json.dump(cov, open('cov.json', 'w'))\n")
APP = "export function tiny(a: number) { return a; }\n"
TANGLED = ("export function tangled(a: number, b: number): number {\n"
           "  let r = 0;\n"
           "  if (a > 0) { if (b > 0) { r = 1; } else if (b < -5) { r = 2; } }\n"
           "  if (a > 10 && b > 10) { r += 3; }\n"
           "  if (a < -1) { r -= 1; } else if (b === 0) { r -= 2; }\n"
           "  return r;\n}\n")


def _environment(extra: dict) -> dict:
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONIOENCODING", "PYTHONUTF8", "LANG", "LC_ALL", "CRAPKIT_OVERRIDE_REASON")}
    env["PATH"] = os.pathsep.join(filter(None, (str(Path(sys.executable).parent), env.get("PATH"))))
    env.update(extra)
    return env


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def _crapkit(repo: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return hang_guard.run([sys.executable, "-m", "crapkit", *args], cwd=repo, env=_environment(env),
                          text=True, encoding="utf-8", errors="replace")


def _repo(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.ts").write_text(APP, encoding="utf-8")
    (root / "crapkit.toml").write_text(TOML.format(alert=json.dumps(_command(SINK))), encoding="utf-8")
    (root / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\ncov.json\nalert.bin\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _only_lf_utf8(data: bytes) -> str:
    assert data, "the alert command received nothing"
    assert b"\r" not in data, data
    return data.decode("utf-8")


@pytest.mark.parametrize("env", ENVS, ids=ENV_IDS)
def test_the_hook_override_hands_the_alert_lf_utf8_bytes(tmp_path, env):
    repo = _repo(tmp_path / "repo")
    (repo / "src" / "tangled.ts").write_text(TANGLED, encoding="utf-8")
    _git(repo, "add", "-A")

    result = _crapkit(repo, {**env, "CRAPKIT_OVERRIDE_REASON": "hotfix déjà vu"}, "hook-precommit")

    assert result.returncode == 0, result.stdout + result.stderr
    text = _only_lf_utf8((repo / "alert.bin").read_bytes())
    assert text.startswith("crapkit OVERRIDE (hotfix déjà vu): src/tangled.ts:1 tangled ( a , b ) crap="), text
    assert text.endswith("\n") and text.count("\n") == 1, text


COLOURS = {"FORCE_COLOR=1": {"FORCE_COLOR": "1"}, "PYTHON_COLORS=1": {"PYTHON_COLORS": "1"},
           "TERM=dumb FORCE_COLOR=1": {"TERM": "dumb", "FORCE_COLOR": "1"}, "no-colour": {}}
REFUSING = b"\x1b[31mrelay down\x1b[0m\r\nretry at 09:00\r\n"
REFUSAL = f"override alert command failed (exit 3): relay down\nretry at 09:00{NEXT}"


def _refusing_repo(root: Path) -> Path:
    """The repo above with an alert command that fails, printing REFUSING."""
    repo = _repo(root)
    (repo / "crapkit.toml").write_text(TOML.format(alert=json.dumps(_printing(repo, REFUSING))),
                                       encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "refusing alert")
    return repo


def _read(raw: bytes) -> str:
    """A crapkit stream as a reader sees it: the line ends crapkit's own text
    mode writes (CR LF on Windows) read as LF, and any other CR kept."""
    return raw.decode("utf-8").replace("\r\n", "\n")


@pytest.mark.parametrize("colour", list(COLOURS))
def test_the_hook_refuses_a_failed_alert_in_plain_lf_text(tmp_path, colour):
    repo = _refusing_repo(tmp_path / "repo")
    (repo / "src" / "tangled.ts").write_text(TANGLED, encoding="utf-8")
    _git(repo, "add", "-A")
    env = _environment({**COLOURS[colour], "CRAPKIT_OVERRIDE_REASON": "hotfix"})

    done = hang_guard.run([sys.executable, "-m", "crapkit", "hook-precommit"], cwd=repo, env=env)

    stderr = _read(done.stderr)
    assert done.returncode != 0, stderr
    assert REFUSAL in stderr, repr(stderr)
    assert "\x1b" not in stderr and "\r" not in stderr, repr(stderr)


@pytest.mark.parametrize("colour", list(COLOURS))
def test_verify_json_refuses_a_failed_alert_in_plain_lf_text(tmp_path, colour):
    repo = _refusing_repo(tmp_path / "repo")
    assert _crapkit(repo, {}, "coverage").returncode == 0
    (repo / "src" / "tangled.ts").write_text(TANGLED, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "tangled")

    done = hang_guard.run([sys.executable, "-m", "crapkit", "verify", "--override", "hotfix", "--json"],
                          cwd=repo, env=_environment(COLOURS[colour]))

    assert done.returncode != 0, _read(done.stderr)
    assert json.loads(done.stdout)["error"]["message"] == REFUSAL, _read(done.stdout)


@pytest.mark.parametrize("env", ENVS, ids=ENV_IDS)
def test_digest_alert_hands_the_alert_lf_utf8_bytes(tmp_path, env):
    repo = _repo(tmp_path / "repo")
    assert _crapkit(repo, env, "coverage").returncode == 0
    (repo / "src" / "tangled.ts").write_text(TANGLED, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "tangled")
    assert _crapkit(repo, env, "coverage").returncode == 0

    result = _crapkit(repo, env, "digest", "--alert")

    assert result.returncode == 0, result.stdout + result.stderr
    text = _only_lf_utf8((repo / "alert.bin").read_bytes())
    assert text.startswith("crapkit digest (runs 1 -> 2):\n"), text
    assert text.count("\n") >= 2, text
