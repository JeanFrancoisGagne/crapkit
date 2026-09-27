"""Hand the tree under test to a deploy container without mounting the checkout.

Writes two files into --out:

  src.bundle  `git bundle create --all`: every branch and tag plus HEAD, so the
              container can clone a mirror and a release can be modelled by
              moving the mirror's refs.
  tree.tar    the files `git ls-files -co --exclude-standard` names, with the
              mode git records, owned by root, dated at HEAD's commit time.
              build/, dist/ and venvs stay out because .gitignore keeps them out.

A mount failed three ways: git refused it as "dubious ownership" when the
runner's uid differed from the owner, a linked worktree's `.git` file pointed
at a host path the container cannot see, and ignored build output leaked into
the candidate. Run it from any checkout, a linked worktree included:

    python tools/deploy/export.py --repo . --out .crapkit/deploy-out/in
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

EXECUTABLE = "100755"
SYMLINK = "120000"


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=repo, check=True,
                          capture_output=True).stdout


def _entries(raw: bytes) -> list[str]:
    return [entry.decode("utf-8") for entry in raw.split(b"\0") if entry]


def index_modes(repo: Path) -> dict[str, str]:
    """path -> the mode git records ("100644", "100755", "120000")."""
    modes = {}
    for entry in _entries(git(repo, "ls-files", "-s", "-z")):
        meta, path = entry.split("\t", 1)
        modes[path] = meta.split(" ", 1)[0]
    return modes


def tree_files(repo: Path) -> list[str]:
    """Tracked and untracked paths that exist on disk, .gitignore applied."""
    listed = _entries(git(repo, "ls-files", "-co", "--exclude-standard", "-z"))
    return sorted(path for path in dict.fromkeys(listed) if os.path.lexists(repo / path))


def _member(name: str, mode: str, mtime: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mode = 0o755 if mode == EXECUTABLE else 0o644
    info.mtime = mtime
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    return info


def _add(tar: tarfile.TarFile, repo: Path, name: str, mode: str, mtime: int) -> None:
    info = _member(name, mode, mtime)
    source = repo / name
    if mode == SYMLINK and source.is_symlink():
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(source)
        tar.addfile(info)
        return
    data = source.read_bytes()
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def write_tree(repo: Path, dest: Path) -> Path:
    modes = index_modes(repo)
    mtime = int(git(repo, "log", "-1", "--format=%ct", "HEAD").strip() or 0)
    with tarfile.open(dest, "w", format=tarfile.PAX_FORMAT) as tar:
        for name in tree_files(repo):
            _add(tar, repo, name, modes.get(name, "100644"), mtime)
    return dest


def write_bundle(repo: Path, dest: Path) -> Path:
    git(repo, "bundle", "create", "-q", str(dest), "--all")
    return dest


def export(repo: Path, out: Path) -> tuple[Path, Path]:
    """Write out/src.bundle and out/tree.tar for the checkout at `repo`."""
    out.mkdir(parents=True, exist_ok=True)
    repo = Path(repo).resolve()
    return write_bundle(repo, out / "src.bundle"), write_tree(repo, out / "tree.tar")


def extract_options() -> dict:
    """tarfile's 'tar' filter where this Python has extraction filters. CPython
    took them in 3.11.4; 3.11.0 to 3.11.3 (Debian 12's python3 is 3.11.2) accept
    no `filter` argument, and every archive this kit unpacks there is its own
    export or a download already held to its pins.toml sha256."""
    return {"filter": "tar"} if hasattr(tarfile, "tar_filter") else {}


def unpack_tar(archive: Path, dest: Path) -> Path:
    """Unpack a tarball (tree.tar, or a pinned download) under dest."""
    with tarfile.open(archive) as tar:
        tar.extractall(dest, **extract_options())
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    bundle, tree = export(args.repo, args.out)
    print(f"export: {bundle} and {tree}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
