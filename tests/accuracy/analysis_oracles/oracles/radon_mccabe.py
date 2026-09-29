"""radon 6.0.1 and mccabe 0.7.0 cyclomatic complexity per Python function, and
the named transforms that map each tool's counting onto crapkit's ccn_std.

Both tools count McCabe's decisions from the ast, and each departs from
crapkit's documented count (README "crapkit": standard cyclomatic complexity
read off lizard's Python reader) in a few named places. Every transform below
is one rulings.tsv row, and test_complexity_oracles pins each with a hand case
that states the tool's raw value and the transformed one. A def holding a
shape crapkit misreads (py_defect_shapes.CCN) is left out and counted.

The transforms count ast nodes; none reads crapkit. A decision here is what
NIST SP 500-235 sec. 4.1 calls one: an `if`, `elif`, loop, `except` clause,
short-circuit operator, conditional expression, comprehension `for` or `if`,
or `case` arm.
"""
from __future__ import annotations

import ast
from functools import lru_cache
import re

import mccabe
from radon.visitors import ComplexityVisitor

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
TRIES = tuple(getattr(ast, name) for name in ("Try", "TryStar") if hasattr(ast, name))
LOOPS = (ast.For, ast.AsyncFor, ast.While)


# --- the raw tools ------------------------------------------------------------------------

def _radon_blocks(blocks, out: dict) -> dict:
    for block in blocks:
        if hasattr(block, "closures"):
            out[(block.lineno, block.col_offset)] = block.complexity
            _radon_blocks(block.closures, out)
        if hasattr(block, "methods"):
            _radon_blocks(block.methods, out)
            _radon_blocks(block.inner_classes, out)
    return out


@lru_cache(maxsize=64)
def radon_values(source: str) -> dict[tuple[int, int], int]:
    """{(def line, def column): radon's complexity} for every function radon lists."""
    visitor = ComplexityVisitor.from_code(source)
    return _radon_blocks(visitor.functions + visitor.classes, {})


@lru_cache(maxsize=64)
def mccabe_values(source: str) -> dict[tuple[int, int], int]:
    """{(def line, def column): mccabe's complexity} for each function and method
    mccabe graphs; a nested def is folded into its parent's graph."""
    visitor = mccabe.PathGraphingAstVisitor()
    visitor.preorder(ast.parse(source), visitor)
    return {(graph.lineno, graph.column): graph.complexity()
            for graph in visitor.graphs.values() if not MODULE_GRAPH.match(graph.entity)}


# mccabe also graphs a module-level if, loop or try, named after its line.
MODULE_GRAPH = re.compile(r"^(If|Loop|TryExcept) \d+$")


# --- walking one function's own nodes -----------------------------------------------------

def _decorators(node) -> list:
    return list(getattr(node, "decorator_list", ()))


def _body_children(node) -> list:
    """A node's children without its decorators, which sit above its first line."""
    decorators = set(map(id, _decorators(node)))
    return [child for child in ast.iter_child_nodes(node) if id(child) not in decorators]


def own_nodes(node, stop: tuple = FUNCTIONS):
    """The nodes on `node`'s own lines: its decorators left out, and a nested
    node of a `stop` type left out except for its decorators, which sit on the
    enclosing function's lines."""
    todo = _body_children(node)
    while todo:
        child = todo.pop()
        if isinstance(child, stop):
            todo.extend(_decorators(child))
            continue
        yield child
        todo.extend(ast.iter_child_nodes(child))


def _expression_decisions(node) -> int:
    """Short-circuit operators, conditional expressions and comprehension for/if."""
    if isinstance(node, ast.BoolOp):
        return len(node.values) - 1
    if isinstance(node, ast.IfExp):
        return 1
    return 1 + len(node.ifs) if isinstance(node, ast.comprehension) else 0


def _statement_decisions(node) -> int:
    """if/elif, loops, except clauses, finally, case arms and case guards. An
    unguarded `case _` is the default, which NIST sec. 4.1 leaves out."""
    if isinstance(node, (ast.If, *LOOPS)):
        return 1
    if isinstance(node, TRIES):
        return len(node.handlers) + bool(node.finalbody)
    if isinstance(node, ast.Match):
        return sum(1 + (case.guard is not None) - _is_wildcard(case) for case in node.cases)
    return 0


def decisions(node, stop: tuple = FUNCTIONS) -> int:
    """Every decision under `node` (itself included), in crapkit's documented count."""
    nodes = [node, *own_nodes(node, stop)]
    return sum(_expression_decisions(item) + _statement_decisions(item) for item in nodes)


def modified(fn) -> int:
    """ccn_mod: 1 + every decision, with each match's case arms read as one
    decision (lizard README, option -m: "count a switch/case with multiple
    cases as one CCN"). Guards stay, as `if`s."""
    matches = _count(fn, ast.Match)
    arms = sum(1 - _is_wildcard(case) for match in matches for case in match.cases)
    return 1 + decisions(fn) - arms + len(matches)


def _count(fn, kind, stop: tuple = FUNCTIONS) -> list:
    return [node for node in own_nodes(fn, stop) if isinstance(node, kind)]


def _is_wildcard(case) -> bool:
    pattern = case.pattern
    return isinstance(pattern, ast.MatchAs) and pattern.pattern is None and case.guard is None


# --- radon -> crapkit ----------------------------------------------------------------------

RADON_STOP = FUNCTIONS + (ast.ClassDef,)


def _radon_assert(fn) -> int:
    """radon adds 1 per assert and reads nothing inside it; lizard adds nothing
    for assert and counts the operators in its test."""
    asserts = _count(fn, ast.Assert, RADON_STOP)
    inner = sum(_expression_decisions(node) for item in asserts for node in ast.walk(item))
    return inner - len(asserts)


def _radon_finally(fn) -> int:
    """lizard's Python reader counts `finally` as a decision; radon does not."""
    return sum(bool(node.finalbody) for node in _count(fn, TRIES, RADON_STOP))


def _radon_else(fn) -> int:
    """radon adds 1 for the else of a loop or a try; lizard adds nothing for else."""
    return -sum(bool(node.orelse) for node in _count(fn, (*LOOPS, *TRIES), RADON_STOP))


def _radon_guard(fn) -> int:
    """radon does not count a case guard's `if`; lizard counts it."""
    matches = _count(fn, ast.Match, RADON_STOP)
    return sum(case.guard is not None for match in matches for case in match.cases)


def _radon_class_body(fn) -> int:
    """radon scores a class body nested in a function as the class's; lizard
    charges its decisions to the enclosing function."""
    classes = [node for node in own_nodes(fn) if isinstance(node, ast.ClassDef)]
    return sum(decisions(stmt) for stmt in _class_statements(classes))


def _class_statements(classes: list) -> list:
    """The statements of class bodies that are neither defs nor classes."""
    statements = [stmt for item in classes for stmt in item.body]
    return [stmt for stmt in statements if not isinstance(stmt, (*FUNCTIONS, ast.ClassDef))]


RADON = {
    "AO-RADON-ASSERT": _radon_assert,
    "AO-RADON-FINALLY": _radon_finally,
    "AO-RADON-ELSE": _radon_else,
    "AO-RADON-GUARD": _radon_guard,
    "AO-RADON-CLASS-BODY": _radon_class_body,
}


def radon_expected(fn, raw: int, transforms: dict = RADON) -> int:
    """radon's raw value with every transform applied."""
    return raw + sum(transform(fn) for transform in transforms.values())


# --- mccabe -> crapkit ---------------------------------------------------------------------
#
# mccabe 0.7.0 builds a path graph from statements only (mccabe.py
# PathGraphingAstVisitor). It walks if, loops, with and class bodies, a try's
# body, handlers and else, and a nested def as part of its parent. It never
# walks a try's finally body or anything inside a match statement, and it
# counts no expression.

def _is_try(node) -> bool:
    return isinstance(node, TRIES)


def _walked_children(node) -> tuple[list, list]:
    """(children mccabe walks, children it skips) of one node."""
    children = list(ast.iter_child_nodes(node))
    if isinstance(node, ast.Match):
        return [], children
    if _is_try(node):
        return [child for child in children if child not in node.finalbody], node.finalbody
    return children, []


class _Regions:
    """One function's own nodes split into what mccabe walks and what it skips."""

    def __init__(self, fn):
        self.walked, self.skipped, self.nested = [], [], []
        self._walk(fn)

    def _walk(self, node) -> None:
        walked, skipped = _walked_children(node)
        self.skipped += [item for child in skipped for item in [child, *own_nodes(child)]
                         if not isinstance(item, FUNCTIONS)]
        for child in walked:
            self._visit(child)

    def _visit(self, child) -> None:
        if isinstance(child, FUNCTIONS):
            self.nested.append(child)
            return
        self.walked.append(child)
        self._walk(child)


def _mccabe_try(regions: _Regions, source: str) -> int:
    """A try mccabe walks adds 1 + one per handler there; lizard adds one per
    handler and one for finally."""
    return sum(bool(node.finalbody) - 1 for node in regions.walked if _is_try(node))


def _mccabe_expressions(regions: _Regions, source: str) -> int:
    """mccabe counts no expression; lizard counts short-circuit operators,
    conditional expressions and comprehension for/if."""
    return sum(_expression_decisions(node) for node in regions.walked)


def _mccabe_skipped(regions: _Regions, source: str) -> int:
    """Everything in a finally body or a match statement, which mccabe never
    walks: case arms and guards and every decision in those statements."""
    walked_matches = [node for node in regions.walked if isinstance(node, ast.Match)]
    nodes = walked_matches + regions.skipped
    return sum(_expression_decisions(node) + _statement_decisions(node) for node in nodes)


def _mccabe_nested(regions: _Regions, source: str) -> int:
    """mccabe folds a nested def it walks into its parent; crapkit lists it as a
    function of its own. Take back mccabe's value for that def measured alone."""
    return -sum(_alone(node) for node in regions.nested)


def _alone(node) -> int:
    """mccabe's value for one def lifted out of its parent."""
    visitor = mccabe.PathGraphingAstVisitor()
    visitor.preorder(ast.Module(body=[node], type_ignores=[]), visitor)
    return next(iter(visitor.graphs.values())).complexity()


MCCABE = {
    "AO-MCCABE-TRY": _mccabe_try,
    "AO-MCCABE-EXPRESSIONS": _mccabe_expressions,
    "AO-MCCABE-SKIPPED": _mccabe_skipped,
    "AO-MCCABE-NESTED": _mccabe_nested,
}


def mccabe_expected(fn, raw: int, source: str = "", transforms: dict = MCCABE) -> int:
    """mccabe's raw value with every transform applied."""
    regions = _Regions(fn)
    return raw + sum(transform(regions, source) for transform in transforms.values())

