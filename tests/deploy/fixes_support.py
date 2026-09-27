"""What the deploy-fixes cells share: the candidate in a venv the way a user
installs it, a repo adopted and measured, and the harnesses on PATH."""
from __future__ import annotations

import os
from pathlib import Path

from kit import docsnip, repos, wheels

WINDOWS = os.name == "nt"
# docs/lanes.md#containers: where the key a coverage.py lane needs inside a
# container is printed.
CONTAINER_FENCE = ("docs/lanes.md", "Containers")


def in_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("CRAPKIT_INSIDE_CONTAINER") == "1"


def scripts_dir(venv: Path) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin")


def launcher(scripts: Path) -> Path:
    return scripts / ("crapkit.exe" if WINDOWS else "crapkit")


def install_candidate(box, spec: str = "crapkit[py]", python: str = "3.12") -> Path:
    """`spec` in a fresh venv, the way the README's `pip install` puts it
    there, with the venv first on PATH as an activated venv puts it; the
    venv's scripts directory. The sandbox's pip.conf serves the wheelhouse and
    the candidate, so a bare `crapkit` resolves to the candidate."""
    venv = box.root / "venv"
    box.run([box.toolchain.python(python), "-m", "venv", str(venv)], expect=0)
    box.prepend_path(scripts_dir(venv))
    box.run(["python", "-m", "pip", "install", "-q", spec], expect=0)
    assert installed_version(box) == wanted_version(spec), (
        "pip backtracked to another crapkit: the wheelhouse lacks a row this Python needs")
    return scripts_dir(venv)


def wanted_version(spec: str) -> str:
    """The version a spec pins, else the candidate's."""
    return spec.partition("==")[2] or wheels.Candidate.load().version


def installed_version(box) -> str:
    probe = "import importlib.metadata as m; print(m.version('crapkit'))"
    return box.run(["python", "-c", probe], expect=0).stdout.strip()


def container_key() -> str:
    """The toml fence under docs/lanes.md#containers; the fence above it quotes
    the refusal, which names the same key in prose."""
    page, heading = CONTAINER_FENCE
    (fence,) = [block for block in docsnip.fences(page) if block.heading == heading and block.lang == "toml"]
    return fence.text.strip()


def allow_container_lane(repo: Path) -> None:
    """Apply the key docs/lanes.md#containers prints, as a user in a container does."""
    key = container_key()
    assert key == "container_ok = true", key
    config = repo / "crapkit.toml"
    text = config.read_text(encoding="utf-8")
    assert "[[lane]]\n" in text, "init wrote no lane to apply the key to"
    config.write_text(text.replace("[[lane]]\n", f"[[lane]]\n{key}\n", 1), encoding="utf-8")


def measured_repo(box, templates) -> Path:
    """py-pytest adopted and measured by the crapkit on PATH: init, the
    container key where the guard applies, one coverage run."""
    repo = repos.checkout(box, "py-pytest", cache=templates)
    box.run(["crapkit", "init"], cwd=repo, expect=0)
    if in_container():
        allow_container_lane(repo)
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    return repo


def harnesses_on_path(box) -> None:
    for directory in reversed(box.toolchain["harness_bin"]):
        box.prepend_path(directory)


def harness_package(box, package: str) -> Path:
    """An npm alias the image or toolchain.py installed beside a pinned harness."""
    for directory in box.toolchain["harness_bin"]:
        for base in (Path(directory).parent / "node_modules", Path(directory).parent):
            if (base / package / "package.json").exists():
                return base / package
    raise AssertionError(f"{package} is not installed under {box.toolchain['harness_bin']}")
