"""A `crapkit` launcher that records how a harness starts it.

    shim_bin = shim.install(box, real=box.which("crapkit"))
    box.prepend_path(shim_bin)          # the harness now finds the shim first
    ...
    starts = shim.starts(box)           # one dict per launch: argv, cwd, env, ppid, first_line, lines, exit

The shim is a console script built from shim_pkg/ with the real launcher and
the log folder written into its code, installed by uv into its own venv, and
copied alone into <box>/shim-bin so nothing else from that venv reaches PATH.
On Windows it is a real .exe, as a pip or uv install of crapkit is, so a
harness that resolves PATHEXT or calls CreateProcess finds it the same way.

Each shim process writes its records to its own file,
<box>/shim-log/<start ns>-<pid>.jsonl. One log that every launch appended to
lost and cut records on Windows when two launches wrote at once, as Codex
starts two `crapkit mcp` together, and a reused pid merged two launches.
starts() reads the files in name order, so launches keep their start order.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tomllib
import zipfile
from pathlib import Path

PKG = Path(__file__).resolve().parent / "shim_pkg"
WINDOWS = os.name == "nt"
LOG_DIR = "shim-log"


def _project() -> dict:
    return tomllib.loads((PKG / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return f"{name},sha256={digest},{len(data)}"


def _files(real: str, log_dir: Path) -> dict[str, bytes]:
    project = _project()
    dist = f"{project['name'].replace('-', '_')}-{project['version']}.dist-info"
    scripts = "".join(f"{name} = {target}\n" for name, target in project["scripts"].items())
    return {
        "crapkit_shim/__init__.py": (PKG / "crapkit_shim" / "__init__.py").read_bytes(),
        "crapkit_shim/_config.py": f"REAL = {real!r}\nLOG_DIR = {str(log_dir)!r}\n".encode("utf-8"),
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {project['version']}\n".encode(),
        f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: crapkit-deploy-kit\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{dist}/entry_points.txt": f"[console_scripts]\n{scripts}".encode("utf-8"),
    }


def build_wheel(real: str, log_dir: Path, dest: Path) -> Path:
    """shim_pkg as a wheel, with `real` and `log_dir` written into _config.py."""
    project = _project()
    files = _files(real, log_dir)
    record_name = next(name for name in files if name.endswith("/METADATA")).replace("METADATA", "RECORD")
    record = "\n".join([*(_record_line(name, data) for name, data in files.items()), f"{record_name},,"]) + "\n"
    wheel = dest / f"{project['name'].replace('-', '_')}-{project['version']}-py3-none-any.whl"
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in [*files.items(), (record_name, record.encode("utf-8"))]:
            archive.writestr(name, data)
    return wheel


def launcher_name() -> str:
    return "crapkit.exe" if WINDOWS else "crapkit"


def install(box, real: str, python: str = "3.12") -> Path:
    """Install the shim for `real` and return the directory holding only it."""
    log_dir = box.root / LOG_DIR
    log_dir.mkdir(exist_ok=True)
    venv = box.root / "shim-venv"
    wheel = build_wheel(str(real), log_dir, box.root / "shim-wheel")
    box.run(["uv", "venv", "-q", "--python", box.toolchain.python(python), str(venv)], expect=0)
    box.run(["uv", "pip", "install", "-q", "--no-deps", "--python", str(venv), str(wheel)], expect=0)
    scripts = venv / ("Scripts" if WINDOWS else "bin")
    target = box.root / "shim-bin"
    target.mkdir(exist_ok=True)
    shutil.copy2(scripts / launcher_name(), target / launcher_name())
    return target


def _logs(box) -> list[Path]:
    """Each shim process's record file, in start order: each name leads with its start time."""
    folder = box.root / LOG_DIR
    return sorted(folder.glob("*.jsonl")) if folder.is_dir() else []


def _records(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def events(box) -> list[dict]:
    """Every record, file by file in start order, each file's in the order its process wrote them."""
    return [event for log in _logs(box) for event in _records(log)]


def starts(box) -> list[dict]:
    """One dict per launch, in start order: the start record with first_line,
    the client's lines up to its initialize (`lines`, first_line among them)
    and exit folded in. exit stays None for a server its harness killed. A file
    with no record yet is a launch that has not written its start: a reader
    polling while hooks start (test_claude_hook_stub) can meet one."""
    return [_launch(records) for records in map(_records, _logs(box)) if records]


def _launch(records: list[dict]) -> dict:
    entry = {"first_line": None, "lines": [], "exit": None}
    for event in records:
        entry.update({key: value for key, value in event.items() if key not in ("event", "code", "line")})
        _fold(entry, event)
    return entry


def _fold(entry: dict, event: dict) -> None:
    kind = event["event"]
    if kind in ("first_line", "client_line"):
        entry["lines"].append(event["line"])
    if kind == "first_line":
        entry["first_line"] = event["line"]
    if kind == "exit":
        entry["exit"] = event["code"]
