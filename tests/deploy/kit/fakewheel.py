"""A released wheel repacked under another version number.

lin-up-lizard-only asks what a user sees when lizard alone moves: the metric
stamp names lizard's version, so a lizard upgrade with no crapkit upgrade must
refuse the marks. No lizard 1.24.1 exists, so this repacks 1.24.0 as 1.24.1:
the dist-info directory, METADATA's Version, lizard_ext/version.py (what
`lizard.version` reads) and a RECORD rehashed to match. The repack lands in a
find-links directory of its own that only that cell passes to pip.

    links = fakewheel.lizard(box.toolchain["wheelhouse"], box.root / "fake-links", "1.24.1")
"""
from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

VERSION_FILE = "lizard_ext/version.py"


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return f"{name},sha256={digest},{len(data)}"


def _renamed(name: str, project: str, old: str, new: str) -> str:
    prefix = f"{project}-{old}.dist-info/"
    return f"{project}-{new}.dist-info/" + name[len(prefix):] if name.startswith(prefix) else name


def _restamped(name: str, data: bytes, old: str, new: str) -> bytes:
    """METADATA's Version line and the module's version string; every other file as it was."""
    if name.endswith(".dist-info/METADATA"):
        return data.replace(f"\nVersion: {old}\n".encode(), f"\nVersion: {new}\n".encode(), 1)
    if name == VERSION_FILE:
        return data.replace(f'"{old}"'.encode(), f'"{new}"'.encode())
    return data


def _entries(source: Path, project: str, old: str, new: str) -> dict[str, bytes]:
    with zipfile.ZipFile(source) as wheel:
        return {_renamed(name, project, old, new): _restamped(name, wheel.read(name), old, new)
                for name in wheel.namelist() if not name.endswith(".dist-info/RECORD")}


def repack(source: Path, dest: Path, project: str, old: str, new: str) -> Path:
    """`source` (project-old-*.whl) written into `dest` as project-new-*.whl."""
    entries = _entries(Path(source), project, old, new)
    record = f"{project}-{new}.dist-info/RECORD"
    lines = [_record_line(name, data) for name, data in entries.items()] + [f"{record},,"]
    entries[record] = ("\n".join(lines) + "\n").encode("utf-8")
    dest.mkdir(parents=True, exist_ok=True)
    wheel = dest / Path(source).name.replace(f"{project}-{old}-", f"{project}-{new}-")
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return wheel


def lizard(wheelhouse: str | Path, dest: Path, new: str, old: str = "1.24.0") -> Path:
    """The wheelhouse's lizard `old` repacked as `new` into `dest`; returns `dest`."""
    source = next(Path(wheelhouse).glob(f"lizard-{old}-*.whl"))
    repack(source, Path(dest), "lizard", old, new)
    return Path(dest)
