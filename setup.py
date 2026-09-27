"""The build writes the commit it was built from into the package.

`crapkit --version --json` reads a source checkout's commit and dirty flag from
git. An installed wheel has no checkout beside it, so the build writes both
into crapkit/_build.json: a wheel built from a checkout, from a git URL, or
from an sdist cut in a checkout names that commit. A tree with no .git and no
stamp, such as an export that left the history behind, builds with none, and
--version --json prints null for it.

The rest of the build is in pyproject.toml. setuptools runs this file as
__main__; tests/unit/test_build_stamp.py loads it under another name.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAMP = "_build.json"
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_GIT_SECONDS = 30


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(("git", "--no-optional-locks", *args), cwd=root, capture_output=True,
                          check=True, timeout=_GIT_SECONDS)
    return done.stdout.decode("utf-8", "replace")


def stamp_of(commit: object, dirty: object) -> dict | None:
    """{"commit", "dirty"} when commit is a full sha and dirty a boolean, else None."""
    if isinstance(commit, str) and _COMMIT.fullmatch(commit) and isinstance(dirty, bool):
        return {"commit": commit, "dirty": dirty}
    return None


def checkout_stamp(root: Path) -> dict | None:
    """The commit the checkout at `root` is at, and whether it holds changes that
    commit does not: staged or unstaged edits, or a file git neither tracks nor
    ignores. None when root is not the top of a checkout, since a tree inside some
    other repository must not take that repository's HEAD, and when git cannot
    read it."""
    if not (root / ".git").exists():
        return None
    try:
        commit = _git(root, "rev-parse", "HEAD").strip()
        changes = _git(root, "status", "--porcelain", "--untracked-files=normal")
    except (OSError, subprocess.SubprocessError):
        return None
    return stamp_of(commit, bool(changes.strip()))


def carried_stamp(package: Path) -> dict | None:
    """The stamp a source tree's package already holds: an sdist's, written when
    the sdist was cut in a checkout."""
    try:
        found = json.loads((package / STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return stamp_of(found.get("commit"), found.get("dirty")) if isinstance(found, dict) else None


def tree_stamp(root: Path = ROOT) -> dict | None:
    """The stamp for the tree at `root`: its checkout's, else the one it carries.

    Read before the command writes anything: sdist makes its release tree inside
    the checkout, and that untracked directory would read as a dirty checkout."""
    return checkout_stamp(root) or carried_stamp(root / "src" / "crapkit")


def write_stamp(package: Path, stamp: dict | None) -> None:
    """Write `stamp` into the built `package`, or remove a stale one when there is
    no stamp to give: a build/lib reused from an earlier build keeps its files."""
    target = package / STAMP
    if stamp is None:
        target.unlink(missing_ok=True)
        return
    package.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(stamp, sort_keys=True) + "\n", encoding="utf-8")


def commands() -> dict:
    """build_py and sdist, each writing the stamp into what it builds. An editable
    install gets none: it runs from the checkout, which git answers for."""
    from setuptools.command.build_py import build_py
    from setuptools.command.sdist import sdist

    class StampedBuildPy(build_py):
        def run(self):
            stamp = tree_stamp()
            super().run()
            if not getattr(self, "editable_mode", False):
                write_stamp(Path(self.build_lib) / "crapkit", stamp)

    class StampedSdist(sdist):
        def make_release_tree(self, base_dir, files):
            stamp = tree_stamp()
            super().make_release_tree(base_dir, files)
            write_stamp(Path(base_dir) / "src" / "crapkit", stamp)

    return {"build_py": StampedBuildPy, "sdist": StampedSdist}


if __name__ == "__main__":
    from setuptools import setup

    setup(cmdclass=commands())
