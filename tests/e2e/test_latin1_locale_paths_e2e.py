"""A repo path with an accent, under a POSIX locale that is not UTF-8.

git names `pkg/café.py` in UTF-8 bytes, and crapkit keys it as that text.
Python hands a str back to the OS through the filesystem encoding, which on
POSIX is the locale's: under en_US.ISO-8859-1 it went back as
b"pkg/caf\\xe9.py", a file that does not exist. coverage skipped the file as
missing (2 files and 2 functions instead of 3 and 4; 8 functions and CRAP load
115.87 instead of 9 and 127.87 on the larger repo), and claude-hook exited 0
with nothing to say about an edit that broke the ceiling.

The console entry now starts the command again once with `-X utf8` when the
filesystem encoding is not UTF-8, so every open, stat and argument spells
UTF-8. The flag and not PYTHONUTF8, so the lane's own child keeps the locale
and the environment the user gave it.

The locales come from localedef into a directory glibc reads through LOCPATH,
so the test needs no root and no system locale. Windows and macOS name files
in UTF-8 whatever the locale, so the rows run on Linux.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import hang_guard
from conftest import child_env, cli_runner
from legacy_locale import missing

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="Linux only: Windows and macOS name files in UTF-8 whatever the locale")

run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace", spawn=True)

TARGET = "pkg/café.py"
CALC = '''def grade(score):
    if score > 90:
        return "A"
    elif score > 80:
        return "B"
    elif score > 70:
        return "C"
    elif score > 60:
        return "D"
    return "F"


def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
'''
TEST_CALC = '''from pkg.calc import clamp, grade


def test_grade():
    assert grade(95) == "A"


def test_clamp():
    assert clamp(5, 0, 10) == 5
'''
# Written by the lane's own pytest: what its interpreter was started with.
PROBE = '''import json, os, sys

with open("child-env.json", "w", encoding="ascii") as out:
    json.dump({"utf8_mode": sys.flags.utf8_mode, "PYTHONUTF8": os.environ.get("PYTHONUTF8"),
               "fs": sys.getfilesystemencoding()}, out)
'''
# The larger repo: nine functions in five files, one of them named with an accent.
MORE = {
    "pkg/twins.py": "".join(
        f"def handler(x):\n    if x > {i}:\n        return {i}\n    elif x < -{i}:\n        return -{i}\n"
        "    return 0\n\n\n" for i in range(3)),
    "pkg/b.py": "def clamp(x, lo, hi):\n    if x < lo:\n        return lo\n    if x > hi:\n        return hi\n"
                "    return x\n\n\ndef clamp2(x, lo, hi):\n    if x < lo:\n        return lo\n    if x > hi:\n"
                "        return hi\n    return x\n",
    "pkg/cé.py": "def accent(v):\n    for a in v:\n        if a:\n            return a\n    return None\n",
    "pkg/deep/z.py": "def z(a, b):\n    if a and b:\n        return 1\n    if a or b:\n        return 2\n    return 3\n",
}
LANE = ("python -m pytest -p no:cacheprovider -p no:randomly --cov=pkg --cov-branch "
        "--cov-report=json:.crapkit/cov/py.json")
TOML = f'''[crapkit]
target = 4

[[scope]]
name = "pkg"
paths = ["pkg"]
languages = ["python"]

[exclude]
globs = ["tests/**"]

[[lane]]
name = "py"
command = "{LANE}"
artifact = ".crapkit/cov/py.json"
parser = "coveragepy"
scopes = ["pkg"]
env = {{ COVERAGE_PROCESS_CONFIG = "", COV_CORE_DATAFILE = "" }}
'''
BUILT = {"en_US.ISO-8859-1": ("en_US", "ISO-8859-1"), "fr_FR.UTF-8": ("fr_FR", "UTF-8")}
LATIN1 = {"LANG": "en_US.ISO-8859-1", "LC_ALL": "en_US.ISO-8859-1"}
UTF8 = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
LOCALES = [
    ("c-utf8", UTF8, "utf-8"),
    ("c", {"LANG": "C", "LC_ALL": "C"}, "utf-8"),
    ("posix", {"LANG": "POSIX", "LC_ALL": "POSIX"}, "utf-8"),
    ("fr-utf8", {"LANG": "fr_FR.UTF-8", "LC_ALL": "fr_FR.UTF-8"}, "utf-8"),
    ("latin1", LATIN1, "iso8859-1"),
    ("latin1-pythonutf8", {**LATIN1, "PYTHONUTF8": "1"}, "utf-8"),
]


@pytest.fixture(scope="session")
def locpath(tmp_path_factory) -> Path:
    """en_US.ISO-8859-1 and fr_FR.UTF-8, built where glibc finds them through
    LOCPATH. A locale that does not build leaves its rows skipped, by name, or
    failed on a CI job that sets CRAPKIT_REQUIRE_LOCALES=1."""
    root = tmp_path_factory.mktemp("locales")
    for name, (source, charmap) in BUILT.items():
        if shutil.which("localedef"):
            subprocess.run(["localedef", "-i", source, "-f", charmap, str(root / name)],
                           capture_output=True, timeout=hang_guard.HANG_SECONDS)
    return root


def _env(locpath: Path, locale: dict) -> dict:
    """The locale and nothing else of the parent's: no inherited LC_* or
    PYTHON* text setting decides which spelling a row sees."""
    return {"LC_CTYPE": None, "LANGUAGE": None, "PYTHONIOENCODING": None, "PYTHONUTF8": None,
            "PYTHONCOERCECLOCALE": None, "LOCPATH": str(locpath), **locale}


def _filesystem_encoding(env: dict) -> str:
    probe = hang_guard.run([sys.executable, "-c", "import sys; print(sys.getfilesystemencoding())"],
                           env=child_env(env), text=True)
    return probe.stdout.strip()


def _in_force(locpath: Path, locale: dict, expected: str) -> dict:
    """The row's env, once an interpreter started under it reports the
    filesystem encoding the row is about."""
    env = _env(locpath, locale)
    found = _filesystem_encoding(env)
    if found != expected:
        missing(f"{locale['LANG']} gives filesystem encoding {found!r} here, not {expected!r}: "
                "localedef could not build it")
    return env


def _repo(tmp_path: Path, files: dict[str, str], named: dict[bytes, str] | None = None) -> Path:
    """The files, and `named` under names given as raw bytes."""
    repo = tmp_path / "repo"
    for rel, text in {"pkg/__init__.py": "", "pkg/calc.py": CALC, "tests/test_calc.py": TEST_CALC,
                      "tests/conftest.py": PROBE, "crapkit.toml": TOML,
                      ".gitignore": ".crapkit/\nchild-env.json\n", **files,
                      **{os.fsdecode(name): text for name, text in (named or {}).items()}}.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(text.encode("utf-8"))
    for args in (["init", "-q", "-b", "main"], ["config", "core.autocrlf", "false"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-q", "-m", "base"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return repo


def _coverage(repo: Path, env: dict) -> dict:
    result = run_cli(repo, "coverage", "--json", env_extra=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "missing from working tree" not in result.stderr, result.stderr
    return json.loads(result.stdout)


def _ranked(repo: Path, env: dict) -> set[str]:
    result = run_cli(repo, "worklist", "--json", env_extra=env)
    assert result.returncode == 0, result.stderr
    worklist = json.loads(result.stdout)
    return {row["path"] for row in worklist.get("active", []) + worklist.get("dormant", [])}


def _edit_payload(source: Path) -> str:
    return json.dumps({"session_id": "s", "transcript_path": str(source.parent / "t.jsonl"),
                       "cwd": str(source.parents[1]), "hook_event_name": "PostToolUse",
                       "tool_name": "Edit",
                       "tool_input": {"file_path": str(source), "old_string": "a", "new_string": "b"},
                       "tool_response": {"filePath": str(source), "success": True}})


@pytest.mark.parametrize("locale, expected", [row[1:] for row in LOCALES], ids=[row[0] for row in LOCALES])
def test_every_locale_scores_and_advises_on_a_name_with_an_accent(tmp_path, locpath, locale, expected):
    """coverage scores pkg/café.py, worklist ranks it, and claude-hook warns
    about an edit to it that breaks the ceiling, in every locale."""
    env = _in_force(locpath, locale, expected)
    repo = _repo(tmp_path, {TARGET: CALC.replace("grade", "accent")})

    summary = _coverage(repo, env)
    assert (summary["files"], summary["functions"]) == (3, 4), summary
    assert TARGET in _ranked(repo, env)

    source = repo / TARGET
    source.write_bytes(source.read_bytes().replace(
        b'    return "F"', b'    if score < 0:\n        return "?"\n    return "F"'))
    hook = run_cli(repo, "claude-hook", "--protocol", "1", env_extra=env, stdin=_edit_payload(source))
    assert hook.returncode == 2, hook.stderr
    assert f"1 function(s) over ceiling 4 in {TARGET}" in hook.stderr


def test_the_lane_keeps_the_locale_it_was_given(tmp_path, locpath):
    """crapkit restarts itself in UTF-8 mode with a flag; its lane child gets
    no PYTHONUTF8 and runs under the Latin-1 locale, and a PYTHONUTF8 the user
    set still reaches it."""
    env = _in_force(locpath, LATIN1, "iso8859-1")
    repo = _repo(tmp_path, {TARGET: CALC.replace("grade", "accent")})

    _coverage(repo, env)
    assert json.loads((repo / "child-env.json").read_text(encoding="ascii")) == {
        "utf8_mode": 0, "PYTHONUTF8": None, "fs": "iso8859-1"}

    _coverage(repo, {**env, "PYTHONUTF8": "1"})
    child = json.loads((repo / "child-env.json").read_text(encoding="ascii"))
    assert (child["utf8_mode"], child["PYTHONUTF8"]) == (1, "1")


def _mcp_frames(tool: str, arguments: dict) -> str:
    return "\n".join(json.dumps(frame, ensure_ascii=False) for frame in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}))


@pytest.mark.parametrize("locale, expected", [row[1:] for row in LOCALES], ids=[row[0] for row in LOCALES])
@pytest.mark.parametrize("tool, arguments", [
    ("get_function_brief", {"path": TARGET, "name": "accent"}),
    ("check_gate", {"path": TARGET}),
], ids=["brief", "check-gate"])
def test_every_locale_answers_an_mcp_call_on_a_name_with_an_accent(tmp_path, locpath, locale, expected,
                                                                    tool, arguments):
    """The server runs in UTF-8 mode and starts the CLI through the owned
    launcher. Under Latin-1 that launcher handed `pkg/café.py` on as
    b"pkg/caf\\xe9.py", even with PYTHONUTF8=1, which its `-I` ignores, and
    each tool answered isError true about pkg/caf�.py."""
    env = _in_force(locpath, locale, expected)
    repo = _repo(tmp_path, {TARGET: CALC.replace("grade", "accent")})
    _coverage(repo, env)

    result = run_cli(repo, "mcp", env_extra=env, stdin=_mcp_frames(tool, arguments))
    reply = json.loads(result.stdout.splitlines()[-1])["result"]

    assert reply.get("isError") is not True, reply
    assert TARGET in json.dumps(json.loads(reply["content"][0]["text"]), ensure_ascii=False), reply


def test_the_larger_repo_scores_the_same_under_latin1_as_under_utf8(tmp_path, locpath):
    """Nine functions and CRAP load 127.87 under both, the accented file's
    function included."""
    latin1 = _in_force(locpath, LATIN1, "iso8859-1")
    by_locale = {}
    for name, env in (("utf8", _env(locpath, UTF8)), ("latin1", latin1)):
        summary = _coverage(_repo(tmp_path / name, MORE), env)
        by_locale[name] = (summary["functions"], summary["files"], summary["crap_load"])

    assert by_locale["latin1"] == by_locale["utf8"]
    assert (by_locale["latin1"][0], by_locale["latin1"][2]) == (9, 127.87)


def test_the_accented_file_reads_measured_under_latin1_as_under_utf8(tmp_path, locpath):
    """The lane's own interpreter keeps the Latin-1 locale (no PYTHONUTF8, as
    ruled) and names the file 'pkg/cÃ©.py' in its report. That key matched no
    row, so accent() read untested where UTF-8 reads it measured: 5 measured
    files against 6. The adapter reads the key back as pkg/cé.py."""
    latin1 = _in_force(locpath, LATIN1, "iso8859-1")
    measured = {}
    for name, env in (("utf8", _env(locpath, UTF8)), ("latin1", latin1)):
        summary = _coverage(_repo(tmp_path / name, MORE), env)
        measured[name] = (summary["measured"], summary["untested"], summary["crap_load"])

    assert measured["latin1"] == measured["utf8"]


def test_pythonutf8_in_the_lanes_env_has_the_accented_file_measured(tmp_path, locpath):
    """PYTHONUTF8 in the lane's own `env` puts its coverage.py in UTF-8 mode
    under the same Latin-1 locale, and the file reads the same way."""
    latin1 = _in_force(locpath, LATIN1, "iso8859-1")
    utf8_lane = TOML.replace('COV_CORE_DATAFILE = "" }', 'COV_CORE_DATAFILE = "", PYTHONUTF8 = "1" }')
    measured = {name: _coverage(_repo(tmp_path / name, {**MORE, "crapkit.toml": toml}), env)["measured"]
                for name, env, toml in (("utf8", _env(locpath, UTF8), TOML), ("latin1", latin1, utf8_lane))}

    assert measured["latin1"] == measured["utf8"]


@pytest.mark.parametrize("locale", ["utf8", "latin1"])
def test_a_scoped_name_that_is_not_utf8_is_refused_in_either_locale(tmp_path, locpath, locale):
    """b"pkg/caf\\xe9.py" reads as café.py to a Latin-1 locale, and it is still
    a name crapkit cannot key: coverage refuses it the same way under both."""
    env = _in_force(locpath, LATIN1, "iso8859-1") if locale == "latin1" else _env(locpath, UTF8)
    repo = _repo(tmp_path, {}, named={b"pkg/caf\xe9.py": CALC})

    result = run_cli(repo, "coverage", env_extra=env)
    assert result.returncode == 3, result.stdout + result.stderr
    assert ("crapkit: pkg/caf\\xe9.py is in scope 'pkg', but git names it in bytes that are not UTF-8"
            in result.stderr), result.stderr


def test_a_path_argument_with_an_accent_reaches_its_file(tmp_path, locpath):
    """The shell passes `pkg/café.py` as UTF-8 bytes; the restart hands the
    same bytes on, so rescore reads the file instead of calling it missing."""
    env = _in_force(locpath, LATIN1, "iso8859-1")
    repo = _repo(tmp_path, {TARGET: CALC.replace("grade", "accent")})
    _coverage(repo, env)

    result = run_cli(repo, "rescore", TARGET, "--json", env_extra=env)
    assert result.returncode == 0, result.stderr
    assert {row["path"] for row in json.loads(result.stdout)["functions"]} == {TARGET}


def test_the_console_script_restarts_as_itself(tmp_path, locpath):
    """A console script restarts as the same script, so the next step it
    prints still names `crapkit`, not `python -m crapkit`."""
    env = _in_force(locpath, LATIN1, "iso8859-1")
    repo = _repo(tmp_path, {TARGET: CALC.replace("grade", "accent")})
    script = tmp_path / "bin" / "crapkit"
    script.parent.mkdir()
    script.write_text("import sys\nfrom crapkit.cli import main\nsys.exit(main())\n", encoding="utf-8")

    result = hang_guard.run([sys.executable, str(script), "coverage"], cwd=repo, env=child_env(env),
                            text=True, encoding="utf-8", errors="replace", timeout=300)
    assert result.returncode == 0, result.stderr
    assert "-> next: crapkit worklist" in result.stdout, result.stdout
    assert "missing from working tree" not in result.stderr
