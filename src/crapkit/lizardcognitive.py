"""Cognitive complexity (Sonar spec) as a lizard token-stream extension.

Rides lizard's language-aware tokenizers, so TS/TSX/JS/Python/Swift all pay the
same rules with no second parse and no new dependency:
  +1 and +nesting for if / ternary / switch / loops / catch-except
  +1 flat for else / elif / elseif (an else-if chain costs one per link, no
  deepening)
  +1 per boolean-operator run, +1 each time the operator alternates
  +1 for a labeled break/continue or goto, +1 once for direct recursion
  try / finally / case labels / with are free; nesting rises inside the
  block structures listed above.

Attribution follows lizard's function splitting (a nested arrow's tokens are
the arrow's), exactly as ccn is attributed today. Ternary branches do not
deepen nesting (a structure inside a ternary arm is rare enough to accept).

Where this extension sits in lizard's chain is load-bearing and differs by
reader: the python rules read whitespace tokens that lizard's own
`preprocessing` strips, while SwiftReader and KotlinReader drain the stream
inside their `preprocess` and starve anything placed ahead of it. analyze._chain
owns that placement.

The whitepaper's worked examples in tests/unit/test_cognitive.py are the spec.
"""
from __future__ import annotations

_COUNTING = frozenset({"if", "for", "foreach", "while", "do", "catch", "except", "switch"})

# `-and` and `-or` are PowerShell's, where `&&` and `||` chain pipelines instead.
# They reach here as single tokens only because crapkit's PowerShell reader adds
# a `-\w+` rule to lizard's shared pattern; every other reader splits them into
# `-` and a word, so widening this set moves no other language's score.
_BOOL_OPS = frozenset({"&&", "||", "??", "and", "or", "-and", "-or"})

# The keywords that pay a FLAT +1 with no nesting increment, which is what the
# whitepaper gives an else-if link. `elseif` is one word in PowerShell (and in
# PHP); it belongs here rather than in `_COUNTING`, where it would be charged
# +1 + nesting and a chain inside a loop would cost more than the same chain at
# the top of the function.
_ELSE_KEYWORDS = frozenset({"else", "elif", "elseif"})

_RUN_RESETS = frozenset({";", ",", "{", "}"})


class _FnState:
    __slots__ = ("total", "stack", "brace_depth", "line_indent", "at_line_start",
                 "pending", "else_pending", "question_pending", "bool_op", "name",
                 "recursed", "body_started", "prev", "label_check")

    def __init__(self, name: str):
        self.total = 0
        self.stack = []          # (entry_brace_depth) or python header indents
        self.brace_depth = 0
        self.line_indent = 0
        self.at_line_start = True
        self.pending = False     # a counting structure awaits its '{'
        self.else_pending = False
        self.question_pending = False
        self.bool_op = None
        self.name = name
        self.recursed = False
        self.body_started = False
        self.prev = ""
        self.label_check = False  # just saw break/continue


class LizardExtension:
    """One instance per analysis pass; state is per lizard FunctionInfo."""

    FUNCTION_INFO = {"cognitive_complexity": {"caption": " Cog "}}

    def __call__(self, tokens, reader):
        # Keyed on the FunctionInfo itself, never on id(fn): the map would hold no
        # reference, a finished function's address would be recycled under a later
        # one, and that one would inherit a stranger's running total. Measured on
        # the consumer repo: 377-379 rows moved between two runs of the same commit.
        states: dict[object, _FnState] = {}
        is_python = type(reader).__name__.lower().startswith("python")
        for token in tokens:
            fn = reader.context.current_function
            state = states.get(fn)
            if state is None:
                state = states[fn] = _FnState(getattr(fn, "name", ""))
            _step(state, token, is_python)
            fn.cognitive_complexity = state.total
            yield token


def _step(state: _FnState, token: str, is_python: bool) -> None:
    if not token.strip():
        _line_event(state, token, is_python)
        return
    if token.startswith(("#", "//", "/*")):
        return  # a comment token must never read as code, whatever it contains
    if is_python and state.at_line_start:
        _python_dedent(state)
    if _resolve_lookbehinds(state, token, is_python):
        state.prev = token
        state.at_line_start = False
        return
    _consume(state, token, is_python)
    state.prev = token
    state.at_line_start = False


def _line_event(state: _FnState, token: str, is_python: bool) -> None:
    """Whitespace arrives split ('\\n' then '    '): the newline opens the line,
    later whitespace extends its indent, and the dedent settles only when the
    first real token of the line arrives."""
    if "\n" in token:
        state.at_line_start = True
        state.bool_op = None
        state.line_indent = len(token) - token.rfind("\n") - 1
        state.body_started = state.body_started or is_python
    elif state.at_line_start:
        state.line_indent += len(token)


def _python_dedent(state: _FnState) -> None:
    while state.stack and state.line_indent <= state.stack[-1]:
        state.stack.pop()


def _resolve_lookbehinds(state: _FnState, token: str, is_python: bool) -> bool:
    """Signals needing one token of hindsight. True = this token is consumed."""
    if state.question_pending:
        _resolve_question(state, token, is_python)
    if state.label_check:
        _resolve_label(state, token)
    if state.else_pending:
        state.else_pending = False
        state.pending = True
        # else-if: the else already paid the flat +1; this if only opens the block
        return token == "if"
    return False


def _resolve_question(state: _FnState, token: str, is_python: bool) -> None:
    state.question_pending = False
    if token not in (".", ":", ")"):  # optional chaining / optional type / trailing
        state.total += 1 + _nesting(state, is_python)


def _resolve_label(state: _FnState, token: str) -> None:
    state.label_check = False
    if token not in (";", "}", ")") and token.strip():
        state.total += 1  # break/continue TO A LABEL


def _nesting(state: _FnState, is_python: bool) -> int:
    return len(state.stack)


def _consume(state: _FnState, token: str, is_python: bool) -> None:
    if token in _RUN_RESETS:
        state.bool_op = None
    if token in ("{", "}"):
        _brace(state, token)
        return
    if is_python and not state.body_started:
        return  # tokens of the def header line never count
    if token in _BOOL_OPS:
        _bool_op(state, token)
        return
    _keywords(state, token, is_python)


def _brace(state: _FnState, token: str) -> None:
    if token == "{":
        _open_brace(state)
    else:
        _close_brace(state)


def _open_brace(state: _FnState) -> None:
    if state.pending:
        state.stack.append(state.brace_depth)
        state.pending = False
    state.brace_depth += 1


def _close_brace(state: _FnState) -> None:
    state.brace_depth -= 1
    if state.stack and state.stack[-1] == state.brace_depth:
        state.stack.pop()


def _bool_op(state: _FnState, token: str) -> None:
    op = {"and": "&&", "or": "||"}.get(token, token)
    if op != state.bool_op:
        state.total += 1
        state.bool_op = op


def _keywords(state: _FnState, token: str, is_python: bool) -> None:
    if token == "if":
        _if_token(state, is_python)
    elif token in _ELSE_KEYWORDS:
        _else_token(state, token, is_python)
    elif token in _COUNTING:
        _structure_token(state, token, is_python)
    else:
        _jumps_and_recursion(state, token, is_python)


def _structure_token(state: _FnState, token: str, is_python: bool) -> None:
    if token == "while" and state.prev == "}":
        return  # the closing half of do-while; the do already paid
    _structure(state, token, is_python)


def _jumps_and_recursion(state: _FnState, token: str, is_python: bool) -> None:
    if token == "?":
        state.question_pending = True
    elif token in ("break", "continue"):
        state.label_check = not is_python
    elif token == "goto":
        state.total += 1
    elif _is_recursion(state, token):
        state.recursed = True
        state.total += 1


def _is_recursion(state: _FnState, token: str) -> bool:
    return (token == state.name and bool(state.name) and not state.recursed
            and (state.body_started or state.brace_depth > 0))


def _if_token(state: _FnState, is_python: bool) -> None:
    if is_python and not state.at_line_start:
        state.total += 1 + _nesting(state, is_python)  # ternary expression form
        return
    state.total += 1 + _nesting(state, is_python)
    _push_structure(state, is_python)


def _else_token(state: _FnState, token: str, is_python: bool) -> None:
    if is_python and not state.at_line_start:
        return  # the else arm of a ternary expression is part of its +1
    state.total += 1
    if token != "else":
        # `elif` and `elseif` carry their own condition and their own block, so
        # the block opens here; a bare `else` has to wait one token to find out
        # whether an `if` follows it.
        _push_structure(state, is_python)
    else:
        state.else_pending = not is_python
        if is_python:
            _push_structure(state, is_python)


def _structure(state: _FnState, token: str, is_python: bool) -> None:
    state.total += 1 + _nesting(state, is_python)
    _push_structure(state, is_python)


def _push_structure(state: _FnState, is_python: bool) -> None:
    if is_python:
        state.stack.append(state.line_indent)
    else:
        state.pending = True
