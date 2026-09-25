"""complexipy 8.0.1's cognitive complexity per Python function, and where it
can stand as an oracle.

complexipy implements the Sonar paper over Python's ast with conventions of its
own, each a rulings.tsv row with a hand case (test_cognitive_oracles):

- AO-CXP-COMP-FLAT: a comprehension's clauses are flat, each +1 at the
  comprehension's level (`[b for a in x for b in a]` reads 2; the paper nests
  a loop in a loop, B2);
- AO-CXP-LOOP-ELSE: a loop's or a try's else adds nothing (B1 lists else);
- AO-CXP-CONDITION, AO-CXP-LAMBDA, AO-CXP-TERNARY: a comprehension in an if's
  condition sits at the if's level, and a lambda and a conditional expression
  raise the level (the paper's reading; crapkit's documented readings differ).

Two more are places complexipy reads nothing or reads another level, so a
function holding them is left out of the comparison and counted, never
transformed (the oracle_bug rows AO-CXP-SKIPS and AO-CXP-NESTED-DEF):

- a counted expression (and/or, a conditional expression, a comprehension, a
  lambda) inside a binary operator, a subscript, an attribute, an f-string, a
  starred or walrus expression, await, yield, `not` or a call's arguments,
  which complexipy skips or charges one level deeper;
- a nested def, whose body complexipy charges to the enclosing function one
  level in.

No crapkit import.
"""
from __future__ import annotations

import ast

import complexipy

from accuracy.analysis_oracles.oracles import py_sonar

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
COUNTED = (ast.BoolOp, ast.IfExp, ast.Lambda, *py_sonar.COMPREHENSIONS)
# The Sonar counter's choices that reproduce complexipy's reading.
COMPLEXIPY = py_sonar.Choices(for_nests=False, filter_nests=False, filter_increment=False,
                              loop_else_counts=False, condition_nests=False)


def values(source: str) -> dict[int, int]:
    """{line of the def keyword or its first decorator: complexipy's value}."""
    return {fn.line_start: fn.complexity for fn in complexipy.code_complexity(source).functions}


def raw(fn, table: dict[int, int]) -> int | None:
    """complexipy's value for one def node, which it keys by its first line."""
    first = fn.decorator_list[0].lineno if fn.decorator_list else fn.lineno
    return table.get(fn.lineno, table.get(first))


def _parents(fn) -> dict[int, ast.AST]:
    return {id(child): node for node in py_sonar_nodes(fn) for child in ast.iter_child_nodes(node)}


PLAIN_PARENTS = (ast.IfExp, ast.Lambda, *py_sonar.COMPREHENSIONS, ast.comprehension)


def _plain_chain(node, parents: dict) -> bool:
    """Whether every node between a counted node and its statement is a
    conditional expression, a lambda or a comprehension (not an and/or, whose
    operands complexipy does not read)."""
    parent = parents.get(id(node))
    while parent is not None and not isinstance(parent, ast.stmt):
        if not isinstance(parent, PLAIN_PARENTS):
            return False
        parent = parents.get(id(parent))
    return True


def _negated_operand(fn) -> bool:
    """An and/or with a `not` operand, which complexipy counts as a new sequence."""
    return any(isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not)
               for node in py_sonar_nodes(fn) if isinstance(node, ast.BoolOp)
               for value in node.values)


def _counted_under_skip(fn) -> bool:
    parents = _parents(fn)
    counted = [node for node in py_sonar_nodes(fn) if isinstance(node, COUNTED)]
    return not all(_plain_chain(node, parents) for node in counted)


def py_sonar_nodes(fn) -> list:
    """Every node of fn's own body, nested defs left out."""
    todo, out = list(fn.body), []
    while todo:
        node = todo.pop()
        out.append(node)
        todo.extend(child for child in ast.iter_child_nodes(node)
                    if not isinstance(child, FUNCTIONS))
    return out


ELSE_HOLDERS = (*py_sonar.TRIES, *py_sonar.LOOPS)


def _plain_else(fn) -> bool:
    """A loop or a try with an else, whose body complexipy reads one level
    shallower than the paper's B2 places an else."""
    return any(isinstance(node, ELSE_HOLDERS) and node.orelse for node in py_sonar_nodes(fn))


def _ifs(node) -> list:
    return node.ifs if isinstance(node, ast.comprehension) else []


def _filter_nodes(fn) -> list:
    """Every node inside a comprehension's if clauses."""
    tests = [test for node in py_sonar_nodes(fn) for test in _ifs(node)]
    return [inner for test in tests for inner in ast.walk(test)]


def _counted_in_filter(fn) -> bool:
    """A counted expression in a comprehension's if, which complexipy reads one
    level deeper than the clause it sits in."""
    return any(isinstance(inner, COUNTED) for inner in _filter_nodes(fn))


def _method_recursion(fn) -> bool:
    """`self.name(...)` or `cls.name(...)` inside `name`, which complexipy does
    not read as recursion."""
    return any(isinstance(node, ast.Attribute) and node.attr == fn.name
               and isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")
               for node in py_sonar_nodes(fn))


def _has_nested_def(fn) -> bool:
    return any(isinstance(node, FUNCTIONS) for node in ast.walk(fn) if node is not fn)


def comparable(fn) -> bool:
    """Whether complexipy reads every counted node of fn the way the paper
    places it: none under a node it skips, and no nested def."""
    skips = (_has_nested_def, _counted_under_skip, _negated_operand, _plain_else,
             _method_recursion, _counted_in_filter)
    return not any(skip(fn) for skip in skips)


def expected(fn) -> int:
    """complexipy's value as the Sonar counter predicts it under COMPLEXIPY."""
    return py_sonar.count(fn, COMPLEXIPY).cognitive
