"""The Python line and parameter shapes crapkit is known to misread, one
detector per rulings row, for the nloc and params differentials.

A differential compares crapkit with tokenize or ast only where no open
defect applies; each detector names the rulings row whose strict-xfail probe
pins the defect, and the differential counts how many defs it set aside.

- AO-PY-NLOC-FSTRING: an f-string that spans lines drops lines from nloc;
- AO-PY-NLOC-TRIPLE: a triple-quoted string inside an expression is read as
  a comment unless the token before it is one of lizard's expression tokens
  (python.py process_token), so its lines drop out of nloc;
- AO-PY-NLOC-PREFIX: a prefixed triple-quoted string (r, b, u) inside an
  expression drops out of nloc whatever token comes before it;
- AO-PY-PARAMS-DEFAULTS: a parameter list with a bracket after its first
  `=`, or a parameter with a default whose annotation holds a bracket, is cut
  short or split.

No crapkit import.
"""
from __future__ import annotations

import ast
import io
import tokenize

from accuracy.analysis_oracles.oracles import py_ast_oracle

SKIP = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}
# The tokens after which lizard's Python reader keeps a triple-quoted string as code.
EXPRESSION_TOKENS = frozenset({"=", "+=", "-=", "*=", "/=", "%=", "//=", "**=", "&=", "|=",
                               "^=", "<<=", ">>=", "(", "return", ",", "[", "+", "-", "*",
                               "/", "%"})
FSTRING_START = getattr(tokenize, "FSTRING_START", -1)
FSTRING_END = getattr(tokenize, "FSTRING_END", -1)
BRACKETS = "([{"


def _prefix(text: str) -> str:
    return text[:len(text) - len(text.lstrip("rRuUbBfFtT"))].lower()


def _triple(text: str) -> bool:
    return text[len(_prefix(text)):].startswith(('"""', "'''"))


def _standalone_lines(tree) -> set[int]:
    return {node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)}


def _tokens(source: str) -> list:
    return list(tokenize.generate_tokens(io.StringIO(source).readline))


def _inner_triple(token, standalone: set[int]) -> bool:
    return (token.type == tokenize.STRING and _triple(token.string)
            and token.start[0] not in standalone)


def _pairs(source: str) -> list:
    tokens = [token for token in _tokens(source) if token.type not in SKIP]
    return list(zip(tokens, tokens[1:]))


def dropped_triple_lines(source: str, tree) -> set[int]:
    """Start lines of unprefixed triple-quoted strings inside an expression
    whose previous token lizard does not read as an expression token."""
    standalone = _standalone_lines(tree)
    return {token.start[0] for before, token in _pairs(source)
            if _inner_triple(token, standalone) and not _prefix(token.string)
            and before.string not in EXPRESSION_TOKENS}


def prefixed_triple_lines(source: str, tree) -> set[int]:
    """Start lines of prefixed triple-quoted strings inside an expression."""
    standalone = _standalone_lines(tree)
    return {token.start[0] for _, token in _pairs(source)
            if _inner_triple(token, standalone) and _prefix(token.string)}


def _one_token_fstring(token) -> bool:
    return (token.type == tokenize.STRING and "f" in _prefix(token.string)
            and token.start[0] != token.end[0])


def fstring_lines(source: str) -> set[int]:
    """Start lines of the f-strings that span lines: one STRING token before
    Python 3.12, an FSTRING_START..FSTRING_END run after."""
    lines, opened = set(), []
    for token in _tokens(source):
        if token.type == FSTRING_START:
            opened.append(token.start[0])
        elif token.type == FSTRING_END:
            _close(opened, token, lines)
        elif _one_token_fstring(token):
            lines.add(token.start[0])
    return lines


def _close(opened: list[int], token, lines: set[int]) -> None:
    """An outermost f-string that ends on another line than it started."""
    start = opened.pop()
    if not opened and start != token.end[0]:
        lines.add(start)


class Lines:
    """The per-source line sets the nloc detectors read."""

    def __init__(self, source: str):
        source = py_ast_oracle.normalized(source)
        tree = ast.parse(source)
        self.marked = {"AO-PY-NLOC-TRIPLE": dropped_triple_lines(source, tree),
                       "AO-PY-NLOC-PREFIX": prefixed_triple_lines(source, tree),
                       "AO-PY-NLOC-FSTRING": fstring_lines(source)}

    def reasons(self, fn) -> list[str]:
        span = range(fn.lineno, fn.end_lineno + 1)
        return sorted(ruling for ruling, lines in self.marked.items()
                      if any(line in span for line in lines))


def _signature(fn, source: str, colons: dict, offsets: list[int]) -> str:
    start = offsets[fn.lineno - 1] + fn.col_offset
    return source[start:colons[(fn.lineno, fn.col_offset)][1]]


def _bracket_after_default(signature: str) -> bool:
    inner = signature[signature.index("(") + 1:]
    equals = inner.find("=")
    return equals >= 0 and any(bracket in inner[equals:] for bracket in BRACKETS)


def _defaulted(fn) -> list:
    args = fn.args
    positional = args.posonlyargs + args.args
    tail = positional[len(positional) - len(args.defaults):] if args.defaults else []
    keyword = [arg for arg, default in zip(args.kwonlyargs, args.kw_defaults) if default]
    return tail + keyword


def _annotation_text(arg, source: str) -> str:
    return ast.get_source_segment(source, arg.annotation) or "" if arg.annotation else ""


def _bracketed_annotation(fn, source: str) -> bool:
    texts = [_annotation_text(arg, source) for arg in _defaulted(fn)]
    return any(bracket in text for text in texts for bracket in BRACKETS)


def params_shape(fn, source: str, colons: dict, offsets: list[int]) -> bool:
    """AO-PY-PARAMS-DEFAULTS: the shapes that cut or split the parameter list."""
    signature = _signature(fn, source, colons, offsets)
    return _bracket_after_default(signature) or _bracketed_annotation(fn, source)
