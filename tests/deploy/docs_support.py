"""What the deploy-docs cells share: crapkit installed the way a page says,
and a repo adopted and measured under it."""
from __future__ import annotations

import os
from pathlib import Path

WINDOWS = os.name == "nt"
CONTAINER_KEY = "container_ok = true"  # docs/lanes.md#containers


def scripts_dir(venv: Path) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin")


def user_bin(box) -> Path:
    """Where pipx and uv tool put a launcher: ~/.local/bin on every OS."""
    return box.home / ".local" / "bin"


def version_of(box, cwd=None) -> str:
    return box.run(["crapkit", "--version"], cwd=cwd, expect=0).stdout.strip()


def in_container() -> bool:
    return Path("/.dockerenv").exists()


def allow_container_lane(config: Path) -> None:
    """The docs/lanes.md#containers key on the lane init wrote."""
    text = config.read_text(encoding="utf-8")
    config.write_text(text.replace("[[lane]]\n", f"[[lane]]\n{CONTAINER_KEY}\n", 1), encoding="utf-8")


def venv_with(box, *specs: str) -> Path:
    """A venv on PATH holding `specs`: crapkit[py] for a pip install, the lane's
    test dependencies beside a pipx or uv tool install."""
    venv = box.root / "venv"
    box.run([box.toolchain.python("3.12"), "-m", "venv", str(venv)], expect=0)
    box.prepend_path(scripts_dir(venv))
    box.run(["python", "-m", "pip", "install", "-q", *specs], expect=0)
    return venv


def install_candidate(box, installer: str) -> None:
    if installer == "pip":
        venv_with(box, "crapkit[py]")
        return
    venv_with(box, "pytest", "pytest-cov")
    box.run([*installer.split(), "install", "crapkit"], expect=0)
    box.prepend_path(user_bin(box))


def commit_all(box, repo: Path, message: str) -> None:
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", message], cwd=repo, env=box.commit_env(), expect=0)


def init(box, repo: Path) -> None:
    """`crapkit init`, plus the containers key when the cell runs in one."""
    box.run(["crapkit", "init"], cwd=repo, expect=0)
    if in_container():
        allow_container_lane(repo / "crapkit.toml")


def adopt_measured(box, repo: Path) -> None:
    """init, coverage and seed, committed: the README's start on `repo`."""
    init(box, repo)
    for step in (["coverage"], ["ratchet", "seed"]):
        box.run(["crapkit", *step], cwd=repo, expect=0)
    commit_all(box, repo, "adopt crapkit")
