"""The alert command reads the same bytes on every OS: UTF-8, lines ended by LF.

`[crapkit] alert_command` gets the override line and the digest body on stdin.
Both writers opened the pipe in text mode with no newline control, so on
Windows Python turned every `\\n` into `\\r\\n` on the way in: a `cat >>
alerts.log` or `curl --data-binary @-` downstream stored CR LF from a Windows
committer and LF from everyone else. Every file crapkit writes for a person or
a diff is pinned to LF; the alert is text handed to another program, and it now
follows the same rule.
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


def test_a_failed_digest_alert_names_the_exit_code(tmp_path):
    with pytest.raises(ToolError, match=r"digest alert command failed \(exit 3\)"):
        _send_digest_alert(tmp_path, SimpleNamespace(alert_command=_command(FAILS)),
                           {"id": 1}, {"id": 2}, ["CRAP load 1.0 -> 2.0"])


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
