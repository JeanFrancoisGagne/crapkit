"""Python functions as the standard library's ast and tokenize modules read them.

This is the expected side of every Python span, name, count and parameter
check. Definitions come from the Python documentation, not from crapkit:

- a function is every `def` and `async def` node, nested ones and methods
  included (https://docs.python.org/3/library/ast.html#ast.FunctionDef);
- its first line is the node's `lineno`, the `def` line, decorators excluded
  (ast docs: "lineno ... of the def keyword" since Python 3.8), and its last
  line is `end_lineno`;
- its parameters are posonlyargs + args + vararg + kwonlyargs + kwarg
  (https://docs.python.org/3/reference/compound_stmts.html#function-definitions:
  the `/` and bare `*` markers are not parameters);
- `qualname` is PEP 3155's __qualname__ (`Class.method`, `outer.<locals>.inner`);
- a body is inline when its first statement starts on the line of the colon
  that ends the signature (tokenize finds that colon at bracket depth 0).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import io
import tokenize

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Fn:
    name: str
    qualname: str
    start: int
    end: int
    params: int
    inline_body: bool
    enclosing: tuple  # (kind, name) of each enclosing def or class, outermost first


def parses(source: str) -> bool:
    """Whether the source is a complete Python module."""
    try:
        ast.parse(normalized(source))
    except (SyntaxError, ValueError):
        return False
    return True


def param_count(node) -> int:
    args = node.args
    starred = [arg for arg in (args.vararg, args.kwarg) if arg is not None]
    return len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs) + len(starred)


def _qualname(enclosing: tuple, name: str) -> str:
    parts = []
    for kind, outer in enclosing:
        parts += [outer, "<locals>"] if kind == "def" else [outer]
    return ".".join([*parts, name])


_OPEN, _CLOSE = "([{", ")]}"


def normalized(source: str) -> str:
    """Line ends as the compiler reads them: CRLF and a lone CR are each one LF.
    Every function here reads this form, so ast and tokenize count one set of lines."""
    return source.replace("\r\n", "\n").replace("\r", "\n")


def _line_offsets(source: str) -> list[int]:
    """The offset each line starts at; only LF ends a line (a form feed does not)."""
    offsets, total = [], 0
    for line in source.split("\n"):
        offsets.append(total)
        total += len(line) + 1
    return offsets


_KINDS = {(tokenize.NAME, "def"): "def", (tokenize.OP, ":"): "colon",
          **{(tokenize.OP, bracket): "bracket" for bracket in _OPEN + _CLOSE}}


def _kind(token) -> str | None:
    return _KINDS.get((token.type, token.string))


class _Colons:
    """One tokenize pass: {(line, column) where a def node starts: (line of the
    colon ending its signature, offset just past that colon)}. An `async def`
    is keyed at `async`, where ast puts the node."""

    def __init__(self, source: str):
        self.offsets = _line_offsets(source)
        self.depth, self.stack, self.found, self.last = 0, [], {}, None

    def feed(self, token) -> None:
        kind = _kind(token)
        if kind:
            getattr(self, "_" + kind)(token)
        self.last = token

    def _def(self, token) -> None:
        after_async = self.last is not None and self.last.string == "async"
        self.stack.append((self.last.start if after_async else token.start, self.depth))

    def _bracket(self, token) -> None:
        self.depth += 1 if token.string in _OPEN else -1

    def _colon(self, token) -> None:
        if self.stack and self.stack[-1][1] == self.depth:
            start, _ = self.stack.pop()
            line, column = token.end
            self.found[start] = (line, self.offsets[line - 1] + column)


def signature_colons(source: str) -> dict:
    """The _Colons map of a normalized source."""
    colons = _Colons(source)
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        colons.feed(token)
    return colons.found


def _walk(node, enclosing: tuple, colons: dict):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, FUNCTIONS):
            yield _fn(child, enclosing, colons)
            yield from _walk(child, enclosing + (("def", child.name),), colons)
        elif isinstance(child, ast.ClassDef):
            yield from _walk(child, enclosing + (("class", child.name),), colons)
        else:
            yield from _walk(child, enclosing, colons)


def _fn(node, enclosing: tuple, colons: dict) -> Fn:
    colon_line = colons[(node.lineno, node.col_offset)][0]
    inline = colon_line == node.body[0].lineno
    return Fn(node.name, _qualname(enclosing, node.name), node.lineno, node.end_lineno,
              param_count(node), inline, enclosing)


def functions(source: str) -> list[Fn]:
    """Every def in source order (a parent before the defs nested in it)."""
    source = normalized(source)
    tree = ast.parse(source)
    return sorted(_walk(tree, (), signature_colons(source)), key=lambda fn: (fn.start, fn.end))


def enclosing_defs_name(fn: Fn) -> str:
    """crapkit's documented Python name (docs/upgrading.md "Analysis version 11":
    a nested def names each enclosing def once, `a.b.c`), which leaves classes
    out; rulings.tsv AO-PY-NAME records that transform of `qualname`."""
    return ".".join([name for kind, name in fn.enclosing if kind == "def"] + [fn.name])


def signature_spans(source: str) -> list[tuple[int, int]]:
    """(offset of the def node, offset just past its signature's colon) for each
    def: a file cut anywhere in between ends inside that def's signature.
    Offsets index normalized(source)."""
    source = normalized(source)
    offsets, colons = _line_offsets(source), signature_colons(source)
    nodes = [node for node in ast.walk(ast.parse(source)) if isinstance(node, FUNCTIONS)]
    return sorted((offsets[node.lineno - 1] + node.col_offset,
                   colons[(node.lineno, node.col_offset)][1]) for node in nodes)
