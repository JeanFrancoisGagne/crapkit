"""Every rule of the gate module sits in a calc, so mutation tests it.

In 0.9.0 the touch, ceiling and pardon rules that verify, rescore --gate,
hook-precommit and claude-hook judge by left verify.py, hook.py and
cli/scoring.py for src/crapkit/gate.py. Mutation mutates the modules a
calcs.tsv row names (pyproject.toml's paths_to_mutate) and judges those a
floors.tsv group holds (tools/accuracy/mutation.py, weekly_modules), so a rule
in a module no row names is a rule no mutant tests, and a mutant there that
every test survives goes unseen. These checks read gate.py's top-level
functions from its source, hold each one that decides a finding to a calcs.tsv
row, and hold gate.py to a floors.tsv group at verify.py's floor, since it now
holds verify's rules. No crapkit import: the source is read as text from the tree
kit.source_tree names, which inside a mutation stage is the stage's checkout.
"""
from __future__ import annotations

import ast
import fnmatch
from pathlib import Path

from accuracy.kit import calcs, source_tree

GATE = "src/crapkit/gate.py"
VERIFY = "src/crapkit/verify.py"
FLOORS = Path(__file__).resolve().parents[1] / "suite_strength" / "mutation" / "floors.tsv"
# The top-level functions of gate.py that decide no finding, each with why. Every
# function there today decides one: touches and judge which functions are judged
# and the unreadable-name refusal, _unread the unread finding, _breaches the
# ceiling, _located and _keyed the ratchet key a breach's mark is looked up by,
# _pardons and _pardon the pardon, and when the marks are read.
LEFT_OUT: dict[str, str] = {}


def _gate_functions() -> list[str]:
    tree = ast.parse((source_tree.root() / "gate.py").read_bytes())
    return [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _named() -> set[str]:
    return {function for row in calcs.load() for function in row.functions}


def _floors() -> list[dict[str, str]]:
    """floors.tsv's rows, each keyed by its header."""
    header, *rows = [line.split("\t") for line in FLOORS.read_bytes().decode("utf-8").splitlines() if line]
    return [dict(zip(header, cells)) for cells in rows]


def _floor_of(module: str) -> float | None:
    """The floor of the group whose paths take `module`, None when no group does."""
    return next((float(row["floor"]) for row in _floors()
                 if any(fnmatch.fnmatchcase(module, pattern.strip()) for pattern in row["paths"].split(","))),
                None)


def test_every_gate_function_that_decides_a_finding_is_named_by_a_calc():
    functions = _gate_functions()
    named = _named()

    unnamed = [name for name in functions if name not in LEFT_OUT and f"{GATE}:{name}" not in named]

    assert "judge" in functions and "touches" in functions
    assert sorted(set(LEFT_OUT) - set(functions)) == [], "a LEFT_OUT entry names no gate.py function"
    assert unnamed == [], f"no calcs.tsv row names these {GATE} functions"


def test_gate_py_sits_under_a_floor_as_high_as_verify_py_s():
    """The floors are the only reader of a calc run's verdicts, so a module no
    floors.tsv group holds is never mutated; gate.py holds verify's rules, so its
    floor is verify's. It has a group of its own: change control keys a floor row
    by every cell but the floor (B3), so a path added to core's row reads as
    core's floor gone."""
    assert _floor_of(VERIFY) is not None
    assert _floor_of(GATE) == _floor_of(VERIFY)
