"""Identity across renames and moves: where `ratchet prune` puts a mark after git
moves, copies or rewrites its file.

docs/ratchet.md#pruning-and-renames: prune re-paths a mark when its function
is gone from the recorded path, git calls that path renamed (-M, 50 percent
similar by default: git-diff docs), and the same key name exists at the
destination; a copy never moves a mark. RENAMED_MARKS applies those three
conditions by hand to the moves in RENAME_MOVES; nightly, pygit2's own rename
detection (find_similar at 50 percent) and a parse of each file's functions
apply them again. The marks are read with oracles/marks_history_walk.py's
reader. This file imports no crapkit.
"""
from __future__ import annotations

import ast
from pathlib import Path
import shutil

import pytest

from accuracy.kit import drive, repos
from accuracy.history_oracles.oracles import marks_history_walk, pygit2_renames
from accuracy.history_oracles.repos import history_specs as specs

pytestmark = pytest.mark.process
MARKS = "crapkit-ratchet.tsv"


def _ok(result) -> None:
    assert result.code == 0, result.stdout + result.stderr


def seeded(make_repo, spec: repos.Spec) -> tuple[repos.Built, drive.Driver, str]:
    """The spec measured, seeded and its marks committed; also the first run's commit."""
    built = make_repo(spec)
    driver = drive.Driver(built.root, date_now=specs.RENAMES_NOW)
    _ok(driver.run("coverage"))
    _ok(driver.run("ratchet", "seed"))
    base = repos.git(built.top, "rev-parse", "HEAD").strip()
    repos.git(built.root, "add", "--", MARKS)
    repos.git(built.top, "commit", "-q", "-m", "marks", date=repos.EPOCH + 1)
    return built, driver, base


def _move(root: Path, old: str, new: str, text) -> None:
    (root / new).parent.mkdir(parents=True, exist_ok=True)
    if text == "keep":
        shutil.copyfile(root / old, root / new)
    else:
        repos.git(root, "mv", "--", old, new)
    if text not in ("keep", None):
        (root / new).write_bytes(text.encode("utf-8"))
    repos.git(root, "add", "--", new)


def moved(built: repos.Built, driver: drive.Driver) -> None:
    for old, new, text in specs.RENAME_MOVES.values():
        _move(built.root, old, new, text)
    repos.git(built.top, "commit", "-q", "-m", "moves", date=repos.EPOCH + 2)
    _ok(driver.run("coverage"))
    _ok(driver.run("ratchet", "prune"))


def marks(root: Path) -> set[tuple[str, str]]:
    """{(path, bare function name)} of the marks on disk."""
    found = marks_history_walk.read_marks((root / MARKS).read_bytes().decode("utf-8"))
    return {(path, name.split("(")[0]) for path, name in found}


def test_marks_follow_the_documented_renames(make_repo):
    built, driver, _ = seeded(make_repo, specs.RENAMES)

    moved(built, driver)

    assert marks(built.root) == specs.RENAMED_MARKS


def test_a_root_below_the_git_top_follows_a_rename(make_repo):
    """docs/ratchet.md#a-crapkit-root-below-the-git-top: marks carry root-relative
    paths at both ends of a rename."""
    built, driver, _ = seeded(make_repo, specs.RENAMES_NESTED)
    repos.git(built.top, "mv", "pkg/calc/grade.py", "pkg/calc/grading.py")
    repos.git(built.top, "commit", "-q", "-m", "rename", date=repos.EPOCH + 2)

    _ok(driver.run("coverage"))
    _ok(driver.run("ratchet", "prune"))

    assert marks(built.root) == {("calc/grading.py", "audit"), ("calc/grading.py", "classify")}


def _functions(root: Path, path: str) -> set[str]:
    """The function names Python's own parser finds in the file, none when it is gone."""
    target = root / path
    if not target.is_file():
        return set()
    tree = ast.parse(target.read_bytes())
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _followed(root: Path, key: tuple[str, str], renames: dict[str, str]) -> tuple | None:
    """Where the docs' three conditions put one seeded mark; None when it drops."""
    path, name = key
    if name in _functions(root, path):
        return key
    destination = renames.get(path)
    if destination and name in _functions(root, destination):
        return destination, name
    return None


@pytest.mark.nightly
def test_pygit2_renames_place_the_marks(make_repo, oracle):
    oracle("pygit2")
    built, driver, base = seeded(make_repo, specs.RENAMES)
    before = marks(built.root)

    moved(built, driver)

    renames = pygit2_renames.renames(built.top, base, "HEAD")
    placed = {_followed(built.root, key, renames) for key in before}
    assert marks(built.root) == placed - {None}
