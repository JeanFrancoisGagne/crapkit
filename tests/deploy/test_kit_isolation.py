"""The sandbox holds nothing from the machine running it.

A deploy cell that passes because a tool on the runner's PATH, a config in
the runner's home or the runner's own venv leaked into the sandbox is a false
green: the user it models has none of those. These tests run in every job
(marker `kit`) and fail the moment the sandbox stops being empty. The last
sections hold every deploy test module to the kit (a cell that starts a
process past box.run sees this machine instead of the sandbox) and read a
cell's JUnit record back.
"""
from __future__ import annotations

import ast
import ctypes
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import tomllib
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import hang_guard
import pytest

from kit import cells, repos, sandbox, wheels

pytestmark = pytest.mark.kit

WINDOWS = os.name == "nt"
ERROR_LINE = re.compile(r"^\s*(error|ERROR|npm error|npm ERR!|fatal:)")
IN_IMAGE = bool(os.environ.get("CRAPKIT_DEPLOY_IMAGE"))
DEPLOY = Path(__file__).resolve().parent


def bindir(venv: Path) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin")


def error_lines(step) -> list[str]:
    return [line for line in (step.stdout + step.stderr).splitlines() if ERROR_LINE.match(line)]


def new_venv(box, name: str = "venv") -> Path:
    venv = box.root / name
    box.run([box.toolchain.python("3.12"), "-m", "venv", str(venv)], expect=0)
    return venv


def harness(box, command: str) -> str | None:
    """A pinned harness CLI from the toolchain's harness_bin dirs, or None
    where this image or machine does not hold it."""
    return shutil.which(command, path=os.pathsep.join(box.toolchain.get("harness_bin", [])))


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
    return box.run(["npm", "ci", "--offline", "--ignore-scripts"], cwd=project, expect=0, bound=sandbox.SLOW)


@pytest.mark.parametrize("install", [uv_python_find, pip_in_a_new_venv, pipx, uv_tool, uvx, npm_ci_offline],
                         ids=lambda install: install.__name__)
def test_each_installer_works_offline_with_no_error_line(box, candidate, install):
    step = install(box, candidate)

    assert error_lines(step) == []


# --- crapkit is nowhere until an install puts it somewhere -----------------------------

LAUNCHERS = ("crapkit.exe", "crapkit.cmd", "crapkit.bat", "crapkit") if WINDOWS else ("crapkit",)


def every_crapkit(box) -> list[str]:
    """Every file named like a crapkit launcher in any sandbox PATH dir."""
    return [str(Path(directory) / name) for directory in box.path_dirs() for name in LAUNCHERS
            if (Path(directory) / name).is_file()]


def uv_tool_bin(box) -> str:
    box.run(["uv", "tool", "install", "crapkit"], expect=0)
    return box.run(["uv", "tool", "dir", "--bin"], expect=0).stdout.strip()


def pipx_bin(box) -> str:
    box.run(["pipx", "install", "crapkit"], expect=0)
    return box.run(["pipx", "environment", "--value", "PIPX_BIN_DIR"], expect=0).stdout.strip()


def venv_bin(box) -> str:
    venv = new_venv(box)
    box.run([str(bindir(venv) / "python"), "-m", "pip", "install", "-q", "crapkit"], expect=0)
    return str(bindir(venv))


@pytest.mark.parametrize("install", [uv_tool_bin, pipx_bin, venv_bin], ids=lambda install: install.__name__)
def test_crapkit_resolves_to_nothing_before_install_and_only_to_that_install_after(box, install):
    assert box.which("crapkit") is None and every_crapkit(box) == []

    directory = install(box)
    assert box.which("crapkit") is None, "the install reached PATH before the user added its bin dir"
    box.prepend_path(directory)

    assert Path(box.which("crapkit")).parent == Path(directory)
    assert len(every_crapkit(box)) == 1, every_crapkit(box)


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
# What a runner or a developer's shell may export that a user's shell does not.
LEAKS = {"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "leak", "VIRTUAL_ENV": "leak",
         "PIP_INDEX_URL": "http://leak.invalid/simple", "UV_INDEX_URL": "http://leak.invalid/simple",
         "GIT_CONFIG_GLOBAL": "leak", "NODE_OPTIONS": "--no-warnings", "CRAPKIT_DEPLOY_LEAK_PROBE": "1"}


def test_the_sandbox_env_comes_from_the_allowlist_and_leaves_the_runner_switches_out(box):
    # run.py and entry.sh set both on the runner process; the sandbox drops them.
    assert os.environ.get("PYTHONHASHSEED") == "0" and os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"

    assert set(box.env) <= ALLOWED
    for name in ("PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "VIRTUAL_ENV"):
        assert name not in box.env


def _names(names) -> set[str]:
    """Environment names as the OS compares them: Windows ignores case."""
    return {name.upper() if WINDOWS else name for name in names}


def _child_env_names(box) -> set[str]:
    step = box.run([box.toolchain.python("3.12"), "-c", "import json, os; print(json.dumps(sorted(os.environ)))"],
                   expect=0)
    return _names(json.loads(step.stdout))


def test_a_child_sees_only_the_allowlist(box):
    assert _child_env_names(box) <= _names(ALLOWED | {"PWD", "SHLVL", "_"})


def test_nothing_the_runner_exports_reaches_a_new_sandbox(tmp_path, transcript, toolchain, monkeypatch):
    for name, value in LEAKS.items():
        monkeypatch.setenv(name, value)
    fresh = sandbox.make(tmp_path / "fresh", transcript, toolchain=toolchain)

    assert _names(LEAKS).isdisjoint(_names(fresh.env))
    assert _names(LEAKS).isdisjoint(_child_env_names(fresh))


def test_no_value_names_the_runners_home_outside_the_toolchain(box):
    home = _normalized(str(Path.home()))
    toolchain_root = _normalized(str(Path(box.toolchain.source).parent))
    leaked = [f"{key}={value}" for key, value in box.env.items() for part in value.split(os.pathsep)
              if _normalized(part).startswith(home) and not _normalized(part).startswith(toolchain_root)]

    assert leaked == []


# --- the machine's own state is untouched -----------------------------------------------
# A cell may write crapkit's entries into a harness config, a plugin cache or
# a tool dir, and git, pip or npm config. Each file is hashed for only that
# part, so a live session on this machine that bumps its own counters in
# ~/.claude.json does not read as a leak.

CANARY = "crapkit-deploy-canary"
PIP_USER_CONFIG = "AppData/Roaming/pip/pip.ini" if WINDOWS else ".config/pip/pip.conf"
WHOLE_FILES = [".gitconfig", ".config/git/config", ".npmrc", ".config/pip/pip.conf", "AppData/Roaming/pip/pip.ini"]
CONFIG_FILES = [".claude.json", ".claude/settings.json", ".claude/plugins/installed_plugins.json",
                ".claude/plugins/known_marketplaces.json", ".codex/config.toml", ".gemini/settings.json",
                ".cursor/mcp.json", ".copilot/mcp-config.json", ".copilot/config.json",
                ".cline/data/settings/cline_mcp_settings.json", ".config/Code/User/mcp.json",
                "AppData/Roaming/Code/User/mcp.json", "AppData/Roaming/Claude/claude_desktop_config.json"]
TREES = [".claude/plugins/cache/crapkit", ".claude/plugins/marketplaces/crapkit", ".codex/plugins/cache/crapkit",
         ".gemini/extensions/crapkit", ".local/share/uv/tools/crapkit", "AppData/Roaming/uv/tools/crapkit",
         ".local/share/pipx/venvs/crapkit", ".local/pipx/venvs/crapkit", ".local/bin/crapkit",
         ".local/bin/crapkit.exe"]


def _is_crapkit_key(key) -> bool:
    return str(key) == "crapkit" or str(key).startswith("crapkit@")


def _entry(key, value, path: tuple) -> dict:
    here = (*path, str(key))
    if _is_crapkit_key(key):
        return {".".join(here): value}
    # skillUsage, pluginUsage: counters a live session bumps, not config.
    return {} if str(key).endswith("Usage") else crapkit_entries(value, here)


def _as_mapping(node) -> dict:
    """A list as {index: item}, a dict as itself, anything else as nothing."""
    if isinstance(node, list):
        return {str(index): item for index, item in enumerate(node)}
    return node if isinstance(node, dict) else {}


def crapkit_entries(node, path: tuple = ()) -> dict:
    """Every value stored under a key named crapkit or crapkit@..., or a list
    item named crapkit, at any depth, keyed by its dotted path."""
    node = _as_mapping(node)
    if node.get("name") == "crapkit":
        return {".".join(path): node}
    found: dict = {}
    for key, value in node.items():
        found.update(_entry(key, value, path))
    return found


def parse_config(path: Path):
    """A harness config as data: TOML, JSON, or JSON behind // comment lines."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".toml":
        return tomllib.loads(text)
    return json.loads("\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//")))


def _digest(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _config_part(path: Path) -> str:
    """The crapkit entries of one config. A live harness may be rewriting the
    file this instant, so a failed parse is read again before the digest
    falls back to the lines that name crapkit."""
    for _ in range(20):
        try:
            return _digest(crapkit_entries(parse_config(path)))
        except ValueError:
            time.sleep(.05)
    text = path.read_text(encoding="utf-8", errors="replace")
    return _digest([line for line in text.splitlines() if "crapkit" in line])


def _whole_part(path: Path) -> str:
    """A whole file's digest. A canary, this run's or a concurrent run's,
    counts as no file: it comes and goes with the run that planted it."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return "absent" if CANARY in text else _digest(text)


def _tree_part(path: Path) -> str:
    if path.is_file():
        return f"file {path.stat().st_size}"
    return _digest(sorted(child.relative_to(path).as_posix() for child in path.rglob("*")))


def user_state(home: Path) -> dict[str, str]:
    """relative path -> the digest of what a cell could write there."""
    parts = [(WHOLE_FILES, _whole_part), (CONFIG_FILES, _config_part), (TREES, _tree_part)]
    return {relative: (reader(home / relative) if (home / relative).exists() else "absent")
            for relatives, reader in parts for relative in relatives}


def path_spellings(root: Path) -> set[str]:
    """How a config could spell a sandbox path: as the OS prints it, with
    forward slashes, and JSON-escaped; lower-cased where the OS ignores case."""
    spellings = {str(root), root.as_posix(), json.dumps(str(root))[1:-1]}
    return {spelling.lower() for spelling in spellings} if WINDOWS else spellings


def _mentions(text: str, spellings: set[str]) -> bool:
    text = text.lower() if WINDOWS else text
    return any(spelling in text for spelling in spellings)


def sandbox_mentions(home: Path, root: Path) -> list[str]:
    """User files on this machine that name a path inside the sandbox."""
    files = [home / relative for relative in WHOLE_FILES + CONFIG_FILES if (home / relative).is_file()]
    spellings = path_spellings(root)
    return [str(path) for path in files if _mentions(path.read_text(encoding="utf-8", errors="replace"), spellings)]


# Planted where the file is absent, and harmless to anything else reading it:
# git and pip skip a section they do not know.
NATIVE_CANARIES = {".config/git/config": f"[{CANARY}]\n\tplanted = yes\n",
                   PIP_USER_CONFIG: f"[{CANARY}]\nplanted = yes\n"}
# In a throwaway container home, canaries that would break a leaked install.
IMAGE_CANARIES = {".config/pip/pip.conf": f"# {CANARY}\n[global]\nindex-url = http://{CANARY}.invalid/simple\n",
                  ".gitconfig": f"# {CANARY}\n[user]\n\tname = {CANARY}\n\temail = canary@invalid\n",
                  ".npmrc": f"{CANARY}=planted\n",
                  ".claude.json": json.dumps({"mcpServers": {CANARY: {"command": CANARY}}}) + "\n"}


def _missing_dirs(path: Path) -> list[Path]:
    """The parent dirs of `path` that do not exist yet, deepest first."""
    missing = []
    for parent in path.parents:
        if parent.exists():
            break
        missing.append(parent)
    return missing


def _plant_one(path: Path, text: str) -> list[Path]:
    """The canary file and the dirs made for it, or [] where a file was."""
    made = _missing_dirs(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError:
        return []
    return [path, *made]


def plant(home: Path, canaries: dict[str, str]) -> tuple[dict[str, str], list[Path]]:
    """The canaries this call created, and every path it made for them (each
    file, then its new dirs deepest first). A file that exists stays as it is."""
    made = {relative: _plant_one(home / relative, text) for relative, text in canaries.items()}
    planted = {relative: canaries[relative] for relative, paths in made.items() if paths}
    return planted, sum(made.values(), [])


def _removed(path: Path) -> bool:
    """False while another process holds the file open: Windows refuses to
    delete a config file a concurrent git or pip is reading."""
    try:
        path.rmdir() if path.is_dir() else path.unlink(missing_ok=True)
    except PermissionError:
        return False
    except OSError:
        return True  # a dir something else now lives in stays
    return True


def unplant(made: list[Path]) -> None:
    for path in made:
        hang_guard.wait_until(lambda: _removed(path), what=f"{path} removed")


@pytest.fixture
def canaries():
    wanted = {**NATIVE_CANARIES, **(IMAGE_CANARIES if IN_IMAGE else {})}
    planted, made = plant(Path.home(), wanted)
    try:
        yield planted
    finally:
        unplant(made)


REGISTRATIONS = {"claude": ["mcp", "add", "--scope", "user", "crapkit", "--", "crapkit", "mcp"],
                 "codex": ["mcp", "add", "crapkit", "--", "crapkit", "mcp"]}


def register_everywhere(box) -> list[str]:
    """What a cell writes: crapkit installed by uv tool and pipx, a global git
    setting, and crapkit registered in each harness CLI this image holds."""
    box.run(["uv", "tool", "install", "crapkit"], expect=0)
    box.run(["pipx", "install", "crapkit"], expect=0)
    box.run(["git", "config", "--global", "alias.st", "status"], expect=0)
    ran = [name for name in REGISTRATIONS if harness(box, name)]
    for name in ran:
        box.run([harness(box, name), *REGISTRATIONS[name]], expect=0)
    return ran


def sandbox_registrations(box) -> dict:
    """Each sandbox harness config that holds a crapkit entry."""
    files = {box.home / ".claude.json", Path(box.env["CLAUDE_CONFIG_DIR"]) / ".claude.json",
             Path(box.env["CODEX_HOME"]) / "config.toml"}
    entries = {str(path): crapkit_entries(parse_config(path)) for path in files if path.is_file()}
    return {path: found for path, found in entries.items() if found}


def reads_of_the_canaries(box) -> list[str]:
    """Every sandbox command output that shows a canary: git, pip and npm config."""
    shown = [box.run(["git", "config", "--get", f"{CANARY}.planted"], expect=1).stdout,
             box.run([box.toolchain.python("3.12"), "-m", "pip", "config", "list"], expect=0).stdout,
             box.run(["npm", "config", "list"], expect=0).stdout]
    return [text for text in shown if CANARY in text]


def test_a_cell_leaves_the_machines_own_state_untouched_and_reads_none_of_it(box, canaries):
    home = Path.home()
    before = user_state(home)
    ran = register_everywhere(box)
    identity = box.run(["git", "config", "user.name"], expect=0).stdout.strip()

    assert user_state(home) == before
    assert sandbox_mentions(home, box.root) == []
    assert {relative: (home / relative).read_text(encoding="utf-8") for relative in canaries} == canaries
    assert reads_of_the_canaries(box) == [] and identity == sandbox.GIT_IDENTITY[0]
    assert len(sandbox_registrations(box)) == len(ran), (ran, sandbox_registrations(box))


def test_the_crapkit_entries_of_a_config_are_what_a_cell_writes_and_nothing_else():
    config = {"mcpServers": {"crapkit": {"command": "crapkit"}, "other": {}},
              "projects": {"/work/crapkit": {"mcpServers": {"crapkit": {"args": ["mcp"]}}, "lastCost": 3}},
              "skillUsage": {"crapkit": {"count": 9}}, "enabledPlugins": {"crapkit@crapkit": True},
              "plugins": [{"name": "crapkit", "version": "1"}, {"name": "other"}]}

    assert crapkit_entries(config) == {"mcpServers.crapkit": {"command": "crapkit"},
                                       "projects./work/crapkit.mcpServers.crapkit": {"args": ["mcp"]},
                                       "enabledPlugins.crapkit@crapkit": True,
                                       "plugins.0": {"name": "crapkit", "version": "1"}}


def test_user_state_holds_each_kind_of_file_to_what_a_cell_could_write(tmp_path):
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    (tmp_path / ".local" / "bin" / "crapkit").write_text("x", encoding="utf-8")
    (tmp_path / ".claude" / "plugins" / "cache" / "crapkit" / "crapkit" / "0.8.0").mkdir(parents=True)
    (tmp_path / ".gitconfig").write_text(f"# {CANARY}\n", encoding="utf-8")
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text('[mcp_servers.crapkit]\ncommand = "crapkit"\n', encoding="utf-8")
    state = user_state(tmp_path)

    assert state[".local/bin/crapkit"] == "file 1" and state[".gitconfig"] == "absent" and state[".npmrc"] == "absent"
    assert state[".claude/plugins/cache/crapkit"] == _digest(["crapkit", "crapkit/0.8.0"])
    assert state[".codex/config.toml"] == _digest({"mcp_servers.crapkit": {"command": "crapkit"}})


def test_a_config_that_never_parses_is_read_by_its_crapkit_lines(tmp_path):
    broken = tmp_path / ".claude.json"
    broken.write_text('{"mcpServers": {"crapkit":\n  "half\n', encoding="utf-8")

    assert _config_part(broken) == _digest(['{"mcpServers": {"crapkit":'])


def test_a_config_that_names_a_sandbox_path_in_any_spelling_is_caught(tmp_path):
    root = tmp_path / "box"
    config = tmp_path / ".claude.json"
    config.write_text(json.dumps({"projects": {str(root / "repo"): {}}}), encoding="utf-8")

    assert sandbox_mentions(tmp_path, root) == [str(config)]
    assert sandbox_mentions(tmp_path, tmp_path / "other") == []


def test_a_canary_is_planted_only_where_no_file_is_and_leaves_nothing_behind(tmp_path):
    (tmp_path / ".gitconfig").write_text("[user]\n\tname = someone\n", encoding="utf-8")
    (tmp_path / ".config").mkdir()
    planted, made = plant(tmp_path, {".gitconfig": "canary\n", ".config/git/config": "canary\n"})

    assert planted == {".config/git/config": "canary\n"}
    assert made == [tmp_path / ".config" / "git" / "config", tmp_path / ".config" / "git"]
    unplant(made)
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == [".config", ".gitconfig"]
    assert (tmp_path / ".gitconfig").read_text(encoding="utf-8") == "[user]\n\tname = someone\n"


def test_unplant_waits_out_a_reader_that_holds_the_canary_open(tmp_path):
    _, made = plant(tmp_path, {"held.cfg": "canary\n"})
    reader = open(made[0], encoding="utf-8")
    threading.Timer(.5, reader.close).start()
    unplant(made)

    assert not made[0].exists()
    # Windows refuses the delete until the reader closes; POSIX deletes at once.
    assert reader.closed or not WINDOWS


def test_unplant_keeps_a_dir_something_else_moved_into(tmp_path):
    _, made = plant(tmp_path, {"new/held.cfg": "canary\n"})
    (tmp_path / "new" / "other.cfg").write_text("mine\n", encoding="utf-8")
    unplant(made)

    assert [path.name for path in (tmp_path / "new").iterdir()] == ["other.cfg"]


# --- no harness binary changes mid-session ------------------------------------------------

UPDATE_ENV = {"DISABLE_AUTOUPDATER": "1", "COPILOT_AUTO_UPDATE": "false", "AMP_SKIP_UPDATE_CHECK": "1",
              "OPENCODE_DISABLE_AUTOUPDATE": "true"}
# (file under HOME, key path, value): each harness's own update switch.
UPDATE_FILES = [(".gemini/settings.json", ("general", "enableAutoUpdate"), False),
                (".config/amp/settings.json", ("amp.updates.mode",), "disabled"),
                (".config/opencode/opencode.json", ("autoupdate",), False),
                (".config/cursor/cli-config.json", ("channel",), "static"),
                (".codex/config.toml", ("check_for_update_on_startup",), False)]


def pinned_harnesses() -> dict[str, dict]:
    pins = tomllib.loads((wheels.SRC / "tools" / "deploy" / "pins.toml").read_text(encoding="utf-8"))
    return {name: spec for name, spec in pins["harness"].items() if "command" in spec}


def _value(data, keys: tuple):
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def update_switches(box) -> dict[str, object]:
    """Each update switch as the sandbox holds it, after the harnesses ran."""
    files = {relative: _value(parse_config(box.home / relative), keys) for relative, keys, _ in UPDATE_FILES}
    return {**{name: box.env.get(name) for name in UPDATE_ENV}, **files}


def must_start(spec: dict) -> bool:
    """An image runs every harness it holds (prototype/probe_offline.sh). A
    native toolchain must run its core harnesses; its full npm lock also
    installs Junie, whose launcher wants a shim in the home the image fixes,
    and omp, which wants Bun, and no native cell drives either."""
    return IN_IMAGE or spec.get("image") == "core"


def version_check(box, spec: dict) -> bool | None:
    """Whether `<command> --version` printed the pinned version, run the way
    a user who installed it has it: its bin dir on PATH. None for a harness
    this run does not need that could not start. The bound is SLOW: Copilot
    CLI unpacks itself into a fresh home on its first start, 105 s on a
    loaded Windows machine."""
    step = box.run([box.which(spec["command"]), "--version"], bound=sandbox.SLOW)
    if step.exit != 0 and not must_start(spec):
        box.transcript.note(f"{spec['command']} does not start here, and no cell on this machine drives it")
        return None
    return spec.get("prints", spec["version"]) in step.stdout + step.stderr


def held_harnesses(box) -> dict[str, dict]:
    """The pinned harnesses this image or toolchain holds."""
    return {name: spec for name, spec in pinned_harnesses().items() if harness(box, spec["command"])}


def run_each_harness(box) -> dict[str, bool]:
    """harness -> whether it printed its pinned version."""
    box.put_harnesses_on_path()
    checked = {name: version_check(box, spec) for name, spec in held_harnesses(box).items()}
    return {name: printed for name, printed in checked.items() if printed is not None}


def test_no_harness_binary_changes_during_the_session_with_every_update_switch_set(box, request):
    printed = run_each_harness(box)
    session_start = request.config.stash[cells.HARNESSES]

    assert sandbox.changed_stamps(session_start, sandbox.harness_stamps(box.toolchain)) == []
    assert printed == {name: True for name in printed}
    assert update_switches(box) == {**UPDATE_ENV, **{relative: value for relative, _, value in UPDATE_FILES}}


def test_a_changed_harness_binary_is_named():
    before = {"/opt/h/bin/claude": "10 1", "/opt/h/node_modules/@openai/codex/package.json": "0.156.1"}
    after = {"/opt/h/bin/claude": "12 2", "/opt/h/node_modules/@openai/codex/package.json": "0.156.1"}

    assert sandbox.changed_stamps(before, after) == ["/opt/h/bin/claude: 10 1 -> 12 2"]


# --- paths ---------------------------------------------------------------------------------

def short_path(path: Path) -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    return Path(buffer.value) if size else path


@pytest.mark.skipif(not WINDOWS, reason="8.3 short names exist only on Windows")
def test_the_windows_sandbox_sits_under_c_dt_in_its_long_form_with_no_tilde(box):
    assert _normalized(str(box.root)).startswith(_normalized("C:/dt") + os.sep)
    assert box.root == sandbox.long_path(box.root)
    assert [value for value in [str(box.root), *box.env.values(), *box.toolchain["path"]] if "~" in value] == []


@pytest.mark.skipif(not WINDOWS, reason="8.3 short names exist only on Windows")
def test_long_path_expands_an_8_3_name():
    long = Path(os.environ["ProgramFiles"])
    short = short_path(long)
    if short == long:
        pytest.skip(f"{long} has no 8.3 name on this volume")

    assert "~" in str(short) and sandbox.long_path(short) == long


DUBIOUS = "detected dubious ownership"


def test_the_exported_bundle_clones_with_no_dubious_ownership(box):
    mirror = Path(os.environ["CRAPKIT_DEPLOY_MIRROR"])
    clone = box.root / "clone"
    steps = [box.run(["git", "-C", str(mirror), "rev-parse", "--is-bare-repository"]),
             box.run(["git", "clone", "-q", str(mirror), str(clone)])]
    steps.append(box.run(["git", "-C", str(clone), "status", "--porcelain"]))

    assert [step.stderr for step in steps if DUBIOUS in step.stderr] == []
    assert [step.exit for step in steps] == [0, 0, 0], box.transcript.text()
    if not WINDOWS:
        assert mirror.stat().st_uid == os.getuid()


@pytest.mark.skipif(not IN_IMAGE, reason="plants a .git in /var/tmp, which only a throwaway container may")
def test_a_repo_another_user_owns_gets_gits_exact_refusal(box):
    """The control for the test above: in the image git refuses a work tree
    another uid owns, which is what a mounted source tree would be."""
    control = Path("/var/tmp")
    assert control.stat().st_uid != os.getuid()
    box.run(["git", "init", "-q", str(control)], expect=0)
    try:
        status = box.run(["git", "-C", str(control), "status"])
    finally:
        shutil.rmtree(control / ".git")

    assert f"fatal: {DUBIOUS} in repository at '{control}'" in status.stderr


def test_odd_home_and_repo_names_survive(tmp_path, transcript, toolchain, templates):
    odd = sandbox.make(tmp_path / "odd", transcript, toolchain=toolchain, home_name="O'Brien Jérôme test")
    repo = repos.checkout(odd, "py-pytest", cache=templates, repo_name="my repo é")
    (repo / "b.py").write_text("Y = 2\n", encoding="utf-8")
    odd.run(["git", "add", "-A"], cwd=repo, expect=0)
    odd.run(["git", "commit", "-q", "-m", "two"], cwd=repo, env=odd.commit_env(), expect=0)
    home = odd.run([odd.toolchain.python("3.12"), "-c", "import pathlib; print(pathlib.Path.home().name)"],
                   expect=0)

    assert repo.name == "my repo é" and home.stdout.strip() == "O'Brien Jérôme test"
    assert odd.run(["git", "log", "-1", "--format=%an"], cwd=repo, expect=0).stdout.strip() == sandbox.GIT_IDENTITY[0]


# --- every deploy test goes through the kit -----------------------------------------------

# The names a module starts a process with, past box.run and box.script: a module that exists
# to start them, or a call by its dotted name. A longer name counts as the first of these it
# starts with, so multiprocessing.pool.Pool counts as multiprocessing.
SHELL_OUTS = re.compile(r"subprocess|pty|multiprocessing|asyncio\.subprocess|asyncio\.create_subprocess_\w+"
                        r"|.+\.subprocess_(exec|shell)|concurrent\.futures\.(process|ProcessPoolExecutor)"
                        r"|(.+\.)?hang_guard\.run|os\.(system|popen|startfile|fork\w*|exec\w+|spawn\w+|posix_spawnp?)")
# A module that must reach this machine on purpose: the names it may use, and why.
REACHES_THE_MACHINE = {
    "test_harness_profiles.py": ({"subprocess"}, "win-profiles-sim asks PowerShell for the CI runner's own $PROFILE, "
                                                 "which Zed's spawn runs and a sandbox HOME cannot stand in for"),
    "test_gui_harnesses.py": ({"subprocess"}, "lin-zed-xvfb starts Zed under xvfb-run in a session of its own and "
                                              "ends the process group once Zed.log holds a line: box.run waits for the "
                                              "child to exit, and Zed runs until its window closes. The Popen call "
                                              "passes box.env and box.resolve(argv[0]), so Zed still runs in the sandbox"),
}


def _imported(node: ast.ImportFrom) -> list[str]:
    """`from os import system` reaches os and os.system."""
    return [str(node.module), *(f"{node.module}.{alias.name}" for alias in node.names)]


def _binding(node: ast.Import | ast.ImportFrom, alias: ast.alias) -> tuple[str, str]:
    """The name an import binds and the dotted name it stands for: `import os as o` binds o to os,
    `from concurrent import futures` binds futures to concurrent.futures."""
    if isinstance(node, ast.ImportFrom):
        return alias.asname or alias.name, f"{node.module}.{alias.name}"
    return alias.asname or alias.name, alias.name


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Every name the module's imports bind, and the dotted name each stands for."""
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    return dict(_binding(node, alias) for node in imports for alias in node.names)


def _spelled_out(node: ast.Attribute, aliases: dict[str, str]) -> str:
    """An attribute's dotted name with its first name replaced by what the import bound: o.system -> os.system."""
    first, dot, rest = ast.unparse(node).partition(".")
    return aliases.get(first, first) + dot + rest


def _reached(node, aliases: dict[str, str]) -> list[str]:
    """What an import or attribute node names: 'subprocess', 'os.system' ..."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return _imported(node)
    return [_spelled_out(node, aliases)] if isinstance(node, ast.Attribute) else []


def _shell_out(name: str) -> str | None:
    """The shortest start of a dotted name that SHELL_OUTS matches: subprocess for subprocess.DEVNULL."""
    parts = name.split(".")
    return next((start for size in range(1, len(parts) + 1)
                 if SHELL_OUTS.fullmatch(start := ".".join(parts[:size]))), None)


def shell_outs(tree: ast.AST) -> list[str]:
    """Every way a module starts a process without box.run or box.script, each once, in the order they appear."""
    aliases = _aliases(tree)
    found = [_shell_out(name) for node in ast.walk(tree) for name in _reached(node, aliases)]
    return list(dict.fromkeys(name for name in found if name))


def unexplained_shell_outs(name: str, tree: ast.AST) -> list[str]:
    """The shell-outs of module `name` that REACHES_THE_MACHINE does not explain."""
    allowed = REACHES_THE_MACHINE.get(name, (set(), ""))[0]
    return [reached for reached in shell_outs(tree) if reached not in allowed]


def shell_out_refusal(found: dict[str, list[str]]) -> str:
    """What a packet author reads when the rule fails: each module, what it starts, and the two ways to pass."""
    named = [f"{name} starts a process with {', '.join(names)}, past the sandbox" for name, names in found.items()]
    return "\n".join([*named, "Start it with box.run or box.script, which run it in the sandbox and record it in the "
                              "transcript. A module that has to start it itself (a process box.run cannot wait on, "
                              "or one only the runner's own environment answers) needs an entry in "
                              "REACHES_THE_MACHINE in tests/deploy/test_kit_isolation.py: the names it uses and why."])


def deploy_modules(folder: Path = DEPLOY) -> dict[str, ast.Module]:
    """Every module at the top of the deploy folder: the cells and the helpers and conftest they run.
    The kit package below it is where box.run starts processes, so the rule leaves it out."""
    return {path.name: ast.parse(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.py"))}


def test_no_deploy_test_module_starts_a_process_past_the_sandbox():
    found = {name: names for name, tree in deploy_modules().items() if (names := unexplained_shell_outs(name, tree))}

    assert found == {}, shell_out_refusal(found)


def test_a_shell_out_is_caught_unless_the_module_is_a_reviewed_exception():
    tree = ast.parse("import subprocess\nimport os\n\ndef test_x():\n    os.system('crapkit')\n")

    assert shell_outs(tree) == ["subprocess", "os.system"]
    assert unexplained_shell_outs("test_x.py", tree) == ["subprocess", "os.system"]
    assert unexplained_shell_outs("test_harness_profiles.py", tree) == ["os.system"]
    assert unexplained_shell_outs("test_gui_harnesses.py", tree) == ["os.system"]


def test_a_shell_out_failure_names_each_module_and_both_ways_to_pass():
    refusal = shell_out_refusal({"test_x.py": ["subprocess", "os.system"], "test_y.py": ["os.popen"]})

    assert refusal.splitlines()[:2] == ["test_x.py starts a process with subprocess, os.system, past the sandbox",
                                        "test_y.py starts a process with os.popen, past the sandbox"]
    assert "Start it with box.run or box.script" in refusal
    assert "an entry in REACHES_THE_MACHINE in tests/deploy/test_kit_isolation.py" in refusal


@pytest.mark.parametrize("source, reached", [
    ("from os import system\nsystem('crapkit')\n", "os.system"),
    ("from subprocess import Popen\n", "subprocess"),
    ("import os\nos.execvp('crapkit', ['crapkit'])\n", "os.execvp"),
    ("import os\nos.spawnlp(os.P_WAIT, 'crapkit', 'crapkit')\n", "os.spawnlp"),
    ("import os\nos.posix_spawnp('crapkit', ['crapkit'], {})\n", "os.posix_spawnp"),
    ("import os\nos.forkpty()\n", "os.forkpty"),
    ("import asyncio\nasyncio.create_subprocess_exec('crapkit')\n", "asyncio.create_subprocess_exec"),
    ("from asyncio import create_subprocess_shell\n", "asyncio.create_subprocess_shell"),
    ("import pty\npty.spawn(['crapkit'])\n", "pty"),
    ("import multiprocessing\n", "multiprocessing"),
    ("import hang_guard\nhang_guard.run(['crapkit'])\n", "hang_guard.run"),
    ("from hang_guard import run\n", "hang_guard.run"),
    ("from kit.hang_guard import run\n", "kit.hang_guard.run"),
    ("import multiprocessing.pool\n", "multiprocessing"),
    ("from multiprocessing.pool import Pool\n", "multiprocessing"),
    ("from multiprocessing.context import Process\n", "multiprocessing"),
    ("import asyncio.subprocess\nasyncio.subprocess.create_subprocess_exec('crapkit')\n", "asyncio.subprocess"),
    ("from asyncio.subprocess import create_subprocess_exec\n", "asyncio.subprocess"),
    ("from asyncio import subprocess as asp\nasp.create_subprocess_exec('crapkit')\n", "asyncio.subprocess"),
    ("import asyncio\nasyncio.subprocess.create_subprocess_shell('crapkit')\n", "asyncio.subprocess"),
    ("loop.subprocess_exec(Protocol, 'crapkit')\n", "loop.subprocess_exec"),
    ("asyncio.get_event_loop().subprocess_shell(Protocol, 'crapkit')\n", "asyncio.get_event_loop().subprocess_shell"),
    ("from concurrent.futures import ProcessPoolExecutor\n", "concurrent.futures.ProcessPoolExecutor"),
    ("from concurrent.futures.process import ProcessPoolExecutor\n", "concurrent.futures.process"),
    ("import concurrent.futures\nconcurrent.futures.ProcessPoolExecutor()\n", "concurrent.futures.ProcessPoolExecutor"),
    ("from concurrent import futures\nfutures.ProcessPoolExecutor()\n", "concurrent.futures.ProcessPoolExecutor"),
    ("import os as o\no.system('crapkit')\n", "os.system"),
    ("import asyncio as aio\naio.create_subprocess_exec('crapkit')\n", "asyncio.create_subprocess_exec"),
], ids=lambda value: value.splitlines()[0])
def test_every_way_a_module_starts_a_process_is_a_shell_out(source, reached):
    assert reached in shell_outs(ast.parse(source))


def test_a_call_through_a_process_module_counts_once_as_the_module_an_entry_names():
    tree = ast.parse("import subprocess as sp\nsp.Popen(['zed'], stdout=sp.DEVNULL)\nsp.run(['zed'])\n")

    assert shell_outs(tree) == ["subprocess"]
    assert unexplained_shell_outs("test_gui_harnesses.py", tree) == []


def test_the_kits_own_calls_and_the_os_helpers_are_not_shell_outs():
    source = ("import os, asyncio, hang_guard\nfrom os import environ\nfrom . import runner\n"
              "hang_guard.wait_until(ready)\nhang_guard.exited(process)\nbox.run(['crapkit'])\nos.path.join('a')\n"
              "import os as o, hang_guard as hg\nfrom concurrent import futures\nfrom concurrent.futures import "
              "ThreadPoolExecutor\no.path.join('a')\no.environ.get('A')\nhg.wait_until(ready)\n"
              "futures.ThreadPoolExecutor()\nasyncio.run(main())\nself.subprocess_log.write('a')\n")

    assert shell_outs(ast.parse(source)) == []


def test_the_rule_reads_every_module_at_the_top_of_the_deploy_folder_and_leaves_the_kit_out(tmp_path):
    for path in ("test_cell.py", "packet_support.py", "conftest.py", "kit/sandbox.py", "notes.txt"):
        (tmp_path / path).parent.mkdir(exist_ok=True)
        (tmp_path / path).write_text("import subprocess\n", encoding="utf-8")

    assert sorted(deploy_modules(tmp_path)) == ["conftest.py", "packet_support.py", "test_cell.py"]


def test_every_deploy_test_is_a_kit_test_or_a_cell(request):
    """A test with neither mark runs in no job. Several tests may share one
    cell id: a cell's steps, or its passing half beside a strict xfail."""
    assert request.config.stash.get(cells.LOOSE, None) == []


# A loose test, a helper marked kit on the function, and a cell, collected
# under -m kit as run.py runs: the rule must see the loose test before -m
# deselects it.
LOOSE_PROBE = '''import pytest
from kit.cells import cell
from test_kit_isolation import test_every_deploy_test_is_a_kit_test_or_a_cell as the_rule

PACKET = "deploy-kit-probe"
test_rule = pytest.mark.kit(the_rule)


def test_loose():
    pass


@pytest.mark.kit
def test_a_helper():
    pass


@cell("lin-loose-probe", channel="c", harness="h", scenario="s", use_cases="u", os="linux", cadence="push")
def test_a_cell():
    pass
'''


def test_a_loose_test_fails_the_rule_even_where_a_mark_filter_drops_it(box, toolchain):
    run = run_probe_suite(box, LOOSE_PROBE, toolchain.source, "-m", "kit")

    assert run.exit == 1 and "1 failed, 1 passed, 2 deselected" in run.stdout
    assert "'test_probe.py::test_loose'" in run.stdout
    assert "::test_a_helper" not in run.stdout and "::test_a_cell" not in run.stdout


def test_a_packets_own_kit_tests_run_under_its_packet():
    assert cells.selected(None, [], "deploy-upgrade", home="deploy-upgrade")
    assert not cells.selected(None, [], "deploy-git", home="deploy-upgrade")
    assert cells.selected(None, [], "deploy-kit", home="deploy-upgrade")
    assert not cells.selected(None, ["lin-x"], "deploy-upgrade", home="deploy-upgrade")


# --- a cell's JUnit record ------------------------------------------------------------------

ONE_CELL = '''from kit.cells import cell

PACKET = "deploy-kit-probe"


@cell("lin-junit-probe", channel="pip venv", harness="none", scenario="fresh: a probe", use_cases="junit",
      os=("linux", "windows"), image="cells", cadence="push", real_cli=False)
def test_probe():
    pass
'''


def junit_properties(path: Path) -> dict[str, str]:
    return {prop.get("name"): prop.get("value") for prop in ElementTree.parse(path).iter("property")}


def run_probe_suite(box, source: str, toolchain_json: Path, *pytest_args: str):
    """A one-module pytest session under this conftest, as run.py runs a cell:
    xunit1 JUnit at <box>/junit.xml. Returns the step; its exit is the run's."""
    suite = box.root / "suite"
    suite.mkdir()
    shutil.copyfile(DEPLOY / "conftest.py", suite / "conftest.py")
    (suite / "test_probe.py").write_text(source, encoding="utf-8")
    env = {"PYTHONPATH": os.pathsep.join([str(DEPLOY), str(DEPLOY.parent)]),
           "CRAPKIT_DEPLOY_TOOLCHAIN": str(toolchain_json), "CRAPKIT_DEPLOY_IMAGE_DIGEST": "sha256:probe",
           "CRAPKIT_DEPLOY_OUT": str(box.root / "out")}
    return box.run([box.toolchain["runner_python"], "-m", "pytest", str(suite), "-q", "-p", "no:cacheprovider",
                    "-o", "junit_family=xunit1", f"--junitxml={box.root / 'junit.xml'}", *pytest_args],
                   cwd=suite, env=env)


def test_a_cells_junit_record_names_every_field_the_image_and_the_toolchain(box, toolchain):
    assert run_probe_suite(box, ONE_CELL, toolchain.source).exit == 0

    assert junit_properties(box.root / "junit.xml") == {
        "cell_id": "lin-junit-probe", "cell_channel": "pip venv", "cell_harness": "none",
        "cell_scenario": "fresh: a probe", "cell_use_cases": "junit", "cell_os": "linux,windows",
        "cell_image": "cells", "cell_cadence": "push", "cell_real_cli": "False", "cell_packet": "deploy-kit-probe",
        "image_digest": "sha256:probe", "toolchain_hash": hashlib.sha256(toolchain.source.read_bytes()).hexdigest()}


# A test that rewrites a harness binary, as a harness updating itself would.
SELF_UPDATE = '''import json, os
from pathlib import Path

import pytest

pytestmark = pytest.mark.kit


def test_a_harness_updates_itself():
    toolchain = json.loads(Path(os.environ["CRAPKIT_DEPLOY_TOOLCHAIN"]).read_text(encoding="utf-8"))
    (Path(toolchain["harness_bin"][0]) / "fakeharness").write_text("v2, a longer release", encoding="utf-8")
'''


def test_a_harness_that_changes_mid_session_fails_the_run_naming_it(box, toolchain):
    bin_dir = box.root / "harness" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "fakeharness").write_text("v1", encoding="utf-8")
    fake = box.root / "toolchain.json"
    fake.write_text(json.dumps({**toolchain.data, "harness_bin": [str(bin_dir)]}), encoding="utf-8")
    run = run_probe_suite(box, SELF_UPDATE, fake)

    assert run.exit == 1 and "1 passed" in run.stdout
    assert f"a harness changed during the session: {bin_dir / 'fakeharness'}: 2 " in run.stderr
