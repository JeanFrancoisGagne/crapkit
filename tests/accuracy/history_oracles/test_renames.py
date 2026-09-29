"""Identity across renames and moves: where `ratchet prune` puts a mark after git
moves, copies or rewrites its file.

docs/ratchet.md#pruning-and-renames: prune re-paths a mark when its function
is gone from the recorded path, git calls that path renamed (-M, 50 percent
similar by default: git-diff docs), and the same key name exists at the
destination; a copy never moves a mark. hand_renames.tsv applies those three
conditions by hand to the moves in RENAME_MOVES; nightly, pygit2's own rename
detection (find_similar at 50 percent) and a parse of each file's functions
apply them again. The marks are read with oracles/marks_history_walk.py's
reader. Across surfaces, the pre-commit hook, `rescore --gate` and `verify`
each pardon a touched function whose mark followed it and gate the rest. This
file imports no crapkit.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import shutil

import pytest

from accuracy.kit import drive, repos
from accuracy.history_oracles import history_hand
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

    assert marks(built.root) == history_hand.renamed_marks()


# The files that hold a gated function after the moves; src/c2.py's fc has no branch.
TOUCHED = ("src/moved/a.py", "src/b.py", "src/b_copy.py", "src/d2.py", "src/e2.py")
_HOOK_LINE = re.compile(r"^\s+ccn\s+\d+\s+(\S+):\d+\s+(\w+)\(", re.MULTILINE)


def _touch(root: Path) -> None:
    """Every function in TOUCHED returns another string: its branches stay as they were."""
    for path in TOUCHED:
        target = root / path
        target.write_bytes(re.sub(rb"return '\w+'", b"return 'touched'", target.read_bytes()))


def _mccabe(node: ast.FunctionDef) -> int:
    """McCabe's number of a function with no boolean operators: 1 plus its ifs."""
    return 1 + sum(isinstance(inner, ast.If) for inner in ast.walk(node))


def _over_ceiling(root: Path) -> set[tuple[str, str]]:
    """{(path, function)} in TOUCHED whose McCabe number passes the target of 3."""
    found = set()
    for path in TOUCHED:
        tree = ast.parse((root / path).read_bytes())
        found |= {(path, node.name) for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and _mccabe(node) > 3}
    return found


def _rescore_gated(driver: drive.Driver) -> set[tuple[str, str]]:
    result = driver.run("rescore", *TOUCHED, "--gate", "--json")
    assert result.code in (0, 6), result.stdout + result.stderr
    return {(b["path"], b["function"].split("(")[0]) for b in result.json()["gate"]["breaches"]}


def _hook_gated(built: repos.Built, driver: drive.Driver) -> set[tuple[str, str]]:
    repos.git(built.root, "add", "--", *TOUCHED)
    result = driver.run("hook-precommit")
    assert result.code in (0, 6), result.stdout + result.stderr
    return set(_HOOK_LINE.findall(result.stdout + result.stderr))


def _verify_gated(driver: drive.Driver) -> set[tuple[str, str]]:
    result = driver.run("verify", "--no-tighten", "--json")
    assert result.code in (0, 6), result.stdout + result.stderr
    return {(v["path"], v["key_name"].split("(")[0]) for v in result.json()["gate_violations"]}


def test_every_gate_reads_the_followed_marks(make_repo):
    """After prune follows the renames, a touched function over the ceiling is
    pardoned at the hook, `rescore --gate` and `verify` exactly when its mark
    followed it (hand_renames.tsv): the copy's fb and the renamed fe_new are
    gated at all three, and the moved fa, ga, fd and gd at none."""
    built, driver, _ = seeded(make_repo, specs.RENAMES)
    moved(built, driver)
    _touch(built.root)
    expected = _over_ceiling(built.root) - history_hand.renamed_marks()

    gated = (_rescore_gated(driver), _hook_gated(built, driver), _verify_gated(driver))

    assert gated == (expected,) * 3
    assert expected == {("src/b_copy.py", "fb"), ("src/e2.py", "fe_new")}


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
