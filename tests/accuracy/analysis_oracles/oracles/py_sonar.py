"""Cognitive complexity and nesting depth of a Python function, counted over
Python's ast from G. Ann Campbell, "Cognitive Complexity", SonarSource,
version 1.7 (29 August 2023). No crapkit: every rule below cites the paper.

- B1 increments: if, elif and else (+1 each), a conditional expression, each
  loop and each except clause (+1), each sequence of like binary logical
  operators (+1), direct recursion (+1 once; section "Recursion").
- Recursion is a call that reaches the def by Python's scopes: `self.name(...)`,
  `cls.name(...)` or `Class.name(...)`, and a bare `name(...)` only from a def
  outside a class body (a method's bare name is the module's) that binds no
  `name` of its own (a parameter, an import, an assignment, a nested def).
- B2 nesting level: if, elif, else, loops, except, conditional expressions,
  match (a switch) and lambdas raise it; try, finally and with do not.
- B3 nesting increments: if, a conditional expression, loops, except and a
  switch add the nesting level they sit at.
- "Ignore shorthand" and "Jumps to labels": Python has no null-coalescing
  operator and no labeled jump, so neither appears.
- Section "Sequences of logical operators": a chain of logical operators is
  read left to right through parenthesized sub-chains, +1 for its first
  operator and +1 each time the operator changes (`a and b and c or d or e
  and f` scores 3); a `not` or a call ends the chain, so `a and not (b and c)`
  holds two sequences.

A comprehension is not in the paper. Read literally, its `for` is a foreach
and its `if` an if (+1 and the nesting they sit at), each raising the level
for what follows, and the element is evaluated inside them.

Choices names every place a reading could go two ways. PAPER is the paper's;
the counter never knows what crapkit does, and each choice crapkit makes
differently is a rulings.tsv row the tests pass as a Choices value.

Nested defs are left out: crapkit lists each def as its own function (docs
agent-json.md "nesting": a nested function's blocks count on its own row),
and a def's signature is not its body (docs/upgrading.md "Analysis version
11"), so a row's count starts after the signature's colon at nesting 0.
"""
from __future__ import annotations

import ast
import functools
from dataclasses import dataclass

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
LOOPS = (ast.For, ast.AsyncFor, ast.While)
TRIES = tuple(getattr(ast, name) for name in ("Try", "TryStar") if hasattr(ast, name))
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
# What keeps its own names: a nested scope binds nothing in the def around it.
SCOPES = (*FUNCTIONS, ast.Lambda, ast.ClassDef, *COMPREHENSIONS)
# The nodes that bind the name they carry in `name`.
NAMED = (*FUNCTIONS, ast.ClassDef, ast.ExceptHandler,
         *(getattr(ast, kind) for kind in ("MatchAs", "MatchStar") if hasattr(ast, kind)))


@dataclass(frozen=True)
class Choices:
    ternary_nests: bool = True     # B2 lists the ternary operator
    lambda_nests: bool = True      # B2 lists lambdas
    match_counts: bool = True      # B1 lists switch
    match_nests: bool = True       # B2 lists switch
    element_nests: bool = True     # a comprehension's element runs inside its loops
    filter_nests: bool = True      # B2 lists if: a comprehension's if raises the level
    iter_nests: bool = False       # a generator's iterable is evaluated before its own loop starts
    condition_nests: bool = False  # a condition is read at its structure's own level
    else_nests: bool = True        # B2 lists else
    loop_else_counts: bool = True  # B1 lists else; a loop's or a try's else is one
    for_nests: bool = True         # B2 lists loops: a comprehension's for raises the level
    filter_increment: bool = True  # B3 lists if: a comprehension's if adds its nesting level
    recursion_scopes: bool = True  # "Recursion": a call that reaches the def by Python's scopes


PAPER = Choices()


@dataclass
class Count:
    cognitive: int = 0
    depth: int = 0


class _Counter:
    def __init__(self, fn, choices: Choices, owner: str | None = None):
        self.fn, self.choices, self.count = fn, choices, Count()
        self.owner, self.bound = owner, bound_names(fn) if choices.recursion_scopes else frozenset()

    # -- helpers -------------------------------------------------------------------------

    def add(self, amount: int) -> None:
        self.count.cognitive += amount

    def body(self, statements, level: int) -> None:
        if statements:
            self.count.depth = max(self.count.depth, level)
        for statement in statements:
            self.statement(statement, level)

    def condition(self, node, level: int) -> None:
        """The test of an if, elif, loop or guard."""
        self.expression(node, level + 1 if self.choices.condition_nests else level)

    # -- statements ----------------------------------------------------------------------

    def statement(self, node, level: int) -> None:
        handler = _STATEMENTS.get(type(node), _Counter.other_statement)
        handler(self, node, level)

    def if_statement(self, node, level: int) -> None:
        self.add(1 + level)
        self.condition(node.test, level)
        self.body(node.body, level + 1)
        self.else_branch(node.orelse, level, node.col_offset)

    def else_branch(self, orelse: list, level: int, column: int) -> None:
        """elif and else: +1 each, no nesting increment (hybrid). ast gives an
        `elif` and an `else:` holding an `if` one shape; an elif's node starts
        at its chain's column, the nested if deeper."""
        if _is_elif(orelse, column):
            self.add(1)
            self.condition(orelse[0].test, level)
            self.body(orelse[0].body, level + 1)
            self.else_branch(orelse[0].orelse, level, column)
        elif orelse:
            self.add(1)
            self.body(orelse, level + (1 if self.choices.else_nests else 0))

    def loop(self, node, level: int) -> None:
        self.add(1 + level)
        self.condition(node.test if isinstance(node, ast.While) else node.iter, level)
        self.body(node.body, level + 1)
        self.plain_else(node.orelse, level)

    def plain_else(self, orelse: list, level: int) -> None:
        """A loop's or a try's else: B1 lists else, a hybrid increment."""
        if orelse:
            self.add(int(self.choices.loop_else_counts))
            self.body(orelse, level + 1)

    def try_statement(self, node, level: int) -> None:
        self.body(node.body, level)
        for handler in node.handlers:
            self.add(1 + level)
            self.body(handler.body, level + 1)
        self.plain_else(node.orelse, level)
        self.body(node.finalbody, level)

    def match_statement(self, node, level: int) -> None:
        if self.choices.match_counts:
            self.add(1 + level)
        self.expression(node.subject, level)
        inner = level + 1 if self.choices.match_nests else level
        for case in node.cases:
            if case.guard is not None:
                self.expression(case.guard, inner)
            self.body(case.body, inner)

    def with_statement(self, node, level: int) -> None:
        for item in node.items:
            self.expression(item.context_expr, level)
        self.body(node.body, level)

    def nested_def(self, node, level: int) -> None:
        """Its own row; only its decorators sit on this function's lines."""
        for decorator in node.decorator_list:
            self.expression(decorator, level)

    def class_def(self, node, level: int) -> None:
        for decorator in node.decorator_list:
            self.expression(decorator, level)
        for statement in node.body:
            self.statement(statement, level)

    def other_statement(self, node, level: int) -> None:
        for child in ast.iter_child_nodes(node):
            self.any(child, level)

    # -- expressions ---------------------------------------------------------------------

    def any(self, node, level: int) -> None:
        if isinstance(node, ast.stmt):
            self.statement(node, level)
        elif isinstance(node, ast.expr):
            self.expression(node, level)
        else:
            for child in ast.iter_child_nodes(node):
                self.any(child, level)

    def expression(self, node, level: int, operator=None) -> None:
        handler = _EXPRESSIONS.get(type(node), _Counter.other_expression)
        handler(self, node, level, operator)

    def bool_op(self, node, level: int, operator) -> None:
        """One chain of logical operators: +1 for its first operator and +1 each
        time the operator changes, read left to right through parenthesized
        sub-chains (a `not` or a call ends the chain). This is the in-order
        reading of the paper's examples, `a && b && c || d || e && f` scoring 3."""
        operators = _chain(node)
        self.add(1 + sum(left is not right for left, right in zip(operators, operators[1:])))
        for leaf in _leaves(node):
            self.expression(leaf, level)

    def unary(self, node, level: int, operator) -> None:
        """`not` ends a chain: a chain under it is its own (the paper's
        `a && !(b && c)` holds two sequences)."""
        self.expression(node.operand, level)

    def if_expression(self, node, level: int, operator) -> None:
        self.add(1 + level)
        self.expression(node.test, level)
        inner = level + 1 if self.choices.ternary_nests else level
        self.count.depth = max(self.count.depth, inner)
        self.expression(node.body, inner)
        self.expression(node.orelse, inner)

    def lambda_expression(self, node, level: int, operator) -> None:
        inner = level + 1 if self.choices.lambda_nests else level
        self.expression(node.body, inner)

    def comprehension(self, node, level: int, operator) -> None:
        depth = level
        for generator in node.generators:
            depth = self.generator(generator, depth)
        elements = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        inner = max(depth, level + 1) if self.choices.element_nests else level
        for element in elements:
            self.expression(element, inner)

    def generator(self, generator, depth: int) -> int:
        """One `for ... in ... if ...` clause: foreach +1, each if +1, each at
        the level it sits at. Returns the level the next clause sits at."""
        self.add(1 + depth)
        self.expression(generator.iter, depth + int(self.choices.iter_nests))
        depth += int(self.choices.for_nests)
        self.count.depth = max(self.count.depth, depth)
        for condition in generator.ifs:
            self.add(1 + depth * int(self.choices.filter_increment))
            self.expression(condition, depth)
            depth += int(self.choices.filter_nests)
        return depth

    def call(self, node, level: int, operator) -> None:
        if self.recurses(node.func):
            self.recursed = True
        self.other_expression(node, level, operator)

    def recurses(self, func) -> bool:
        """A direct call of this function: `name(...)`, `self.name(...)`, `cls.name(...)`
        or, with recursion_scopes, `Class.name(...)` from a method of Class."""
        if isinstance(func, ast.Name):
            return self.reaches(func.id)
        return isinstance(func, ast.Attribute) and self.through_receiver(func)

    def reaches(self, name: str) -> bool:
        """A bare name reaches a def outside a class body that binds no such name."""
        return name == self.fn.name and self.owner is None and name not in self.bound

    def through_receiver(self, func) -> bool:
        receiver = func.value
        return (func.attr == self.fn.name and isinstance(receiver, ast.Name)
                and receiver.id in ("self", "cls", self.owner))

    def other_expression(self, node, level: int, operator) -> None:
        for child in ast.iter_child_nodes(node):
            self.any(child, level)

    # -- the function --------------------------------------------------------------------

    def run(self) -> Count:
        self.recursed = False
        self.body(self.fn.body, 0)
        self.add(int(self.recursed))
        return self.count


def _is_elif(orelse: list, column: int) -> bool:
    return len(orelse) == 1 and isinstance(orelse[0], ast.If) and orelse[0].col_offset == column


def _chain(node) -> list:
    """The operator types of a BoolOp chain in source order, sub-chains inlined."""
    operators = []
    for number, value in enumerate(node.values):
        if number:
            operators.append(type(node.op))
        if isinstance(value, ast.BoolOp):
            operators += _chain(value)
    return operators


def _leaves(node) -> list:
    """The operands of a chain that are not themselves part of it."""
    return [leaf for value in node.values
            for leaf in (_leaves(value) if isinstance(value, ast.BoolOp) else [value])]


_STATEMENTS = {
    ast.If: _Counter.if_statement,
    ast.For: _Counter.loop, ast.AsyncFor: _Counter.loop, ast.While: _Counter.loop,
    **{kind: _Counter.try_statement for kind in TRIES},
    ast.Match: _Counter.match_statement,
    ast.With: _Counter.with_statement, ast.AsyncWith: _Counter.with_statement,
    ast.FunctionDef: _Counter.nested_def, ast.AsyncFunctionDef: _Counter.nested_def,
    ast.ClassDef: _Counter.class_def,
}
_EXPRESSIONS = {
    ast.BoolOp: _Counter.bool_op,
    ast.UnaryOp: _Counter.unary,
    ast.IfExp: _Counter.if_expression,
    ast.Lambda: _Counter.lambda_expression,
    **{kind: _Counter.comprehension for kind in COMPREHENSIONS},
    ast.Call: _Counter.call,
}


def count(fn, choices: Choices = PAPER, owner: str | None = None) -> Count:
    """Cognitive complexity and the deepest nesting level of one def's body.
    `owner` names the class whose body holds the def (owners() finds it)."""
    scoped = owner if choices.recursion_scopes else None
    return _Counter(fn, choices, scoped).run()


@functools.lru_cache(maxsize=8)
def owners(source: str) -> dict:
    """(line, column) of each def that sits directly in a class body: that class's name."""
    return {(node.lineno, node.col_offset): scope.name for scope in ast.walk(ast.parse(source))
            if isinstance(scope, ast.ClassDef) for node in scope.body if isinstance(node, FUNCTIONS)}


def bound_names(fn) -> frozenset:
    """The names fn's own scope binds: parameters, imports, assignment, loop, with,
    except and match targets, nested defs and classes."""
    params = [node.arg for node in ast.walk(fn.args) if isinstance(node, ast.arg)]
    return frozenset(params).union(*map(_binds, _scope_nodes(fn)))


def _scope_nodes(fn):
    """Every node of fn's body outside the scopes nested in it; a nested def or
    class itself is kept, since its name binds here."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPES):
            stack.extend(ast.iter_child_nodes(node))


_BINDERS = {
    ast.Name: lambda node: [node.id] * isinstance(node.ctx, ast.Store),
    ast.alias: lambda node: [(node.asname or node.name).split(".")[0]],
}


def _binds(node) -> list:
    return _BINDERS.get(type(node), _named)(node)


def _named(node) -> list:
    return [node.name] if isinstance(node, NAMED) and node.name else []


def functions(source: str) -> list:
    """Every def node in the module, in ast.walk order."""
    return [node for node in ast.walk(ast.parse(source)) if isinstance(node, FUNCTIONS)]
