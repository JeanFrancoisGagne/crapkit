"""Non-comment lines of code per Python def, counted with the standard
library's tokenize.

lizard's README defines NLOC as lines of code without comments. A line counts
here when any code token covers it: any token besides a comment, a line
break, an indent or a dedent (https://docs.python.org/3/library/tokenize.html);
a string literal spanning lines covers each of them. Two transforms map this
onto crapkit's documented column, each a rulings row pinned by a hand probe:

- AO-PY-NLOC-DOCSTRING: a triple-quoted string that stands alone as a
  statement (a docstring, https://peps.python.org/pep-0257/, or any other) is
  not code, as lizard's Python reader reads it as a comment (python.py
  process_token); a one-quote string is code;
- AO-PY-NLOC-NESTED-A/-B: a def nested inside another counts on its own row
  from the line after its `def` keyword's: the enclosing def keeps that one
  line (a one-line nested def stays whole in the enclosing def).

No crapkit import.
"""
from __future__ import annotations

import ast
import io
import tokenize

from accuracy.analysis_oracles.oracles import py_ast_oracle

SKIP = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
        tokenize.ENDMARKER, tokenize.ENCODING}
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def code_lines(source: str) -> set[int]:
    """Every line some code token covers."""
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return {line for token in tokens if token.type not in SKIP
            for line in range(token.start[0], token.end[0] + 1)}


TRIPLE = ('"""', "'''")


def _triple_quoted(node, text: list[str]) -> bool:
    start = text[node.lineno - 1][node.col_offset:].lstrip("rRuUbB")
    return start.startswith(TRIPLE)


def _bare_string(node, text: list[str]) -> bool:
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str) and _triple_quoted(node, text))


def _string_lines(fn, text: list[str]) -> set[int]:
    """The lines of each triple-quoted string that stands alone as a statement."""
    return {line for node in ast.walk(fn) if _bare_string(node, text)
            for line in range(node.lineno, node.end_lineno + 1)}


def _inner_defs(fn) -> list:
    return [node for node in ast.walk(fn) if isinstance(node, FUNCTIONS) and node is not fn]


def _nested_lines(fn) -> set[int]:
    return {line for node in _inner_defs(fn) for line in range(node.lineno + 1, node.end_lineno + 1)}


def nloc(fn, lines: set[int], text: list[str]) -> int:
    """fn's code lines, its standalone strings and its nested defs' bodies left
    out. `text` is the source's lines."""
    span = set(range(fn.lineno, fn.end_lineno + 1)) & lines
    return len(span - _string_lines(fn, text) - _nested_lines(fn))


def per_def(source: str) -> list[tuple]:
    """(def node, its nloc) for every def of a source."""
    source = py_ast_oracle.normalized(source)
    lines, text = code_lines(source), source.split("\n")
    return [(node, nloc(node, lines, text)) for node in ast.walk(ast.parse(source))
            if isinstance(node, FUNCTIONS)]
