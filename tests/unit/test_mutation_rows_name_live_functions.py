"""Every survivors.tsv and equivalent.tsv row names a function mutmut can mutate.

tools/accuracy/mutation.py keys a row by module, function and diff digest.
Once a commit removes a function, renames it or moves it to another module, no
mutant can match its rows again: the gate never reads them, and nothing says
they are dead. This check names each such row on push, so the commit that moves
the function drops them.

The units are mutation.py's: top-level functions and the methods of top-level
classes. They are read from the tree as written, which the calc mutation stage
names in CRAPKIT_ACCURACY_SOURCE, since mutmut's copy holds trampolines.
"""
from __future__ import annotations

import ast
import functools
import importlib.util
from pathlib import Path
import sys

import pytest

from accuracy.kit import source_tree

REPO = Path(__file__).resolve().parents[2]


def _load_mutation():
    spec = importlib.util.spec_from_file_location("accuracy_mutation_rows",
                                                  REPO / "tools" / "accuracy" / "mutation.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mutation = _load_mutation()
TABLES = {"survivors.tsv": mutation.SURVIVOR_COLUMNS,
          "equivalent.tsv": mutation.EQUIVALENT_COLUMNS}


def written_tree() -> Path:
    """The tree as written: source_tree.root() is its src/crapkit."""
    return source_tree.root().parents[1]


@functools.lru_cache(maxsize=None)
def units(path: Path) -> frozenset[str]:
    """The names the tables give the functions mutmut mutates in `path`; none when
    the file is gone."""
    if not path.is_file():
        return frozenset()
    return frozenset(name for name, _, _ in mutation._functions(ast.parse(path.read_bytes())))


def dead_rows(rows: list[dict], tree: Path) -> list[str]:
    """Each row whose module no longer holds its function, as `module function key`."""
    return [" ".join((row["module"], row["function"], row["diff_sha256"][:12])) for row in rows
            if row["function"] not in units(tree / row["module"])]


@pytest.mark.parametrize("table", sorted(TABLES))
def test_every_mutation_table_row_names_a_function_the_tree_holds(table):
    rows = mutation.read_table(mutation.TABLES / table, TABLES[table])

    assert dead_rows(rows, written_tree()) == [], (
        f"{table} rows name functions this tree no longer holds: drop each row "
        "(a moved function's mutants come back under its new module)")


def _row(module: str, function: str) -> dict:
    return {"module": module, "function": function, "diff_sha256": "ab" * 32}


def test_a_row_names_a_live_unit_or_is_dead(tmp_path):
    (tmp_path / "m.py").write_text(
        "def f():\n    def inner():\n        pass\n\n\n"
        "class C:\n    def m(self):\n        pass\n", encoding="utf-8")
    rows = [_row("m.py", "f"), _row("m.py", "C.m"), _row("m.py", "inner"), _row("m.py", "g"),
            _row("m.py", "m"), _row("gone.py", "f")]

    assert dead_rows(rows, tmp_path) == [f"m.py inner {'ab' * 6}", f"m.py g {'ab' * 6}",
                                         f"m.py m {'ab' * 6}", f"gone.py f {'ab' * 6}"]


def test_the_tree_read_is_the_one_the_stage_names(tmp_path, monkeypatch):
    monkeypatch.setenv(source_tree.ENV, str(tmp_path / "src" / "crapkit"))

    assert written_tree() == tmp_path


def test_unset_the_tree_read_is_this_checkout(monkeypatch):
    monkeypatch.delenv(source_tree.ENV, raising=False)

    assert written_tree() == REPO
