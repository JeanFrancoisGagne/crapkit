"""Find a pinned oracle and check that it is the version tools/accuracy/pins.toml names.

locate() answers what is installed, or raises OracleMissing with the command
that installs it. require() is what a test calls, through the `oracle`
fixture: a missing oracle fails the test and notes an infra miss for run.py; a
version that differs from its pin fails in the nightly and release tiers
("oracle radon is 6.0.2, pins.toml says 6.0.1") and warns on push, where a
developer's machine may hold another patch release.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import tomllib
import warnings

import pytest

import hang_guard
from . import runlog, tiers

REPO = Path(__file__).resolve().parents[3]
PINS = REPO / "tools" / "accuracy" / "pins.toml"
NODE_ROOT_ENV = "CRAPKIT_ACCURACY_NODE_ROOT"
KINDS = ("python", "node", "binary", "producer")
STRICT_TIERS = ("nightly", "release")


class OracleMissing(LookupError):
    """The oracle is not installed where the kit looks for it."""


class OracleDriftWarning(UserWarning):
    """An installed oracle differs from its pin on a push run."""


@dataclass(frozen=True)
class Pin:
    name: str
    kind: str
    tier: str
    version: str
    url: str
    sha256: str
    version_line: str
    package: str = ""
    command: tuple[str, ...] = ()
    check: str = "version"
    path: str = ""
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Found:
    name: str
    version: str
    where: str


_FIELDS = {"kind", "tier", "version", "url", "sha256", "version_line", "package", "command",
           "check", "path"}


def _pin(name: str, table: dict) -> Pin:
    known = {key: value for key, value in table.items() if key in _FIELDS}
    known["command"] = tuple(known.get("command", ()))
    extra = {key: value for key, value in table.items() if key not in _FIELDS}
    return Pin(name=name, extra=extra, **known)


def load_pins(path: Path = PINS) -> dict[str, Pin]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {name: _pin(name, table) for name, table in data["oracle"].items()}


def _python(pin: Pin) -> Found:
    try:
        version = importlib.metadata.version(pin.package)
    except importlib.metadata.PackageNotFoundError:
        raise OracleMissing(f"oracle {pin.name} {pin.version} is not installed; run: pip install "
                            f"--require-hashes --no-deps -r tools/accuracy/requirements-{pin.tier}"
                            ".txt") from None
    return Found(pin.name, version, pin.package)


def node_modules(tier: str) -> Path:
    root = os.environ.get(NODE_ROOT_ENV)
    base = Path(root) if root else REPO / "tools" / "accuracy" / "node"
    return base / tier / "node_modules"


def _node(pin: Pin) -> Found:
    manifest = node_modules(pin.tier) / pin.package / "package.json"
    if not manifest.is_file():
        raise OracleMissing(f"oracle {pin.name} {pin.version} is not installed; run: "
                            f"npm ci --prefix tools/accuracy/node/{pin.tier}")
    version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    return Found(pin.name, version, str(manifest))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ask(pin: Pin, binary: str) -> str:
    tiers.require_process(pin.name)
    argv = [part.replace("{bin}", binary) for part in pin.command]
    done = hang_guard.run(argv, text=True, encoding="utf-8", errors="replace")
    return done.stdout + done.stderr


def _answer(pin: Pin, binary: str) -> str:
    """The version the binary gives: the pinned line when its answer holds it,
    else the answer's first line, which the drift message then quotes. A binary
    pinned by digest answers with its file's hash instead."""
    if pin.check == "sha256":
        return pin.version_line if _sha256(Path(pin.path)) == pin.sha256 else "another build"
    said = _ask(pin, binary)
    return pin.version_line if pin.version_line in said else (said.strip().splitlines() or [""])[0]


def _binary(pin: Pin) -> Found:
    binary = shutil.which(pin.command[0]) if pin.command else None
    if binary is None:
        raise OracleMissing(f"oracle {pin.name} {pin.version} is not on PATH; it ships in the "
                            "accuracy image built from tools/accuracy/image/Dockerfile")
    return Found(pin.name, _answer(pin, binary), binary)


def _producer(pin: Pin) -> Found:
    raise OracleMissing(f"{pin.name} is a recorded producer: tests replay what it wrote, and "
                        "only a regeneration runs it")


_LOCATE = {"python": _python, "node": _node, "binary": _binary, "producer": _producer}


def locate(name: str, pins: dict[str, Pin] | None = None) -> Found:
    table = load_pins() if pins is None else pins
    if name not in table:
        raise OracleMissing(f"no pin named {name} in tools/accuracy/pins.toml")
    pin = table[name]
    return _LOCATE[pin.kind](pin)


def drift(found: Found, pin: Pin) -> str | None:
    if found.version == pin.version_line:
        return None
    return f"oracle {pin.name} is {found.version}, pins.toml says {pin.version_line}"


def _report(message: str, tier: str) -> None:
    if tier in STRICT_TIERS:
        pytest.fail(message, pytrace=False)
    warnings.warn(OracleDriftWarning(message), stacklevel=3)


def require(name: str, tier: str, pins: dict[str, Pin] | None = None) -> Found:
    """The oracle a test reads, or the test fails saying how to install it."""
    table = load_pins() if pins is None else pins
    try:
        found = locate(name, table)
    except OracleMissing as missing:
        runlog.note("infra", message=str(missing))
        pytest.fail(str(missing), pytrace=False)
    message = drift(found, table[name])
    if message:
        _report(message, tier)
    runlog.note("oracle", name=name, version=found.version)
    return found
