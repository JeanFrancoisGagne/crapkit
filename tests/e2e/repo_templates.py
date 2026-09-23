"""Fixture repos built once per worker and copied into each test.

A measured fixture repo costs a git history and a `crapkit coverage` run, and a
function-scoped fixture paid both again for every test that asked for it. The
built tree comes out the same every time, so a worker builds it once under its
own basetemp (`tmp_path.parent`, which xdist gives each worker) and each test
gets a copy with `.git` and `.crapkit` in it. No lock is needed: nothing else
writes a worker's basetemp.

A copy differs from a fresh build in two ways, and a fixture that uses one must
not depend on either: the absolute path the build ran in, and the time it ran.

The path is the staging dir `template` deletes after the rename. The `.crapkit`
store holds no absolute path, but a lane artifact the build's `coverage` wrote
(cov.json, cov/unit.json) keys each file by it, and in 7 of the 18 templates
the copy still holds that artifact. Against a copy, `brief`, `next` and
`worklist` read the dark lines as unmeasured ("no lane artifact measured
src/app.ts"), and `--reuse-artifacts` and `--reuse-unchanged` reuse an
artifact that measures nothing here. A test that reads dark lines or reuses
artifacts runs `coverage` in its copy first, or builds fresh.

The time is why the build's UTC day is part of the stored name. crapkit keys its
churn caches on the UTC date, so a tree built before midnight would give a test
that runs after it caches the CLI throws away, and that test would take a cold
path a fresh build never takes.

test_inventory_e2e.py's `mini_repo` is where this started.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def template(tmp_path: Path, name: str, build: Callable[[Path], object]) -> Path:
    """This worker's built `name`. The first call runs `build` on an empty
    staging dir and renames the result into place only once `build` returns, so
    a build that raised leaves nothing behind for the next test to copy."""
    built = tmp_path.parent / f"template-{name}-{_utc_day()}"
    if not built.exists():
        with TemporaryDirectory(dir=tmp_path.parent) as staging:
            repo = Path(staging) / name
            repo.mkdir()
            build(repo)
            repo.rename(built)
    return built


def _copy_unless_same(source: str, dest: str) -> str:
    """copy2, except onto a file with the source's size and mtime. copy2 keeps
    both, so such a file is an earlier copy of the same bytes, and a git object
    among them is read-only, which Windows refuses to overwrite."""
    if os.path.exists(dest) and _stamp(os.stat(dest)) == _stamp(os.stat(source)):
        return dest
    return shutil.copy2(source, dest)


def copy_of(built: Path, dest: Path) -> Path:
    """A private copy of `built` at `dest`. `dest` may already exist: the empty
    dir pytest made, or an unchanged copy of a tree `built` grew from."""
    shutil.copytree(built, dest, dirs_exist_ok=True, copy_function=_copy_unless_same)
    return dest


def _stamp(stat: os.stat_result) -> tuple[int, int]:
    return stat.st_size, stat.st_mtime_ns


def _stamps(root: Path) -> dict[str, tuple[int, int]]:
    return {path.relative_to(root).as_posix(): _stamp(path.stat())
            for path in root.rglob("*") if path.is_file()}


def unchanged(repo: Path, built: Path) -> bool:
    """Whether `repo` still holds exactly the files of the `built` it was copied
    from: no file added or removed, none written since. A write moves a file's
    mtime off the one the copy kept, so size and mtime answer it without
    reading a byte."""
    return _stamps(repo) == _stamps(built)
