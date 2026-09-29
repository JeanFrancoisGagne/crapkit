"""The static import closure of a file, followed through tests/ and tools/.

An expected value must not come from crapkit. test_kit_contract checks that the
files which compute expected values (every */oracles/** module, every
model_*.py, kit/exact.py and kit/strategies.py) and each calc's independent
test reach no crapkit module through their imports. The closure follows
`import x`, `from x import y` and `from . import y` into files under the
roots, and names every crapkit import it meets, including one under
TYPE_CHECKING. A module loaded at run time by path (kit.drive's in-process
runner) is not followed: that is how a test reads crapkit's output, never its
expected value. suite-strength's retro ledger digests the same closure.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOTS = (REPO / "tests", REPO / "tools" / "accuracy", REPO / "tools")


@lru_cache(maxsize=None)
def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_bytes(), filename=str(path))


def _import_nodes(path: Path) -> list:
    return [node for node in ast.walk(_tree(path)) if isinstance(node, (ast.Import, ast.ImportFrom))]


def _from_names(node: ast.ImportFrom) -> list[str]:
    head = node.module or ""
    names = [".".join(filter(None, (head, alias.name))) for alias in node.names]
    return ([head] if head else []) + names


def _names(node) -> list[str]:
    """The dotted names an import statement may load, most specific last."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return _from_names(node)


def _bases(node, path: Path, roots: tuple) -> tuple:
    level = getattr(node, "level", 0)
    return (path.parents[level - 1],) if level else (path.parent, *roots)


def _resolve(dotted: str, bases: tuple) -> Path | None:
    parts = dotted.split(".")
    for base in bases:
        for candidate in (base.joinpath(*parts).with_suffix(".py"),
                          base.joinpath(*parts, "__init__.py")):
            if candidate.is_file():
                return candidate.resolve()
    return None


def _prefixes(dotted: str) -> list[str]:
    """a, a.b and a.b.c for a.b.c: importing a module runs its packages first."""
    parts = dotted.split(".")
    return [".".join(parts[:end]) for end in range(1, len(parts) + 1)]


def _imported_files(path: Path, roots: tuple) -> set[Path]:
    found = set()
    for node in _import_nodes(path):
        bases = _bases(node, path, roots)
        names = {prefix for name in _names(node) for prefix in _prefixes(name)}
        found.update(filter(None, (_resolve(name, bases) for name in names)))
    return found


def closure(path: Path, roots: tuple = ROOTS) -> set[Path]:
    """Every file under `roots` that `path` reaches through static imports, itself included."""
    seen: set[Path] = set()
    todo = [Path(path).resolve()]
    while todo:
        current = todo.pop()
        if current not in seen:
            seen.add(current)
            todo.extend(_imported_files(current, roots))
    return seen


def _absolute_names(node) -> list[str]:
    return _names(node) if getattr(node, "level", 0) == 0 else []


def crapkit_imports(path: Path) -> list[str]:
    """The crapkit modules one file imports by name."""
    names = (name for node in _import_nodes(path) for name in _absolute_names(node))
    return sorted({name for name in names if name.split(".")[0] == "crapkit"})


def _shown(path: Path, roots: tuple) -> str:
    for base in (REPO, *roots):
        if base in path.parents:
            return path.relative_to(base).as_posix()
    return path.as_posix()


def crapkit_reach(path: Path, roots: tuple = ROOTS) -> list[str]:
    """'<file> imports <module>' for every crapkit import in the closure of `path`."""
    reached = sorted(closure(path, roots))
    return [f"{_shown(file, roots)} imports {name}" for file in reached
            for name in crapkit_imports(file)[:1]]
