"""The wheels a cell installs: old releases, N-1 and the candidate.

Versions come from the files, never from a literal in a test: the releases
from tools/deploy/wheelhouse.lock, N-1 as its [newest] crapkit, the candidate
from the candidate.json run.py's candidate step wrote.
"""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

SRC = Path(os.environ.get("CRAPKIT_DEPLOY_SRC") or Path(__file__).resolve().parents[3])


def lock(path: Path | None = None) -> dict:
    path = path or SRC / "tools" / "deploy" / "wheelhouse.lock"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def releases(data: dict | None = None) -> list[str]:
    """Every crapkit release the wheelhouse holds, oldest first."""
    data = data or lock()
    found = {entry["version"] for entry in data["file"] if entry["project"].lower() == "crapkit"}
    return sorted(found, key=_key)


def n_minus_1(data: dict | None = None) -> str:
    """The newest published release: what a user upgrades from to the candidate."""
    return (data or lock())["newest"]["crapkit"]


@dataclass(frozen=True)
class Candidate:
    root: Path
    version: str
    wheel: Path
    sdist: Path
    staged: Path
    record: dict

    @classmethod
    def load(cls, root: str | os.PathLike | None = None) -> "Candidate":
        root = Path(root or os.environ["CRAPKIT_DEPLOY_CANDIDATE"])
        record = json.loads((root / "candidate.json").read_text(encoding="utf-8"))
        return cls(root, record["version"], root / "dist" / record["wheel"],
                   root / "dist" / record["sdist"], root / "staged", record)

    @property
    def dist(self) -> Path:
        return self.wheel.parent


def release_wheel(wheelhouse: str | os.PathLike, version: str) -> Path:
    """The release's wheel file in the wheelhouse."""
    path = Path(wheelhouse) / f"crapkit-{version}-py3-none-any.whl"
    if not path.exists():
        raise AssertionError(f"crapkit {version} is not in the wheelhouse at {wheelhouse}")
    return path
