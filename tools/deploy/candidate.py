"""Build the crapkit candidate a deploy run installs: the tree under test,
stamped with a version newer than every release in the wheelhouse.

    python tools/deploy/candidate.py --tree tree.tar --out DIR [--lock wheelhouse.lock]

Writes into DIR:

  staged/          tree.tar unpacked and stamped through tools/release/release.py
                   SURFACES, so README, pyproject, the plugin manifests and
                   server.json all name the candidate version. docsnip reads
                   the docs from here and gitmirror commits it as the release.
  dist/            the wheel and the sdist, built with the running interpreter
                   (`python -m build --no-isolation`, offline).
  candidate.json   version, the release it follows, both artifact names, and
                   hashes of the staged sources and of their file list.

A tree whose version is not above the newest release in wheelhouse.lock is
stamped one patch past that release, so pip, uv and pipx always see the
candidate as the upgrade.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def next_patch(version: str) -> str:
    major, minor, patch = version_tuple(version)
    return f"{major}.{minor}.{patch + 1}"


def stamp_version(tree_version: str, newest_release: str) -> str:
    """The tree's own version when it is already past the newest release."""
    if version_tuple(tree_version) > version_tuple(newest_release):
        return tree_version
    return next_patch(newest_release)


def tree_version(root: Path) -> str:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def newest_release(lock: Path) -> str:
    return tomllib.loads(lock.read_text(encoding="utf-8"))["newest"]["crapkit"]


def extract(tree: Path, dest: Path) -> Path:
    dest.mkdir(parents=True)
    with tarfile.open(tree) as tar:
        tar.extractall(dest, filter="tar")
    return dest


def surfaces(root: Path) -> tuple:
    """release.py's SURFACES table, read from the tree under test itself."""
    spec = importlib.util.spec_from_file_location("_deploy_release", root / "tools/release/release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SURFACES


def _stamp_file(path: Path, patterns: list[str], old: str, new: str) -> bool:
    text = path.read_text(encoding="utf-8")
    stamped = text
    for pattern in patterns:
        stamped = stamped.replace(pattern.format(v=old), pattern.format(v=new))
    path.write_bytes(stamped.encode("utf-8"))
    return stamped != text


def stamp(root: Path, old: str, new: str) -> list[str]:
    """Rewrite every surface from `old` to `new`; the paths that changed."""
    by_path: dict[str, list[str]] = {}
    for surface in surfaces(root):
        by_path.setdefault(surface.path, []).append(surface.pattern)
    return [path for path, patterns in by_path.items()
            if (root / path).exists() and _stamp_file(root / path, patterns, old, new)]


def _files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def hashes(root: Path) -> dict[str, str]:
    """sha256 of the staged sources (paths and bytes) and of the file list alone."""
    sources, listing = hashlib.sha256(), hashlib.sha256()
    for path in _files(root):
        name = path.relative_to(root).as_posix().encode("utf-8")
        listing.update(name + b"\0")
        sources.update(name + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return {"source_hash": sources.hexdigest(), "file_list_hash": listing.hexdigest()}


def build(root: Path, dist: Path, python: str = sys.executable) -> list[Path]:
    argv = [python, "-m", "build", "--no-isolation", "--sdist", "--wheel", "--outdir", str(dist), str(root)]
    subprocess.run(argv, check=True, capture_output=True)
    return sorted(dist.iterdir())


def _artifact(built: list[Path], suffix: str) -> str:
    return next(path.name for path in built if path.name.endswith(suffix))


def candidate(tree: Path, out: Path, lock: Path, python: str = sys.executable) -> dict:
    staged = extract(tree, out / "staged")
    base, newest = tree_version(staged), newest_release(lock)
    version = stamp_version(base, newest)
    changed = stamp(staged, base, version) if version != base else []
    record = {"version": version, "tree_version": base, "newest_release": newest,
              "stamped": changed, **hashes(staged)}
    built = build(staged, out / "dist", python)
    record.update(wheel=_artifact(built, ".whl"), sdist=_artifact(built, ".tar.gz"))
    (out / "candidate.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                                        encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=HERE / "wheelhouse.lock")
    args = parser.parse_args(argv)
    record = candidate(args.tree, args.out, args.lock)
    print(f"candidate: crapkit {record['version']} ({record['wheel']}, {record['sdist']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
