"""pylint 4.0.9's too-many-nested-blocks (R1702) as a nesting-depth oracle.

With max-nested-blocks=0 pylint reports every block nest it leaves with its
depth, "Too many nested blocks (N/0)", on a line inside the function. The
deepest N reported inside a def is that def's depth as pylint counts it
(pylint docs, refactoring checker R1702: if, elif, for, while, try and with
bodies each open a level).

Its count and crapkit's documented one (docs/agent-json.md "nesting": one
level per if, elif, else, for, while, except and comprehension for, none for
with, try, finally, match or a nested def) differ in named places, each a
rulings.tsv N-row with a hand case: pylint opens a level for a try and reads an
except body beside it, crapkit opens one for an except and for a comprehension's
for. A def that holds any of those, or a nested def (whose blocks pylint charges
to the enclosing scope), is left out of the comparison and counted.

One pylint process reads a whole file list. No crapkit import.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import sys

import hang_guard

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
TRIES = tuple(getattr(ast, name) for name in ("Try", "TryStar") if hasattr(ast, name))
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
DIFFERENT = (*TRIES, *COMPREHENSIONS, ast.Match, *FUNCTIONS)
DEPTH = re.compile(r"Too many nested blocks \((\d+)/0\)")
ARGV = ("-m", "pylint", "--disable=all", "--enable=R1702", "--max-nested-blocks=0",
        "--score=n", "--output-format=json", "--persistent=n")


def messages(paths: list[Path], cwd: Path) -> list[dict]:
    """pylint's R1702 messages for every file, from one process."""
    argv = [sys.executable, *ARGV, *map(str, paths)]
    done = hang_guard.run(argv, cwd=cwd, text=True, encoding="utf-8", errors="replace")
    if done.returncode & 1 or done.returncode & 32:
        raise RuntimeError(f"pylint failed (exit {done.returncode}): {done.stderr[-2000:]}")
    return json.loads(done.stdout or "[]")


def _depth(message: dict) -> int:
    return int(DEPTH.search(message["message"]).group(1))


def _relative(path: str, root: Path) -> str:
    """pylint names a file relative to the directory it ran in."""
    named = Path(path)
    return (named.relative_to(root) if named.is_absolute() else named).as_posix()


def deepest(found: list[dict], root: Path) -> dict[tuple[str, int], int]:
    """{(posix path relative to root, line): depth} for each reported line."""
    out: dict[tuple[str, int], int] = {}
    for message in found:
        key = (_relative(message["path"], root), message["line"])
        out[key] = max(out.get(key, 0), _depth(message))
    return out


def depth_of(fn, path: str, reported: dict) -> int:
    """The deepest depth pylint reported on fn's lines; 0 when none."""
    lines = range(fn.lineno, fn.end_lineno + 1)
    return max((depth for (where, line), depth in reported.items()
                if where == path and line in lines), default=0)


def comparable(fn) -> bool:
    """No try, comprehension, match or nested def anywhere in fn."""
    return not any(isinstance(node, DIFFERENT) for node in ast.walk(fn) if node is not fn)


def depths(files: dict[str, str], work: Path) -> dict:
    """Write `files` under work and read pylint's depths for them."""
    for name, text in files.items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return deepest(messages([work / name for name in files], work), work)
