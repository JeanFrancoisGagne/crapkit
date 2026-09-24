"""Read tools/deploy/pins.toml: the one source of versions and hashes.

run.py turns it into build args, toolchain.py into native installs, lock.py
into the wheelhouse, and `expected_versions` into what `entry.sh versions`
must print inside a built image.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINS = HERE / "pins.toml"
# Each image holds the tools of the images it is built on.
IMAGE_CHAIN = {"cells": ["cells"], "core": ["cells", "core"], "full": ["cells", "core", "full"],
               "ci": ["cells", "ci"], "gui": ["cells", "core", "full", "gui"]}


def load(path: Path = PINS) -> dict:
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def arg_name(key: str) -> str:
    """binary key -> build-arg stem: cursor-agent-linux-x64 -> CURSOR_AGENT_LINUX_X64."""
    return key.upper().replace("-", "_")


def binaries(pins: dict, os_name: str) -> dict[str, dict]:
    """The downloads a platform installs: its own rows plus the portable ones."""
    return {key: spec for key, spec in pins["binary"].items() if spec["os"] in (os_name, "any")}


def _binary_args(pins: dict) -> dict[str, str]:
    args = {}
    for key, spec in binaries(pins, "linux").items():
        args[f"{arg_name(key)}_URL"] = spec["url"]
        args[f"{arg_name(key)}_SHA256"] = spec["sha256"]
    return args


def build_args(pins: dict) -> dict[str, str]:
    """Every ARG the Dockerfile declares, and nothing else."""
    images, python = pins["images"], pins["python"]
    return {"DEBIAN_IMAGE": images["debian"], "UV_IMAGE": images["uv"], "NODE_IMAGE": images["node"],
            "SNAPSHOT": images["snapshot"], "PYTHONS": " ".join(python["versions"]),
            "PYTHON_OLD": python["old"], "PYTHON_RUNNER": python["runner"],
            "PYTHON_PRERELEASE": "", **_binary_args(pins)}


def _minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def _base_versions(pins: dict) -> dict[str, str]:
    tools, python = pins["toolchain"], pins["python"]
    expected = {"uv": tools["uv"], "node": "v" + tools["node"], "git": tools["git_linux"],
                "pipx": tools["pipx"], "prek": tools["prek"]}
    for version in [python["old"], *python["versions"]]:
        expected["python" + _minor(version)] = version
    return expected


def _harness_versions(pins: dict, image: str) -> dict[str, str]:
    return {spec["command"]: spec["version"] for spec in pins["harness"].values()
            if spec["image"] == image and "command" in spec}


def _binary_versions(pins: dict, image: str) -> dict[str, str]:
    extra = {"full": {"bun": pins["toolchain"]["bun"]},
             "ci": {"act": pins["toolchain"]["act"]}}.get(image, {})
    gui = {key.split("-")[0]: spec["version"] for key, spec in pins["binary"].items()
           if spec.get("image") == image and "version" in spec}
    return {**extra, **gui}


def expected_versions(pins: dict, image: str) -> dict[str, str]:
    """tool name -> the version text `entry.sh versions` must print for it."""
    expected = _base_versions(pins)
    for layer in IMAGE_CHAIN[image]:
        expected.update(_harness_versions(pins, layer))
        expected.update(_binary_versions(pins, layer))
    return expected


def _printed(text: str) -> dict[str, str]:
    lines = (line.split(" ", 1) for line in text.splitlines() if " " in line)
    return {name: rest for name, rest in lines}


def version_problems(expected: dict[str, str], text: str) -> list[str]:
    """Each expected tool's line must carry its pinned version."""
    printed = _printed(text)
    return [f"{name}: pinned {version}, image prints {printed.get(name, 'nothing')!r}"
            for name, version in sorted(expected.items()) if version not in printed.get(name, "")]
