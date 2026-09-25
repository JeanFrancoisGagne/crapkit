"""Install the deploy toolchain natively, from the same pins the images use.

Linux cells run in the images; Windows and macOS cells cannot, and
lin-native-start runs on the bare ubuntu runner, so this puts the same pinned
tools in a cache directory and writes the toolchain.json the kit reads:

    python tools/deploy/toolchain.py [--root DIR] [--harness core|full|none]

  uv, and every CPython in pins.toml, under <root>/python (uv checks hashes)
  Node; pwsh on Windows and macOS; prek on Windows and Linux; on Windows also
  PortableGit (first on the sandbox PATH; its system gitconfig applies, as it
  does for a user). A Linux run (the bare ubuntu runner of lin-native-start)
  keeps the runner's own git and /usr/bin/python3, as a user's machine does
  pipx (the pinned zipapp)
  each pinned harness binary for this OS whose image the --harness level
  holds (the Cursor agent at core, Goose at full on Windows)
  the wheelhouse rows for this OS, fetched from wheelhouse.lock by sha256
  the runner venv from runner-requirements.txt
  `npm ci` of the npm fixtures and the harness locks, into <root>/npm-cache

The root is %LOCALAPPDATA%\\crapkit-deploy on Windows,
~/Library/Caches/crapkit-deploy on macOS and ~/.cache/crapkit-deploy on
Linux, or $CRAPKIT_DEPLOY_TOOLCHAIN_ROOT, which `run.py --native` reads as
well. Every path is resolved before a sandbox rewrites LOCALAPPDATA, and each
step is skipped when its output already exists, so a warm rerun costs seconds.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
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
BASE_TOOLS = {"windows": ("uv", "node", "portable-git", "pwsh", "prek"), "macos": ("uv", "node", "pwsh"),
              "linux": ("uv", "node", "prek")}
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

def download(spec: dict, dest: Path, opener=lock.urlopen) -> Path:
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


def run_step(argv: list, **kwargs) -> subprocess.CompletedProcess:
    """One install command, quiet when it works. When it fails, the command and
    the end of what it printed, which a bare CalledProcessError leaves out."""
    done = subprocess.run([str(part) for part in argv], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kwargs)
    if done.returncode != 0:
        said = (done.stdout + done.stderr).strip().splitlines()[-20:]
        raise SystemExit("\n".join([f"toolchain: {' '.join(map(str, argv))} exited {done.returncode}", *said]))
    return done


def _once(target: Path, make, pin: str = "") -> Path:
    """Make target once per pin. Each step records what it was made from (a
    sha256, a lock's digest) in <target>.pin, so a changed pin, or a step that
    stopped halfway, removes the old output and makes it again, and a rerun
    with nothing changed costs nothing."""
    stamp = target.with_name(target.name + ".pin")
    if target.exists() and _read_stamp(stamp) == pin:
        return target
    _remove(target)
    make()
    stamp.write_text(pin, encoding="utf-8")
    return target


def _read_stamp(stamp: Path) -> str:
    return stamp.read_text(encoding="utf-8") if stamp.exists() else ""


def _writable(func, path, _error) -> None:
    """Clear the read-only bit Windows sets on git packs and some npm files, then retry."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


RMTREE_HANDLER = {"onexc": _writable} if sys.version_info >= (3, 12) else {"onerror": _writable}


def _remove(target: Path) -> None:
    if target.is_dir():
        shutil.rmtree(target, **RMTREE_HANDLER)
    elif target.exists():
        target.unlink()


def digest(*paths: Path) -> str:
    """One sha256 over the bytes of each file, in the order given."""
    total = hashlib.sha256()
    for path in paths:
        total.update(path.read_bytes())
    return total.hexdigest()


# --- steps -------------------------------------------------------------------------

def binary(pins: dict, stem: str, os_name: str, arch: str | None = None) -> dict:
    """The first pin whose key starts with stem for this OS, and for this
    architecture when one is named: Linux pins prek for x86_64 and aarch64."""
    key = next(key for key, spec in pinsfile.binaries(pins, os_name).items()
               if key.startswith(stem) and arch in (None, spec["arch"], "any"))
    return pins["binary"][key]


def install_archive(pins: dict, stem: str, os_name: str, root: Path, arch: str | None = None) -> Path:
    """Download and unpack one pinned archive into <root>/<stem>, again when its sha256 moves."""
    target, spec = root / stem, binary(pins, stem, os_name, arch)

    def make():
        archive = download(spec, root / "downloads")
        if archive.name.endswith(".7z.exe"):
            run_step([archive, "-y", f"-o{target}"])
        else:
            unpack(archive, target)
    return only_child(_once(target, make, spec["sha256"]))


def exe(name: str) -> str:
    return name + ".exe" if WINDOWS else name


def install_pythons(pins: dict, uv: Path, root: Path) -> dict[str, str]:
    versions = [pins["python"]["old"], *pins["python"]["versions"]]
    env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(root / "python"), UV_PYTHON_DOWNLOADS="automatic")
    run_step([uv, "python", "install", *versions], env=env)
    found = {}
    for version in versions:
        found[".".join(version.split(".")[:2])] = run_step([uv, "python", "find", version], env=env).stdout.strip()
    return found


def install_pipx(pins: dict, python: str, root: Path) -> Path:
    pyz = download(pins["binary"]["pipx-pyz"], root / "bin")
    launcher = root / "bin" / ("pipx.cmd" if WINDOWS else "pipx")
    body = f'@"{python}" "{pyz}" %*\r\n' if WINDOWS else f'#!/bin/sh\nexec "{python}" "{pyz}" "$@"\n'
    launcher.write_text(body, encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def runner_pin(python: str) -> str:
    return digest(DOCKER / "runner-requirements.txt") + " " + python


def install_runner(uv: Path, python: str, root: Path) -> str:
    """The runner venv, made again when its requirements or its Python move."""
    venv = root / "runner"
    runner = venv / ("Scripts/python.exe" if WINDOWS else "bin/python")

    def make():
        subprocess.run([str(uv), "venv", "-q", "--python", python, str(venv)], check=True)
        subprocess.run([str(uv), "pip", "install", "-q", "--require-hashes", "--python", str(runner), "-r",
                        str(DOCKER / "runner-requirements.txt")], check=True)
    _once(venv, make, runner_pin(python))
    return str(runner)


def readme_install_lines(pinned: dict[str, str]) -> list[list[str]]:
    """The `npm i -D` arguments the npm-fixtures stage runs, in its order: the
    pinned vitest alone, with --legacy-peer-deps (npm 10.9.9 crashed resolving
    the optional peers of vitest 5.0.1 once 5.0.2 was out), then the README's
    and docs/lanes.md's provider lines as a user runs them, then jest."""
    vitest = pinned["vitest"]
    return [["--legacy-peer-deps", "vitest@" + vitest], ["@vitest/coverage-v8@" + vitest.split(".")[0]],
            ["@vitest/coverage-v8"], ["jest@" + pinned["jest"], "jest-junit", "husky"]]


def cache_readme_installs(npm: str, root: Path, cache: Path, env: dict) -> None:
    """The README's `npm i -D` lines, run once in throwaway repos so the packuments
    an offline install reads are in the cache (the Dockerfile's npm-fixtures stage)."""
    fixtures = json.loads((DOCKER / "npm-fixtures" / "package.json").read_text(encoding="utf-8"))
    scratch = root / "readme-installs"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    (scratch / "package.json").write_text('{"name": "scratch", "private": true}\n', encoding="utf-8")
    for packages in readme_install_lines(fixtures["devDependencies"]):
        run_step([npm, "i", "-D", "--ignore-scripts", f"--cache={cache}", *packages], cwd=scratch, env=env)
    shutil.rmtree(scratch)


HOME_VARS = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
             "XDG_CACHE_HOME")


def npm_env(env: dict, root: Path) -> dict:
    """env with every home directory under <root>/npm-home: harness postinstalls
    write caches and whole releases (Junie's 180 MB) under $HOME, and a toolchain
    install must not fill the user's own."""
    home = root / "npm-home"
    home.mkdir(parents=True, exist_ok=True)
    return {**env, **{name: str(home) for name in HOME_VARS}}


def npm_pin(source: Path) -> str:
    """What an npm set is made from: its package.json and package-lock.json."""
    return digest(*sorted(source.glob("*.json")))


def _npm_ci_into(npm: str, source: Path, dest: Path, cache: Path, env: dict) -> None:
    shutil.copytree(source, dest)
    run_step([npm, "ci", "--no-audit", "--no-fund", f"--cache={cache}"], cwd=dest, env=npm_env(env, cache.parent))


def npm_ci(npm: str, source: Path, dest: Path, cache: Path, env: dict) -> Path:
    """`npm ci` of one locked package set into dest, filling the shared cache,
    again whenever its package files change."""
    return _once(dest, lambda: _npm_ci_into(npm, source, dest, cache, env), npm_pin(source))


def npm_fixture_pin() -> str:
    return npm_pin(DOCKER / "npm-fixtures")


def npm_fixtures(npm: str, root: Path, env: dict) -> Path:
    """The README's `npm i -D` lines into the cache, then the fixture lock into
    <root>/npm-fixtures, both again whenever the fixture files change."""
    source, dest, cache = DOCKER / "npm-fixtures", root / "npm-fixtures", root / "npm-cache"

    def make():
        cache_readme_installs(npm, root, cache, env)
        _npm_ci_into(npm, source, dest, cache, env)
    return _once(dest, make, npm_fixture_pin())


def native_less_commands(pins: dict) -> set[str]:
    return {spec["command"] for spec in pins["harness"].values() if spec.get("native", True) is False}


def drop_native_less_launchers(pins: dict, harness_dir: Path) -> list[Path]:
    """Remove the npm launchers of harnesses marked `native = false`, so the kit
    finds none rather than one that cannot start in a sandbox."""
    commands = native_less_commands(pins)
    launchers = [path for path in (harness_dir / "node_modules" / ".bin").glob("*") if path.stem in commands]
    for path in launchers:
        path.unlink()
    return launchers


# --- toolchain.json ---------------------------------------------------------------

def system_path() -> list[str]:
    if not WINDOWS:
        return ["/usr/bin", "/bin"]
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    return [str(Path(windir) / sub) if sub else windir for sub in SYSTEM_DIRS]


def describe(root: Path, tools: dict, pythons: dict, harness: list[str]) -> dict:
    """toolchain.json: the same keys the images' /opt/deploy/toolchain.json holds.
    The system Python is the OS's own where a user meets one first (Linux's
    /usr/bin/python3), else the pinned 3.12."""
    runner_python = pythons["3.12"]
    path = [*tools["path"], str(Path(runner_python).parent), *system_path()]
    return {"os": host()[0], "path": path, "git": tools["git"], "bash": tools["bash"], "uv": tools["uv"], "uvx": tools["uvx"],
            "node": tools["node"], "npm": tools["npm"], "pipx": tools["pipx"], "prek": tools["prek"],
            "pythons": pythons, "python_install_dir": str(root / "python"),
            "system_python": tools.get("system_python") or runner_python,
            "wheelhouse": str(root / "wheelhouse"), "npm_cache": str(root / "npm-cache"),
            "npm_fixtures": str(root / "npm-fixtures"), "runner_python": tools["runner"],
            "harness_bin": [*(str(root / f"harness-{name}" / "node_modules" / ".bin") for name in harness),
                            *tools.get("harness_dirs", [])]}


def base_tools(pins: dict, os_name: str, root: Path, arch: str) -> dict[str, Path]:
    """stem -> the unpacked directory of each base tool this OS installs."""
    return {stem: install_archive(pins, f"{stem}-{os_name}", os_name, root, arch) for stem in BASE_TOOLS[os_name]}


def harness_downloads(pins: dict, os_name: str, harness: list[str], arch: str | None = None) -> list[str]:
    """The pinned harness binaries for this OS (and architecture, when named)
    that the chosen images hold."""
    return sorted(key for key, spec in pinsfile.binaries(pins, os_name).items()
                  if spec.get("image") in harness and arch in (None, spec["arch"]))


def harness_spec(pins: dict, key: str) -> dict:
    """The [harness] entry a download belongs to (cursor-agent-windows-x64 ->
    cursor-agent), or {} for a tool a harness needs (Bun, for omp)."""
    return next((spec for name, spec in pins["harness"].items() if key.startswith(name + "-")), {})


def alias_launchers(directory: Path, spec: dict) -> list[Path]:
    """Copy each launcher named after the harness command under each alias, as
    Cursor's installers copy cursor-agent.cmd to agent.cmd."""
    launchers = [path for path in directory.iterdir() if path.stem == spec.get("command")]
    return [Path(shutil.copy2(launcher, directory / (alias + launcher.suffix)))
            for alias in spec.get("aliases", []) for launcher in launchers]


def install_harness_binaries(pins: dict, os_name: str, root: Path, harness: list[str],
                             arch: str | None = None) -> list[str]:
    """Each harness binary unpacked once; the directories that go on harness_bin."""
    directories = []
    for key in harness_downloads(pins, os_name, harness, arch):
        directory = install_archive(pins, key, os_name, root)
        alias_launchers(directory, harness_spec(pins, key))
        directories.append(str(directory))
    return directories


def _windows_tools(pins: dict, root: Path, arch: str) -> dict:
    found = base_tools(pins, "windows", root, arch)
    uv, node, git, pwsh, prek = (found[stem] for stem in BASE_TOOLS["windows"])
    return {"uv": str(uv / "uv.exe"), "uvx": str(uv / "uvx.exe"), "node": str(node / "node.exe"),
            "npm": str(node / "npm.cmd"), "git": str(git / "cmd" / "git.exe"), "prek": str(prek / "prek.exe"),
            "bash": str(git / "bin" / "bash.exe"),
            "path": [str(git / "cmd"), str(root / "bin"), str(uv), str(node), str(pwsh)]}


# What a POSIX system brings itself: its git, and on Linux the python3 a user
# meets first (Debian's and Ubuntu's refuse `pip install`, PEP 668).
SYSTEM_PYTHON = {"linux": "/usr/bin/python3", "macos": ""}


def _found(found: dict[str, Path], stem: str, name: str = "") -> list[str]:
    """[the tool's directory, or the file `name` in it] when this OS installs it, else []."""
    return [str(found[stem] / name if name else found[stem])] if stem in found else []


def _posix_tools(pins: dict, root: Path, os_name: str, arch: str) -> dict:
    found = base_tools(pins, os_name, root, arch)
    uv, node = found["uv"], found["node"] / "bin"
    return {"uv": str(uv / "uv"), "uvx": str(uv / "uvx"), "node": str(node / "node"), "npm": str(node / "npm"),
            "git": shutil.which("git") or "/usr/bin/git", "prek": "".join(_found(found, "prek", "prek")),
            "bash": "/bin/bash", "system_python": SYSTEM_PYTHON[os_name],
            "path": [str(root / "bin"), str(uv), str(node), *_found(found, "pwsh")]}


def install(pins: dict, root: Path, harness: list[str]) -> dict:
    os_name, arch = host()
    (root / "bin").mkdir(parents=True, exist_ok=True)
    tools = _windows_tools(pins, root, arch) if WINDOWS else _posix_tools(pins, root, os_name, arch)
    pythons = install_pythons(pins, Path(tools["uv"]), root)
    tools["pipx"] = str(install_pipx(pins, pythons["3.12"], root))
    tools["runner"] = install_runner(Path(tools["uv"]), pythons["3.12"], root)
    tools["harness_dirs"] = install_harness_binaries(pins, os_name, root, harness, arch)
    lock.fetch(lock.read(), lock.row_names(pins, os_name, arch), root / "wheelhouse")
    env = dict(os.environ, PATH=os.pathsep.join([str(Path(tools["node"]).parent), os.environ["PATH"]]))
    npm_fixtures(tools["npm"], root, env)
    for name in harness:
        npm_ci(tools["npm"], DOCKER / f"harness-{name}", root / f"harness-{name}", root / "npm-cache", env)
        drop_native_less_launchers(pins, root / f"harness-{name}")
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
