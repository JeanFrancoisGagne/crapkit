"""An environment that holds nothing from the machine running the cell.

The environment is built from an allowlist, never copied from os.environ:

  kept        the pinned toolchain (UV_PYTHON_INSTALL_DIR, UV_PYTHON_DOWNLOADS=never,
              npm_config_cache) and the OS minimum a process needs to start;
              PATH is the toolchain's own list, git included
  redirected  HOME and every per-user directory a harness reads (USERPROFILE,
              APPDATA, XDG_*, CODEX_HOME, CLAUDE_CONFIG_DIR, TMP ...), each a
              fresh directory under the sandbox root
  offline     PIP_CONFIG_FILE names a sandbox pip.conf (no-index, find-links
              one per line: pip splits a list value on whitespace); uv gets
              UV_OFFLINE, UV_NO_INDEX and a comma-separated UV_FIND_LINKS
  quiet       every harness's update switch, so no binary changes mid-session

Git config lives in $HOME/.gitconfig and nowhere else: pre-commit strips GIT_*
variables, so GIT_CONFIG_GLOBAL would vanish under a hook. PYTHONHASHSEED and
PYTHONDONTWRITEBYTECODE belong to the runner process and never reach a cell.

    box = sandbox.make(tmp_path, transcript)
    box.run(["uv", "tool", "install", "crapkit"], expect=0)
    box.which("crapkit")
"""
from __future__ import annotations

import ctypes
import json
import locale
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import hang_guard

from kit.transcript import Step, Transcript

WINDOWS = os.name == "nt"
# Per-user state, each pointed at a fresh directory under the sandbox root.
REDIRECTED = {
    "HOME": "home", "USERPROFILE": "home",
    "APPDATA": "home/AppData/Roaming", "LOCALAPPDATA": "home/AppData/Local",
    "XDG_CONFIG_HOME": "home/.config", "XDG_DATA_HOME": "home/.local/share",
    "XDG_CACHE_HOME": "home/.cache", "XDG_STATE_HOME": "home/.local/state",
    "CODEX_HOME": "home/.codex", "CLAUDE_CONFIG_DIR": "home/.claude", "GEMINI_CLI_HOME": "home",
    "COPILOT_HOME": "home/.copilot", "COPILOT_CACHE_HOME": "home/.cache/copilot",
    "CLINE_DIR": "home/.cline", "GOOSE_PATH_ROOT": "home/.local/share/goose",
    "OPENCODE_CONFIG_DIR": "home/.config/opencode", "PI_CODING_AGENT_DIR": "home/.omp/agent",
    "CRUSH_GLOBAL_CONFIG": "home/.config/crush", "CRUSH_GLOBAL_DATA": "home/.local/share/crush",
    "npm_config_prefix": "home/.npm-global", "npm_config_logs_dir": "home/.npm/_logs",
    "TMP": "tmp", "TEMP": "tmp", "TMPDIR": "tmp",
}
# What a process needs to start, copied from the runner when present.
OS_MINIMUM = {
    "posix": ["LANG", "LC_ALL", "TZ", "USER", "LOGNAME"],
    "nt": ["SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE", "OS",
           "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "ProgramData",
           "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles", "USERNAME",
           "COMPUTERNAME", "USERDOMAIN"],
}
# Every harness's update and telemetry switch that an environment variable sets.
QUIET = {
    "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1",
    "NO_UPDATE_NOTIFIER": "1", "npm_config_update_notifier": "false", "npm_config_fund": "false",
    "npm_config_audit": "false", "GOOSE_DISABLE_KEYRING": "1", "GEMINI_TELEMETRY_ENABLED": "false",
}
# The same switches where a harness reads them from a file in $HOME.
QUIET_FILES = {
    ".gemini/settings.json": {"general": {"disableAutoUpdate": True, "disableUpdateNag": True},
                              "privacy": {"usageStatisticsEnabled": False}, "telemetry": {"enabled": False}},
    ".config/opencode/opencode.json": {"$schema": "https://opencode.ai/config.json", "autoupdate": False},
    ".config/amp/settings.json": {"amp.updates.mode": "disabled"},
}
CODEX_CONFIG = "check_for_update_on_startup = false\n"
GIT_IDENTITY = ("Deploy Cell", "deploy-cell@example.com")
SESSION_START = datetime.now(timezone.utc).replace(microsecond=0)


# --- the pinned toolchain -------------------------------------------------------

@dataclass(frozen=True)
class Toolchain:
    """toolchain.json: where the image or toolchain.py put each pinned tool."""
    data: dict
    source: Path

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Toolchain":
        source = Path(path or os.environ["CRAPKIT_DEPLOY_TOOLCHAIN"])
        return cls(json.loads(source.read_text(encoding="utf-8")), source)

    def __getitem__(self, key: str):
        return self.data[key]

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def pythons(self) -> dict[str, str]:
        """minor -> interpreter path, e.g. {"3.12": "/opt/toolchain/bin/python3.12"}."""
        if "pythons" in self.data:
            return dict(self.data["pythons"])
        found = sorted(Path(self.data["python_dir"]).glob("python3.*"))
        return {path.name[len("python"):]: str(path) for path in found}

    def python(self, minor: str) -> str:
        return self.pythons()[minor]


# --- paths ----------------------------------------------------------------------

def long_path(path: Path) -> Path:
    """The path with every 8.3 short component expanded (Windows only)."""
    if not WINDOWS:
        return path
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetLongPathNameW(str(path), buffer, len(buffer))
    return Path(buffer.value) if size else path


def _encoded(text: str) -> str:
    return text.encode("utf-8", "replace").decode("utf-8")


def decode(data: bytes) -> str:
    """A child's output: UTF-8 when it is, else the locale's code page."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), "replace")


# --- the sandbox ------------------------------------------------------------------

@dataclass
class Sandbox:
    root: Path
    env: dict[str, str]
    toolchain: Toolchain
    transcript: Transcript
    find_links: list[str] = field(default_factory=list)
    commits: int = 0

    @property
    def home(self) -> Path:
        return Path(self.env["HOME"])

    @property
    def tmp(self) -> Path:
        return Path(self.env["TMP"])

    def path_dirs(self) -> list[str]:
        return self.env["PATH"].split(os.pathsep)

    def prepend_path(self, directory: str | os.PathLike) -> None:
        self.env["PATH"] = os.pathsep.join([str(directory), *self.path_dirs()])

    def which(self, name: str) -> str | None:
        return shutil.which(name, path=self.env["PATH"])

    def resolve(self, program: str) -> str:
        """argv[0] as the sandbox PATH finds it. Windows CreateProcess would
        search the runner's PATH instead, so the kit resolves it first."""
        if os.path.dirname(program):
            return program
        found = self.which(program)
        if found is None:
            raise AssertionError(f"{program!r} is not on the sandbox PATH: {self.env['PATH']}")
        return found

    def commit_env(self) -> dict[str, str]:
        """Author and committer dates one minute apart, from the session start."""
        self.commits += 1
        stamp = (SESSION_START + timedelta(minutes=self.commits)).isoformat()
        return {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}

    def run(self, argv: list[str], *, cwd=None, env: dict | None = None, input: str | None = None,
            expect: int | None = None, note: str = "") -> Step:
        """Run one command under the bound, record it, and assert its exit code
        when `expect` names one."""
        where = str(cwd or self.root)
        started = time.monotonic()
        done = self._spawn([str(arg) for arg in argv], where, env, input)
        step = self.transcript.add(Step([str(arg) for arg in argv], where, done.returncode, decode(done.stdout),
                                        decode(done.stderr), round(time.monotonic() - started, 2), note))
        self._check(step, expect)
        return step

    def _spawn(self, argv: list[str], where: str, env: dict | None, input: str | None):
        merged = {**self.env, **(env or {})}
        data = None if input is None else input.encode("utf-8")
        return hang_guard.run([self.resolve(argv[0]), *argv[1:]], input=data, cwd=where, env=merged)

    def _check(self, step: Step, expect: int | None) -> None:
        if expect is not None and step.exit != expect:
            raise AssertionError(f"expected exit {expect}\n{self.transcript.text()}")

    def script(self, text: str, *, shell: str | None = None, cwd=None, env: dict | None = None,
               expect: int | None = None, note: str = "") -> Step:
        """Run a block of shell text as a user pastes it: sh on POSIX, cmd on
        Windows unless `shell` names powershell, pwsh, bash or sh."""
        shell = shell or ("cmd" if WINDOWS else "sh")
        path = self.tmp / f"step-{len(self.transcript.steps)}{SCRIPT_SUFFIX[shell]}"
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        return self.run([*self.shell(shell), str(path)], cwd=cwd, env=env, expect=expect,
                        note=note or f"{shell} script:\n{text}")

    def shell(self, name: str) -> list[str]:
        """How to start `name`. On Windows sh and bash are the pinned Git Bash:
        System32 can hold a WSL bash.exe that would otherwise answer first."""
        if WINDOWS and name in ("sh", "bash"):
            return [self.toolchain["bash"]]
        return SCRIPT_RUNNER[name]


SCRIPT_SUFFIX = {"cmd": ".cmd", "powershell": ".ps1", "pwsh": ".ps1", "bash": ".sh", "sh": ".sh"}
SCRIPT_RUNNER = {"cmd": ["cmd.exe", "/d", "/c"], "bash": ["bash"], "sh": ["sh"],
                 "powershell": ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"],
                 "pwsh": ["pwsh", "-NoLogo", "-NoProfile", "-File"]}


# --- building one ------------------------------------------------------------------

def _redirected(root: Path) -> dict[str, str]:
    env = {name: str(root / relative) for name, relative in REDIRECTED.items()}
    if WINDOWS:
        home = Path(env["USERPROFILE"])
        env.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):])
    return env


def _os_minimum() -> dict[str, str]:
    return {name: os.environ[name] for name in OS_MINIMUM[os.name] if name in os.environ}


def _toolchain_env(toolchain: Toolchain) -> dict[str, str]:
    """The pinned tools, plus a fixed locale and zone where POSIX reads them.
    Windows keeps its own code page: the cp1252 console is a user's reality."""
    env = {"PATH": os.pathsep.join(toolchain["path"]), "UV_PYTHON_INSTALL_DIR": toolchain["python_install_dir"],
           "UV_PYTHON_DOWNLOADS": "never", "npm_config_cache": toolchain["npm_cache"]}
    return env if WINDOWS else {**env, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}


def _offline_env(root: Path, find_links: list[str]) -> dict[str, str]:
    return {"PIP_CONFIG_FILE": str(root / "pip.conf"), "UV_OFFLINE": "1", "UV_NO_INDEX": "1",
            "UV_FIND_LINKS": ",".join(find_links)}


def build_env(root: Path, toolchain: Toolchain, find_links: list[str]) -> dict[str, str]:
    env = {**_os_minimum(), **_toolchain_env(toolchain), **_redirected(root), **QUIET,
           **_offline_env(root, find_links)}
    return {key: _encoded(value) for key, value in env.items() if value is not None}


def pip_conf(find_links: list[str]) -> str:
    """One find-links entry per line, each a file URL so a space in a path
    survives pip's whitespace split."""
    links = "".join(f"    {Path(link).resolve().as_uri()}\n" for link in find_links)
    return f"[global]\nno-index = true\ndisable-pip-version-check = true\nfind-links =\n{links}"


def gitconfig(rules: str = "") -> str:
    name, email = GIT_IDENTITY
    return (f"[user]\n\tname = {name}\n\temail = {email}\n[init]\n\tdefaultBranch = main\n"
            "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n"
            "[protocol \"file\"]\n\tallow = always\n" + rules)


def _write_home(home: Path, find_links: list[str], root: Path) -> None:
    (root / "pip.conf").write_text(pip_conf(find_links), encoding="utf-8")
    (home / ".gitconfig").write_text(gitconfig(), encoding="utf-8")
    for relative, content in QUIET_FILES.items():
        target = home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
    (home / ".codex" / "config.toml").write_text(CODEX_CONFIG, encoding="utf-8")


def make(base: Path, transcript: Transcript, *, toolchain: Toolchain | None = None,
         find_links: list[str] | None = None, home_name: str = "home") -> Sandbox:
    """A fresh sandbox rooted at base/<home_name>'s parent. `home_name` is the
    last component of HOME, for the odd-path cells ("O'Brien Jérôme test")."""
    toolchain = toolchain or Toolchain.load()
    root = long_path(Path(base).resolve())
    links = list(find_links if find_links is not None else default_find_links(toolchain))
    env = _renamed_home(build_env(root, toolchain, links), root, home_name)
    for key in REDIRECTED:
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    _write_home(Path(env["HOME"]), links, root)
    return Sandbox(root, env, toolchain, transcript, links)


def _renamed_home(env: dict[str, str], root: Path, home_name: str) -> dict[str, str]:
    default = str(root / "home")
    return {key: value.replace(default, str(root / home_name)) for key, value in env.items()}


def default_find_links(toolchain: Toolchain) -> list[str]:
    """The wheelhouse, then the candidate's dist/ when a run built one."""
    links = [toolchain["wheelhouse"]]
    candidate = os.environ.get("CRAPKIT_DEPLOY_CANDIDATE")
    return links + ([str(Path(candidate) / "dist")] if candidate else [])


def runner_dirs() -> set[str]:
    """Directories that belong to the runner's own interpreter."""
    return {os.path.normcase(os.path.dirname(sys.executable)), os.path.normcase(sys.prefix)}
