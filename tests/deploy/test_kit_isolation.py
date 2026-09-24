"""The sandbox holds nothing from the machine running it.

A deploy cell that passes because a tool on the runner's PATH, a config in
the runner's home or the runner's own venv leaked into the sandbox is a false
green: the user it models has none of those. These tests run in every job
(marker `kit`) and fail the moment the sandbox stops being empty.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from kit import sandbox

pytestmark = pytest.mark.kit

WINDOWS = os.name == "nt"
ERROR_LINE = re.compile(r"^\s*(error|ERROR|npm error|npm ERR!|fatal:)")
IN_IMAGE = bool(os.environ.get("CRAPKIT_DEPLOY_IMAGE"))


def bindir(venv: Path) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin")


def error_lines(step) -> list[str]:
    return [line for line in (step.stdout + step.stderr).splitlines() if ERROR_LINE.match(line)]


def new_venv(box, name: str = "venv") -> Path:
    venv = box.root / name
    box.run([box.toolchain.python("3.12"), "-m", "venv", str(venv)], expect=0)
    return venv


# --- every installer works offline in a fresh sandbox --------------------------------

def uv_python_find(box, candidate):
    step = box.run(["uv", "python", "find", "3.12"], expect=0)
    assert Path(step.stdout.strip()).is_relative_to(box.toolchain["python_install_dir"])
    return step


def pip_in_a_new_venv(box, candidate):
    venv = new_venv(box)
    step = box.run([str(bindir(venv) / "python"), "-m", "pip", "install", "crapkit"], expect=0)
    version = box.run([str(bindir(venv) / "crapkit"), "--version"], expect=0)
    assert candidate.version in version.stdout
    return step


def pipx(box, candidate):
    return box.run(["pipx", "install", "crapkit"], expect=0)


def uv_tool(box, candidate):
    return box.run(["uv", "tool", "install", "crapkit"], expect=0)


def uvx(box, candidate):
    step = box.run(["uvx", "crapkit", "--version"], expect=0)
    assert candidate.version in step.stdout
    return step


def npm_ci_offline(box, candidate):
    project = box.root / "npm"
    project.mkdir()
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(Path(box.toolchain["npm_fixtures"]) / name, project / name)
    return box.run(["npm", "ci", "--offline", "--ignore-scripts"], cwd=project, expect=0)


@pytest.mark.parametrize("install", [uv_python_find, pip_in_a_new_venv, pipx, uv_tool, uvx, npm_ci_offline],
                         ids=lambda install: install.__name__)
def test_each_installer_works_offline_with_no_error_line(box, candidate, install):
    step = install(box, candidate)

    assert error_lines(step) == []


# --- crapkit is nowhere until an install puts it somewhere -----------------------------

def test_crapkit_resolves_to_nothing_before_install_and_to_that_install_after(box):
    assert box.which("crapkit") is None

    box.run(["uv", "tool", "install", "crapkit"], expect=0)
    assert box.which("crapkit") is None, "uv tool's bin dir reached PATH without the user adding it"
    tool_bin = box.run(["uv", "tool", "dir", "--bin"], expect=0).stdout.strip()
    box.prepend_path(tool_bin)

    assert Path(box.which("crapkit")).parent == Path(tool_bin)


def _normalized(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def test_the_runner_venv_never_reaches_the_sandbox_path(box):
    runner = {_normalized(path) for path in sandbox.runner_dirs()}
    runner.add(_normalized(os.path.dirname(box.toolchain["runner_python"])))

    assert runner.isdisjoint(_normalized(path) for path in box.path_dirs())
    prefix = box.run(["python" if WINDOWS else box.toolchain.python("3.12"), "-c", "import sys; print(sys.prefix)"],
                     expect=0).stdout.strip()
    assert _normalized(prefix) != _normalized(sys.prefix)


# --- the environment is an allowlist --------------------------------------------------

ALLOWED = (set(sandbox.OS_MINIMUM[os.name]) | set(sandbox.REDIRECTED) | set(sandbox.QUIET)
           | {"PATH", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON_DOWNLOADS", "npm_config_cache", "LANG", "LC_ALL", "TZ",
              "PIP_CONFIG_FILE", "UV_OFFLINE", "UV_NO_INDEX", "UV_FIND_LINKS", "HOMEDRIVE", "HOMEPATH"})


def test_the_sandbox_env_comes_from_the_allowlist_and_leaves_the_runner_switches_out(box):
    assert set(box.env) <= ALLOWED
    for name in ("PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "VIRTUAL_ENV"):
        assert name not in box.env


def _names(names) -> set[str]:
    """Environment names as the OS compares them: Windows ignores case."""
    return {name.upper() if WINDOWS else name for name in names}


def test_a_child_sees_only_the_allowlist(box):
    step = box.run([box.toolchain.python("3.12"), "-c", "import json, os; print(json.dumps(sorted(os.environ)))"],
                   expect=0)

    assert _names(json.loads(step.stdout)) <= _names(ALLOWED | {"PWD", "SHLVL", "_"})


def test_no_value_names_the_runners_home_outside_the_toolchain(box):
    home = _normalized(str(Path.home()))
    toolchain_root = _normalized(str(Path(box.toolchain.source).parent))
    leaked = [f"{key}={value}" for key, value in box.env.items() for part in value.split(os.pathsep)
              if _normalized(part).startswith(home) and not _normalized(part).startswith(toolchain_root)]

    assert leaked == []


# --- the machine's own state is untouched -----------------------------------------------

WHOLE_FILES = {".gitconfig", ".config/pip/pip.conf", "AppData/Roaming/pip/pip.ini", ".npmrc"}


def _crapkit_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if "crapkit" in line)


def _crapkit_part(home: Path, relative: str) -> str:
    """The digest of what a cell could write in one user file: the whole file
    for git, pip and npm config, the lines naming crapkit anywhere else."""
    path = home / relative
    if not path.exists():
        return "absent"
    if path.is_dir():
        return "dir:" + ",".join(sorted(entry.name for entry in path.iterdir()))
    text = path.read_text(encoding="utf-8", errors="replace")
    return hashlib.sha256((text if relative in WHOLE_FILES else _crapkit_lines(text)).encode()).hexdigest()


USER_FILES = [".claude.json", ".claude/settings.json", ".codex/config.toml", ".claude/plugins/cache/crapkit",
              ".codex/plugins/cache/crapkit", ".gemini/settings.json", ".cursor/mcp.json", ".copilot/mcp-config.json",
              ".cline/data/settings/cline_mcp_settings.json", ".local/bin/crapkit", ".local/bin/crapkit.exe",
              ".local/share/uv/tools/crapkit", ".local/share/pipx/venvs/crapkit", ".gitconfig",
              "AppData/Roaming/uv/tools/crapkit", "AppData/Roaming/Code/User/mcp.json", *sorted(WHOLE_FILES)]


def user_state(home: Path) -> dict[str, str]:
    return {relative: _crapkit_part(home, relative) for relative in USER_FILES}


def _sandbox_mentions(home: Path, root: Path) -> list[str]:
    files = [home / relative for relative in USER_FILES if (home / relative).is_file()]
    return [str(path) for path in files if str(root) in path.read_text(encoding="utf-8", errors="replace")]


CANARIES = {".config/pip/pip.conf": "[global]\nindex-url = http://canary.invalid/simple\n",
            ".gitconfig": "[user]\n\tname = canary\n\temail = canary@invalid\n",
            ".claude.json": '{"mcpServers": {"canary": {"command": "canary"}}}\n'}


def _plant(home: Path) -> dict[str, str]:
    planted = {relative: text for relative, text in CANARIES.items() if not (home / relative).exists()}
    for relative, text in planted.items():
        (home / relative).parent.mkdir(parents=True, exist_ok=True)
        (home / relative).write_text(text, encoding="utf-8")
    return planted


@pytest.fixture
def canaries():
    """Traps in the runner's home, planted only where that home is a throwaway
    container's: a leaked HOME would read the canary index and git identity."""
    planted = _plant(Path.home()) if IN_IMAGE else {}
    yield planted
    for relative in planted:
        (Path.home() / relative).unlink()


def test_installs_leave_the_machines_own_state_untouched(box, canaries):
    home = Path.home()
    before = user_state(home)
    box.run(["uv", "tool", "install", "crapkit"], expect=0)
    box.run(["pipx", "install", "crapkit"], expect=0)
    box.run(["git", "config", "--global", "alias.st", "status"], expect=0)
    identity = box.run(["git", "config", "user.name"], expect=0).stdout.strip()

    assert user_state(home) == before
    assert _sandbox_mentions(home, box.root) == []
    assert {relative: (home / relative).read_text(encoding="utf-8") for relative in canaries} == canaries
    assert identity == sandbox.GIT_IDENTITY[0]


# --- no harness binary changes mid-session ------------------------------------------------

def _harness_binaries(toolchain) -> list[Path]:
    dirs = [Path(directory) for directory in toolchain["harness_bin"] if Path(directory).is_dir()]
    return sorted({Path(os.path.realpath(entry)) for directory in dirs for entry in directory.iterdir()})


def _stamps(paths: list[Path]) -> dict[str, tuple[int, int]]:
    return {str(path): (path.stat().st_size, path.stat().st_mtime_ns) for path in paths if path.is_file()}


def test_no_harness_binary_changes_with_every_update_switch_set(box):
    binaries = _harness_binaries(box.toolchain)
    before = _stamps(binaries)
    for name in ("claude", "codex", "cursor-agent"):
        found = shutil.which(name, path=os.pathsep.join(box.toolchain["harness_bin"]))
        if found:
            box.run([found, "--version"])

    assert _stamps(binaries) == before
    assert all(box.env[name] == value for name, value in sandbox.QUIET.items())
    assert (box.home / ".codex" / "config.toml").read_text(encoding="utf-8") == sandbox.CODEX_CONFIG


# --- paths ---------------------------------------------------------------------------------

@pytest.mark.skipif(not WINDOWS, reason="8.3 short names exist only on Windows")
def test_the_windows_sandbox_path_is_its_long_form(box):
    assert box.root == sandbox.long_path(box.root)
    assert [value for value in box.env.values() if "~" in value] == []


def test_cloning_the_exported_bundle_raises_no_dubious_ownership(box):
    clone = box.root / "clone"
    box.run(["git", "clone", "-q", os.environ["CRAPKIT_DEPLOY_MIRROR"], str(clone)], expect=0)
    status = box.run(["git", "-C", str(clone), "status", "--porcelain"])

    assert "dubious ownership" not in status.stderr, status.stderr
    assert status.exit == 0


def test_odd_home_and_repo_names_survive(tmp_path, transcript, toolchain):
    odd = sandbox.make(tmp_path / "odd", transcript, toolchain=toolchain, home_name="O'Brien Jérôme test")
    repo = odd.root / "my repo é"
    repo.mkdir()
    (repo / "a.py").write_text("X = 1\n", encoding="utf-8")
    odd.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    odd.run(["git", "add", "-A"], cwd=repo, expect=0)
    odd.run(["git", "commit", "-q", "-m", "one"], cwd=repo, env=odd.commit_env(), expect=0)

    assert Path(odd.env["HOME"]).name == "O'Brien Jérôme test"
    assert odd.run(["git", "log", "--format=%an"], cwd=repo, expect=0).stdout.strip() == sandbox.GIT_IDENTITY[0]
