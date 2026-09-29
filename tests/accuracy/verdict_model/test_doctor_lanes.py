"""doctor's lane and plugin checks: whether a lane's runner starts, whether the
python it names imports pytest-cov, and which crapkit the plugin's hooks start.

README.md:791 is the rule: doctor FAILs a lane whose runner does not resolve
or that the shell cannot start, and a relative launcher answers the same from
any directory doctor runs in; `--plugin-root` checks the installed plugin
against the `crapkit` on PATH, the bare name its hooks and MCP server spawn,
reads only manifests named crapkit, and names a root it found. The lane's env
is merged over the inherited environment, and a PATH there replaces the
inherited one (docs/configuration.md:304).

Every expected verdict comes from the process the check is about, asked
directly: the lane's own shell, started from the lane's cwd with the lane's
environment, says whether the runner starts (it prints its own marker, which
reads the same under any shell and locale) and whether pytest_cov imports;
the shell's PATH lookup says which crapkit a bare `crapkit` starts, and that
program's exit code says whether it answered a version; json.load of each
manifest says which install is crapkit's.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import site
import subprocess
import sys

import pytest

from accuracy.kit import drive, repos
from accuracy.verdict_model import guard_repo as g

WINDOWS = os.name == "nt"
LANE = "py"
COV = "--cov=calc --cov-branch --cov-report=json:.crapkit/cov/py.json"
SUB = Path("calc") / "sub"
# The suite's own subprocess coverage must not measure a lane child (kit/repos.py),
# and a PYTHONPATH the suite runs under must not hand a lane's python the packages
# its own environment lacks.
QUIET = {"COVERAGE_PROCESS_CONFIG": "", "COV_CORE_DATAFILE": "", "PYTHONPATH": ""}
MARKER = "runcov 1.0"
FILES = {
    ".gitignore": ".venv/\n.crapkit/\n__pycache__/\n.coverage\ncoverage.json\n.pytest_cache/\n",
    "calc/__init__.py": "",
    "calc/core.py": "def double(x):\n    return 2 * x\n",
    "calc/sub/__init__.py": "",
    "calc/sub/more.py": "def half(x):\n    return x / 2\n",
    "tests/test_core.py": "from calc.core import double\n\n\ndef test_double():\n    assert double(2) == 4\n",
}


def config(command: str, env: dict | None = None) -> str:
    table = ", ".join(f"{key} = {g.toml_string(value)}" for key, value in {**QUIET, **(env or {})}.items())
    return ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "calc"\npaths = ["calc"]\n'
            'languages = ["python"]\n\n[exclude]\nglobs = ["tests/**"]\n\n'
            f'[[lane]]\nname = "{LANE}"\ncommand = {g.toml_string(command)}\n'
            'artifact = ".crapkit/cov/py.json"\nparser = "coveragepy"\nscopes = ["calc"]\n'
            f"env = {{ {table} }}\n")


def lane_repo(make_repo, command: str, env: dict | None = None) -> Path:
    files = {**FILES, "crapkit.toml": config(command, env)}
    return make_repo(repos.Spec(steps=(repos.Commit(files=files, message="seed"),))).root


# --- the oracles ---------------------------------------------------------------------------

def shell(command: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    """`command` run by the platform's shell (sh, cmd.exe), as a lane is run."""
    return subprocess.run(command, shell=True, cwd=cwd, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)


def lane_env(lane: dict | None = None, doctor: dict | None = None) -> dict:
    """The lane child's environment: doctor's own, the lane's pairs over it."""
    return {**drive.child_env(doctor), **QUIET, **(lane or {})}


def starts(word: str, cwd: Path, env: dict) -> str:
    """'ok' when the shell starts `word --version` (the runner prints MARKER), else 'FAIL'."""
    return "ok" if MARKER in shell(f"{word} --version", cwd, env).stdout else "FAIL"


def imports_pytest_cov(word: str, cwd: Path, env: dict) -> str:
    """'ok' when the python the shell starts for `word` imports pytest_cov."""
    return "ok" if shell(f'{word} -c "import pytest_cov"', cwd, env).returncode == 0 else "FAIL"


def prefix(word: str, cwd: Path, env: dict) -> str:
    """sys.prefix of the python the shell starts for `word`: the environment it
    installs into."""
    said = shell(f'{word} -c "import sys; print(sys.prefix)"', cwd, env).stdout.strip()
    return os.path.normcase(os.path.realpath(said))


# --- doctor --------------------------------------------------------------------------------

def doctor(cwd: Path, *args: str, env: dict | None = None) -> drive.Result:
    """doctor spawned from `cwd`: in-process runs share its per-word memos."""
    return drive.Driver(cwd, spawn=True, env=env).run("doctor", *args)


def lane_lines(result: drive.Result) -> list[str]:
    return [line for line in (result.stdout + result.stderr).splitlines() if f"lane '{LANE}'" in line]


def verdict(lines: list[str]) -> str:
    return "FAIL" if any(line.startswith("FAIL") for line in lines) else "ok"


def everywhere(root: Path, elsewhere: Path, env: dict | None = None) -> dict:
    """doctor's lines about the lane from the root, a subdirectory, and outside
    the repo through --repo."""
    elsewhere.mkdir(exist_ok=True)
    return {"root": lane_lines(doctor(root, env=env)),
            "subdirectory": lane_lines(doctor(root / SUB, env=env)),
            "outside": lane_lines(doctor(elsewhere, "--repo", str(root), env=env))}


# --- interpreters ----------------------------------------------------------------------------

def quoted(path) -> str:
    """A path as one shell word: double quotes read the same under sh and cmd.exe."""
    return f'"{path}"'


def venv_python(where: Path) -> Path:
    return where / ("Scripts/python.exe" if WINDOWS else "bin/python")


def make_venv(where: Path, packages: bool = True) -> Path:
    """A fresh venv at `where`. With `packages` its site-packages also reads
    this suite's own (pytest and pytest-cov); without, it holds the stdlib only."""
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(where)], check=True,
                   capture_output=True, timeout=300)
    if packages:
        own = [path for path in site.getsitepackages() if path.endswith("site-packages")]
        target = next(where.glob("Lib/site-packages" if WINDOWS else "lib/python*/site-packages"))
        (target / "accuracy_suite.pth").write_text("\n".join(own) + "\n", encoding="utf-8")
    return venv_python(where)


def launcher() -> str:
    """The relative launcher init writes into a lane."""
    return ".venv\\Scripts\\python.exe" if WINDOWS else ".venv/bin/python"


# --- a bare word, a relative launcher, and the directory doctor runs in ---------------------

def write_runner(root: Path) -> None:
    """A runner at the lane's cwd named `runcov`, for the platform's shell."""
    if WINDOWS:
        (root / "runcov.bat").write_text(f"@echo {MARKER}\r\n", encoding="ascii")
        return
    script = root / "runcov"
    script.write_text(f"#!/bin/sh\necho {MARKER}\n", encoding="ascii")
    script.chmod(0o755)


# cmd.exe looks in the current directory before PATH unless this is set; sh never
# does. Upper case: os.environ on Windows holds every name that way, so a mixed-case
# key would neither remove nor replace it.
NO_CWD = "NoDefaultCurrentDirectoryInExePath".upper()


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("cwd_lookup", ["on", "off"])
def test_doctor_resolves_a_bare_word_where_cmd_exe_does(make_repo, tmp_path, cwd_lookup):
    """A runner beside the lane, named bare. Whether the lane's shell starts it
    or not, doctor answers the same from the root, a subdirectory and outside."""
    own = {NO_CWD: None if cwd_lookup == "on" else "1"}
    root = lane_repo(make_repo, "runcov")
    write_runner(root)
    expected = starts("runcov", root, lane_env(doctor=own))
    seen = {where: verdict(lines) for where, lines in everywhere(root, tmp_path / "out", own).items()}
    assert seen == {"root": expected, "subdirectory": expected, "outside": expected}, (expected, seen)


@pytest.mark.nightly
@pytest.mark.process
def test_doctor_resolves_a_relative_launcher_from_the_lane_cwd(make_repo, tmp_path):
    """The launcher init writes, asked through --repo from outside the tree:
    the lane starts it from its own cwd, so doctor must look there."""
    root = lane_repo(make_repo, f"{launcher()} -m pytest {COV}")
    make_venv(root / ".venv")
    expected = imports_pytest_cov(launcher(), root, lane_env())
    (tmp_path / "out").mkdir()
    assert (expected, verdict(lane_lines(doctor(tmp_path / "out", "--repo", str(root))))) == ("ok", "ok")


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("plugin_installed", [True, False])
def test_doctor_finds_a_lane_word_from_any_directory(make_repo, tmp_path, plugin_installed):
    """The launcher's venv with and without pytest-cov: root, subdirectory and
    --repo from outside print the same lane lines, and the verdict is the lane
    shell's."""
    root = lane_repo(make_repo, f"{launcher()} -m pytest {COV}")
    make_venv(root / ".venv", packages=plugin_installed)
    expected = imports_pytest_cov(launcher(), root, lane_env())
    seen = everywhere(root, tmp_path / "out")
    assert seen["subdirectory"] == seen["root"] == seen["outside"], seen
    assert (verdict(seen["root"]), bool(seen["root"])) == (expected, True), seen


# --- the lane's environment ------------------------------------------------------------------

def _path_led_by(python: Path) -> str:
    return os.pathsep.join([str(python.parent), os.environ.get("PATH", "")])


@pytest.mark.nightly
@pytest.mark.process
def test_doctor_probes_with_the_lane_s_environment(make_repo, tmp_path):
    """doctor's own PATH leads to a python without pytest-cov; the lane's
    [lane.env] PATH leads to one with it. The lane runs, so doctor passes it
    and names the python the lane's shell starts."""
    good, bare = make_venv(tmp_path / "good"), make_venv(tmp_path / "bare", packages=False)
    lane, own = {"PATH": _path_led_by(good)}, {"PATH": _path_led_by(bare)}
    root = lane_repo(make_repo, f"python -m pytest {COV}", env=lane)
    env = lane_env(lane, own)
    expected = (imports_pytest_cov("python", root, env), prefix("python", root, env))
    lines = lane_lines(doctor(root, env=own))
    named = [line.split(" -> ")[1].split(" (pytest")[0] for line in lines if " -> " in line]
    assert (verdict(lines), [prefix(quoted(exe), root, env) for exe in named]) == \
        (expected[0], [expected[1]]), lines


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.platform("linux")
def test_doctor_reads_the_path_key_the_lane_runs_with(make_repo):
    """POSIX environment names are case-sensitive: a lane's `Path` is a second
    variable, and the lane's shell still searches PATH."""
    lane = {"Path": "/nonexistent-accuracy-dir"}
    root = lane_repo(make_repo, f"python -m pytest {COV}", env=lane)
    expected = imports_pytest_cov("python", root, lane_env(lane))
    assert (expected, verdict(lane_lines(doctor(root)))) == ("ok", "ok")


# How doctor words the WARN, as it prints it (README.md:791 names no text).
FOREIGN = "not the python running this doctor"


@pytest.mark.nightly
@pytest.mark.process
def test_doctor_tells_a_venv_from_its_base(make_repo, tmp_path):
    """doctor WARNs when the lane's python is another environment than its own,
    and a venv is one: sys.prefix tells them apart. The python running doctor
    itself earns no WARN."""
    other = make_venv(tmp_path / "other")
    own = drive.Driver(tmp_path).python
    seen = {}
    for name, python in (("other venv", str(other)), ("doctor's own", own)):
        root = lane_repo(make_repo, f"{quoted(python)} -m pytest {COV}")
        env = lane_env()
        expected = prefix(quoted(python), root, env) != prefix(quoted(own), root, env)
        seen[name] = (expected, any(FOREIGN in line for line in lane_lines(doctor(root))))
    assert seen == {"other venv": (True, True), "doctor's own": (False, False)}, seen


@pytest.mark.nightly
@pytest.mark.process
def test_the_pytest_cov_probe_asks_the_python_that_runs_pytest(make_repo):
    """`coverage run -m pytest --cov`: the lane runs (pytest accepts --cov, so
    pytest_cov imported in the python behind coverage), and doctor prints no
    missing-pytest-cov line for it."""
    command = f"coverage run -m pytest {COV}"
    root = lane_repo(make_repo, command)
    ran = shell(command, root, lane_env()).returncode
    missing = [line for line in lane_lines(doctor(root)) if "pytest_cov" in line]
    assert (ran, missing) == (0, [])


# --- --plugin-root ---------------------------------------------------------------------------

def fake_crapkit(where: Path, answer: str, code: int = 0) -> Path:
    """A `crapkit` launcher that prints `answer` and exits `code`."""
    where.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        (where / "crapkit.cmd").write_text(f"@echo {answer}\r\n@exit /b {code}\r\n", encoding="ascii")
    else:
        script = where / "crapkit"
        script.write_text(f"#!/bin/sh\necho '{answer}'\nexit {code}\n", encoding="ascii")
        script.chmod(0o755)
    return where


def path_without_crapkit(*first: Path) -> str:
    """PATH with `first` ahead and every directory holding a real crapkit dropped."""
    kept = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not shutil.which("crapkit", path=d)]
    return os.pathsep.join([*map(str, first), *kept])


def plugin(root: Path, name: str, version: str) -> Path:
    """An installed plugin: a manifest and a hooks file spawning a bare crapkit."""
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": version}))
    (root / "hooks").mkdir()
    hooks = {"hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [
        {"type": "command", "command": "crapkit", "args": ["claude-hook"]}]}]}}
    (root / "hooks" / "hooks.json").write_text(json.dumps(hooks))
    return root


def own_version(where: Path) -> str:
    return drive.Driver(where, spawn=True).run("--version").stdout.split()[-1]


def spawned_answer(env: dict, where: Path) -> tuple[int, str]:
    """What the plugin's bare `crapkit` answers, asked through the shell."""
    done = shell("crapkit --version", where, {**os.environ, **env})
    return done.returncode, done.stdout.strip()


def plugin_doctor(where: Path, plugin_root: Path, env: dict) -> drive.Result:
    return drive.Driver(where, spawn=True, env=env).run("doctor", "--plugin-root", str(plugin_root))


def expected_handshake(spawned: tuple[int, str], version: str) -> tuple[int, bool]:
    """(exit, prints a line): silence and exit 0 only when the crapkit the hook
    starts ran and answered the manifest's version (README.md:791)."""
    return (0, False) if spawned == (0, f"crapkit {version}") else (1, True)


def handshake_case(tmp_path: Path, root: Path, version: str, case: str, answer: str | None):
    """(expected, doctor's) for one PATH: a fake crapkit answering `answer`, or none."""
    fakes = [fake_crapkit(tmp_path / case, answer)] if answer else []
    env = {"PATH": path_without_crapkit(*fakes)}
    result = plugin_doctor(tmp_path, root, env)
    return expected_handshake(spawned_answer(env, tmp_path), version), (result.code, bool(result.stdout.strip()))


@pytest.mark.nightly
@pytest.mark.process
def test_doctor_compares_the_plugin_with_the_crapkit_the_hook_starts(tmp_path):
    """The manifest carries the version of the crapkit running doctor. The
    hook starts PATH's crapkit: when that one answers another version doctor
    names it, when it agrees doctor is silent, and when PATH has none it FAILs."""
    version = own_version(tmp_path)
    root = plugin(tmp_path / "plugin", "crapkit", version)
    cases = (("older", "crapkit 0.0.1"), ("same", f"crapkit {version}"), ("none", None))
    seen = {case: handshake_case(tmp_path, root, version, case, answer) for case, answer in cases}
    assert {case: want for case, (want, _) in seen.items()} == \
        {"older": (1, True), "same": (0, False), "none": (1, True)}
    assert all(want == got for want, got in seen.values()), seen


@pytest.mark.nightly
@pytest.mark.process
def test_doctor_takes_a_version_only_from_a_crapkit_that_exits_0(tmp_path):
    """A crapkit on PATH that prints a version-shaped word and exits 2 answered
    nothing: doctor never reports that word as the version the hook starts."""
    root = plugin(tmp_path / "plugin", "crapkit", own_version(tmp_path))
    env = {"PATH": path_without_crapkit(fake_crapkit(tmp_path / "broken", "crapkit 9.9.9", code=2))}
    code, said = spawned_answer(env, tmp_path)
    result = plugin_doctor(tmp_path, root, env)
    assert (code, said, "9.9.9" in result.stdout) == (2, "crapkit 9.9.9", False), result.stdout


@pytest.mark.nightly
@pytest.mark.process
def test_doctor_fails_a_crapkit_that_answers_no_version(tmp_path):
    """README.md:791: silence only when the two agree. A launcher that exits 2
    agrees with nothing, so doctor prints a line and exits non-zero."""
    root = plugin(tmp_path / "plugin", "crapkit", own_version(tmp_path))
    env = {"PATH": path_without_crapkit(fake_crapkit(tmp_path / "broken", "crapkit 9.9.9", code=2))}
    result = plugin_doctor(tmp_path, root, env)
    assert (spawned_answer(env, tmp_path)[0] != 0, result.code != 0, bool(result.stdout.strip())) == \
        (True, True, True), result.stdout


def claude_home(base: Path, version: str) -> Path:
    """Claude Code's layout: <config>/plugins/cache/<marketplace>/<plugin>/<version>/,
    another vendor's plugin at a higher version beside crapkit's install."""
    cache = base / ".claude" / "plugins" / "cache"
    plugin(cache / "other-market" / "security-guidance" / "2.0.6", "security-guidance", "2.0.6")
    plugin(cache / "crapkit" / "crapkit" / version, "crapkit", version)
    return base / ".claude"


def crapkit_installs(cache: Path) -> list[str]:
    """The installs json.load reads as crapkit's."""
    return [os.path.normcase(str(m.parent.parent)) for m in cache.rglob("plugin.json")
            if json.loads(m.read_text())["name"] == "crapkit"]


CHECKING = "crapkit doctor: checking "


def checked_roots(result: drive.Result) -> tuple[int, list[str]]:
    """doctor's exit and the roots it says it found and checked."""
    return result.code, [os.path.normcase(line.removeprefix(CHECKING))
                         for line in result.stdout.splitlines() if line.startswith(CHECKING)]


def plugin_root_answers(tmp_path: Path) -> tuple[list[str], dict]:
    """The crapkit install json.load finds, and doctor's (exit, names another
    vendor's version, roots it says it checked) from ~/.claude, ~/.claude/plugins
    and the cache, with PATH's crapkit answering the install's version."""
    version = own_version(tmp_path)
    home = claude_home(tmp_path, version)
    env = {"PATH": path_without_crapkit(fake_crapkit(tmp_path / "bin", f"crapkit {version}"))}
    answers = {}
    for start in (home, home / "plugins", home / "plugins" / "cache"):
        result = plugin_doctor(tmp_path, start, env)
        answers[start.name] = (result.code, "2.0.6" in result.stdout, checked_roots(result)[1])
    return crapkit_installs(home / "plugins" / "cache"), answers


@pytest.mark.nightly
@pytest.mark.process
def test_plugin_root_reads_only_crapkit_manifests(tmp_path):
    """From ~/.claude, ~/.claude/plugins and the cache itself, doctor checks the
    crapkit install, which agrees with PATH's crapkit, and never another
    vendor's plugin with a higher version."""
    expected, answers = plugin_root_answers(tmp_path)
    assert len(expected) == 1
    assert {name: (code, other, set(roots) <= set(expected)) for name, (code, other, roots) in answers.items()} \
        == {name: (0, False, True) for name in (".claude", "plugins", "cache")}, answers


@pytest.mark.nightly
@pytest.mark.process
def test_plugin_root_names_the_root_it_found(tmp_path):
    """README.md:791: a root doctor found rather than one typed is named first,
    as `crapkit doctor: checking PATH`."""
    expected, answers = plugin_root_answers(tmp_path)
    assert {name: roots for name, (_, _, roots) in answers.items()} == \
        {name: expected for name in (".claude", "plugins", "cache")}, answers
