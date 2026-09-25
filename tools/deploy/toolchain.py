"""Install the deploy toolchain natively, from the same pins the images use.

Linux cells run in the images; Windows and macOS cells cannot, so this puts
the same pinned tools in a cache directory and writes the toolchain.json the
kit reads:

    python tools/deploy/toolchain.py [--root DIR] [--harness core|full|none]

  uv, and every CPython in pins.toml, under <root>/python (uv checks hashes)
  Node and pwsh; on Windows also PortableGit (first on the sandbox PATH; its
  system gitconfig applies, as it does for a user) and prek
  pipx (the pinned zipapp)
  each pinned harness binary for this OS whose image the --harness level
  holds (the Cursor agent at core, Goose at full on Windows)
  the wheelhouse rows for this OS, fetched from wheelhouse.lock by sha256
  the runner venv from runner-requirements.txt
  `npm ci` of the npm fixtures and the harness locks, into <root>/npm-cache

The root is %LOCALAPPDATA%\\crapkit-deploy on Windows and
~/Library/Caches/crapkit-deploy on macOS, or $CRAPKIT_DEPLOY_TOOLCHAIN_ROOT,
which `run.py --native` reads as well. Every path is resolved before a
sandbox rewrites LOCALAPPDATA, and each step is skipped when its output
already exists, so a warm rerun costs seconds.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

import lock
import pins as pinsfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOCKER = ROOT / "tests" / "deploy" / "docker"
WINDOWS = os.name == "nt"
# The pinned downloads every native toolchain installs, by pins.toml stem; the
# harness binaries come on top, chosen by their `image`.
BASE_TOOLS = {"windows": ("uv", "node", "portable-git", "pwsh", "prek"), "macos": ("uv", "node", "pwsh")}
HARNESS_LEVELS = {"core": ["core"], "full": ["core", "full"], "none": []}
ROOT_ENV = "CRAPKIT_DEPLOY_TOOLCHAIN_ROOT"
BASETEMP_ENV = "CRAPKIT_DEPLOY_BASETEMP"
SYSTEM_DIRS = ["System32", "", r"System32\Wbem", r"System32\WindowsPowerShell\v1.0"]


# --- where ---------------------------------------------------------------------

def host() -> tuple[str, str]:
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux"), arch


def long_path(path: Path) -> Path:
    if not WINDOWS:
        return path
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetLongPathNameW(str(path), buffer, len(buffer))
    return Path(buffer.value) if size else path


def default_root() -> Path:
    """$CRAPKIT_DEPLOY_TOOLCHAIN_ROOT when set, so two checkouts on one machine
    each keep their own pins, else the OS's cache directory."""
    if os.environ.get(ROOT_ENV):
        return Path(os.environ[ROOT_ENV])
    if WINDOWS:
        return long_path(Path(os.environ["LOCALAPPDATA"])) / "crapkit-deploy"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "crapkit-deploy"
    return Path.home() / ".cache" / "crapkit-deploy"


def basetemp() -> Path:
    """pytest's --basetemp for native runs: C:\\dt on Windows, a path with no
    8.3 component a tool could print back in another spelling, or
    $CRAPKIT_DEPLOY_BASETEMP. pytest empties its basetemp when it starts, so
    two native runs at once each need their own."""
    chosen = os.environ.get(BASETEMP_ENV) or ("C:/dt" if WINDOWS else None)
    if chosen is None:
        return Path(os.environ.get("TMPDIR", "/tmp")) / "crapkit-deploy-tmp"
    base = Path(chosen)
    base.mkdir(parents=True, exist_ok=True)
    return long_path(base.resolve())


# --- downloads -------------------------------------------------------------------

def download(spec: dict, dest: Path, opener=urllib.request.urlopen) -> Path:
    """One pinned binary, kept when its bytes already match, refused when they do not."""
    entry = {"name": spec["url"].rsplit("/", 1)[-1], "url": spec["url"], "sha256": spec["sha256"]}
    return lock.fetch_one(entry, dest, opener)


def unpack(archive: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(dest)
    else:
        with tarfile.open(archive) as bundle:
            bundle.extractall(dest, filter="tar")
    return dest


def only_child(directory: Path) -> Path:
    """An archive's one top-level directory, or the directory itself."""
    children = [child for child in directory.iterdir()]
    return children[0] if len(children) == 1 and children[0].is_dir() else directory


def _once(target: Path, make) -> Path:
    if not target.exists():
        make()
    return target


# --- steps -------------------------------------------------------------------------

def binary(pins: dict, stem: str, os_name: str) -> dict:
    key = next(key for key, spec in pinsfile.binaries(pins, os_name).items()
               if key.startswith(stem) and spec["os"] in (os_name, "any"))
    return pins["binary"][key]


def install_archive(pins: dict, stem: str, os_name: str, root: Path) -> Path:
    """Download and unpack one pinned archive into <root>/<stem>, once."""
    target = root / stem

    def make():
        archive = download(binary(pins, stem, os_name), root / "downloads")
        if archive.name.endswith(".7z.exe"):
            subprocess.run([str(archive), "-y", f"-o{target}"], check=True, capture_output=True)
        else:
            unpack(archive, target)
    return only_child(_once(target, make))


def exe(name: str) -> str:
    return name + ".exe" if WINDOWS else name


def install_pythons(pins: dict, uv: Path, root: Path) -> dict[str, str]:
    versions = [pins["python"]["old"], *pins["python"]["versions"]]
    env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(root / "python"), UV_PYTHON_DOWNLOADS="automatic")
    subprocess.run([str(uv), "python", "install", *versions], check=True, env=env, capture_output=True)
    found = {}
    for version in versions:
        done = subprocess.run([str(uv), "python", "find", version], check=True, env=env, capture_output=True,
                              text=True)
        found[".".join(version.split(".")[:2])] = done.stdout.strip()
    return found


def install_pipx(pins: dict, python: str, root: Path) -> Path:
    pyz = download(pins["binary"]["pipx-pyz"], root / "bin")
    launcher = root / "bin" / ("pipx.cmd" if WINDOWS else "pipx")
    body = f'@"{python}" "{pyz}" %*\r\n' if WINDOWS else f'#!/bin/sh\nexec "{python}" "{pyz}" "$@"\n'
    launcher.write_text(body, encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def install_runner(uv: Path, python: str, root: Path) -> str:
    venv = root / "runner"
    runner = venv / ("Scripts/python.exe" if WINDOWS else "bin/python")

    def make():
        subprocess.run([str(uv), "venv", "-q", "--python", python, str(venv)], check=True)
        subprocess.run([str(uv), "pip", "install", "-q", "--require-hashes", "--python", str(runner), "-r",
                        str(DOCKER / "runner-requirements.txt")], check=True)
    _once(runner, make)
    return str(runner)


def cache_readme_installs(npm: str, root: Path, cache: Path, env: dict) -> None:
    """The README's `npm i -D` lines, run once in throwaway repos so the packuments
    an offline install reads are in the cache (the Dockerfile's npm-fixtures stage)."""
    fixtures = json.loads((DOCKER / "npm-fixtures" / "package.json").read_text(encoding="utf-8"))
    pinned = fixtures["devDependencies"]
    vitest = pinned["vitest"]
    lines = [["vitest@" + vitest], ["@vitest/coverage-v8@" + vitest.split(".")[0]], ["@vitest/coverage-v8"],
             ["jest@" + pinned["jest"], "jest-junit", "husky"]]
    scratch = root / "readme-installs"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    (scratch / "package.json").write_text('{"name": "scratch", "private": true}\n', encoding="utf-8")
    for packages in lines:
        subprocess.run([npm, "i", "-D", "--ignore-scripts", f"--cache={cache}", *packages], cwd=scratch,
                       check=True, env=env, capture_output=True)
    shutil.rmtree(scratch)


def npm_ci(npm: str, source: Path, dest: Path, cache: Path, env: dict) -> Path:
    """`npm ci` of one locked package set into dest, filling the shared cache."""
    def make():
        shutil.copytree(source, dest)
        subprocess.run([npm, "ci", "--no-audit", "--no-fund", f"--cache={cache}"], cwd=dest, check=True,
                       env=env, capture_output=True)
    return _once(dest / "node_modules", make)


# --- toolchain.json ---------------------------------------------------------------

def system_path() -> list[str]:
    if not WINDOWS:
        return ["/usr/bin", "/bin"]
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    return [str(Path(windir) / sub) if sub else windir for sub in SYSTEM_DIRS]


def describe(root: Path, tools: dict, pythons: dict, harness: list[str]) -> dict:
    """toolchain.json: the same keys the images' /opt/deploy/toolchain.json holds."""
    runner_python = pythons["3.12"]
    path = [*tools["path"], str(Path(runner_python).parent), *system_path()]
    return {"os": host()[0], "path": path, "git": tools["git"], "bash": tools["bash"], "uv": tools["uv"], "uvx": tools["uvx"],
            "node": tools["node"], "npm": tools["npm"], "pipx": tools["pipx"], "prek": tools["prek"],
            "pythons": pythons, "python_install_dir": str(root / "python"), "system_python": runner_python,
            "wheelhouse": str(root / "wheelhouse"), "npm_cache": str(root / "npm-cache"),
            "npm_fixtures": str(root / "npm-fixtures"), "runner_python": tools["runner"],
            "harness_bin": [*(str(root / f"harness-{name}" / "node_modules" / ".bin") for name in harness),
                            *tools.get("harness_dirs", [])]}


def base_tools(pins: dict, os_name: str, root: Path) -> dict[str, Path]:
    """stem -> the unpacked directory of each base tool this OS installs."""
    return {stem: install_archive(pins, f"{stem}-{os_name}", os_name, root) for stem in BASE_TOOLS[os_name]}


def harness_downloads(pins: dict, os_name: str, harness: list[str]) -> list[str]:
    """The pinned harness binaries for this OS that the chosen images hold."""
    return sorted(key for key, spec in pinsfile.binaries(pins, os_name).items() if spec.get("image") in harness)


def harness_spec(pins: dict, key: str) -> dict:
    """The [harness] entry a download belongs to: cursor-agent-windows-x64 -> cursor-agent."""
    return next(spec for name, spec in pins["harness"].items() if key.startswith(name + "-"))


def alias_launchers(directory: Path, spec: dict) -> list[Path]:
    """Copy each launcher named after the harness command under each alias, as
    Cursor's installers copy cursor-agent.cmd to agent.cmd."""
    launchers = [path for path in directory.iterdir() if path.stem == spec["command"]]
    return [Path(shutil.copy2(launcher, directory / (alias + launcher.suffix)))
            for alias in spec.get("aliases", []) for launcher in launchers]


def install_harness_binaries(pins: dict, os_name: str, root: Path, harness: list[str]) -> list[str]:
    """Each harness binary unpacked once; the directories that go on harness_bin."""
    directories = []
    for key in harness_downloads(pins, os_name, harness):
        directory = install_archive(pins, key, os_name, root)
        alias_launchers(directory, harness_spec(pins, key))
        directories.append(str(directory))
    return directories


def _windows_tools(pins: dict, root: Path) -> dict:
    found = base_tools(pins, "windows", root)
    uv, node, git, pwsh, prek = (found[stem] for stem in BASE_TOOLS["windows"])
    return {"uv": str(uv / "uv.exe"), "uvx": str(uv / "uvx.exe"), "node": str(node / "node.exe"),
            "npm": str(node / "npm.cmd"), "git": str(git / "cmd" / "git.exe"), "prek": str(prek / "prek.exe"),
            "bash": str(git / "bin" / "bash.exe"),
            "path": [str(git / "cmd"), str(root / "bin"), str(uv), str(node), str(pwsh)]}


def _posix_tools(pins: dict, root: Path, os_name: str) -> dict:
    found = base_tools(pins, os_name, root)
    uv, node, pwsh = found["uv"], found["node"] / "bin", found["pwsh"]
    return {"uv": str(uv / "uv"), "uvx": str(uv / "uvx"), "node": str(node / "node"), "npm": str(node / "npm"),
            "git": shutil.which("git") or "/usr/bin/git", "prek": "", "bash": "/bin/bash",
            "path": [str(root / "bin"), str(uv), str(node), str(pwsh)]}


def install(pins: dict, root: Path, harness: list[str]) -> dict:
    os_name, arch = host()
    (root / "bin").mkdir(parents=True, exist_ok=True)
    tools = _windows_tools(pins, root) if WINDOWS else _posix_tools(pins, root, os_name)
    pythons = install_pythons(pins, Path(tools["uv"]), root)
    tools["pipx"] = str(install_pipx(pins, pythons["3.12"], root))
    tools["runner"] = install_runner(Path(tools["uv"]), pythons["3.12"], root)
    tools["harness_dirs"] = install_harness_binaries(pins, os_name, root, harness)
    lock.fetch(lock.read(), lock.row_names(pins, os_name, arch), root / "wheelhouse")
    env = dict(os.environ, PATH=os.pathsep.join([str(Path(tools["node"]).parent), os.environ["PATH"]]))
    fixtures = root / "npm-fixtures" / "node_modules"
    if not fixtures.exists():
        cache_readme_installs(tools["npm"], root, root / "npm-cache", env)
    npm_ci(tools["npm"], DOCKER / "npm-fixtures", root / "npm-fixtures", root / "npm-cache", env)
    for name in harness:
        npm_ci(tools["npm"], DOCKER / f"harness-{name}", root / f"harness-{name}", root / "npm-cache", env)
    described = describe(root, tools, pythons, harness)
    (root / "toolchain.json").write_text(json.dumps(described, indent=2) + "\n", encoding="utf-8")
    return described


def read(root: Path) -> dict:
    return json.loads((root / "toolchain.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--harness", default="core", choices=sorted(HARNESS_LEVELS))
    args = parser.parse_args(argv)
    root = (args.root or default_root()).resolve()
    install(pinsfile.load(), root, HARNESS_LEVELS[args.harness])
    print(root / "toolchain.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
