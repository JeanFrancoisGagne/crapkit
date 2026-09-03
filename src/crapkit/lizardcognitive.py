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

Three language-specific rules:
  * in C/C++ and Objective-C/C++ a `&&` before the function's opening brace
    declares an rvalue reference rather than deciding anything, and costs
    nothing. See `_declarator_and`.
  * in Rust a `match` is a switch and is charged as one, +1 and +nesting with
    the arms free. It is read only for the Rust readers because `match` is a
    soft keyword in Python, where the same spelling is an ordinary identifier.
    See `_counting_match`.
  * in shell a block is delimited by words, not by braces or by indent: `if`
    and `case` open, `fi`, `done` and `esac` close, and `do`, `then` and `in`
    only introduce the body of a structure already charged. See
    `_shell_keywords`.

Attribution follows lizard's function splitting (a nested arrow's tokens are
the arrow's), exactly as ccn is attributed today. Ternary branches do not
deepen nesting (a structure inside a ternary arm is rare enough to accept).

The deepest the per-function stack gets is recorded too, as
`cognitive_nesting`. analyze.py reads it as the `nesting` column of a Python
row, because lizard's ND extension counts structures there rather than depth:
a flat function of seven `if`s read 7. Brace languages keep lizard's column.

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

# The two readers whose files can spell a declarator `&&`: CLikeReader for
# C and C++, ObjCReader for `.m` and `.mm`, where Objective-C++ carries C++
# move semantics wholesale. Exact names, not an issubclass test: JavaReader,
# CSharpReader and TTCNReader inherit from CLikeReader too, and in those three
# languages every `&&` is an operator, so a rule reaching them could only ever
# lose a real one.
_DECLARATOR_READERS = frozenset({"CLikeReader", "ObjCReader"})

# The readers whose `match` is Rust's switch: lizard's own, and crapkit's
# subclass of it. Exact names for the same reason `_DECLARATOR_READERS` uses
# them — the discriminator is the language, and an issubclass test would also
# catch anything a later lizard derives from RustReader for another one.
_MATCH_READERS = frozenset({"RustReader", "CorrectedRustReader"})

# The reader whose blocks are delimited by words. Exact name for the same reason
# the two sets above use exact names: the discriminator is the language.
_SHELL_READERS = frozenset({"ShellReader"})

# Shell's block openers. `until` and `select` are here and not in `_COUNTING`
# because no other language crapkit reads spells a loop that way; `case` is
# shell's switch and is charged like one, +1 and the nesting it sits in, with
# the arms free. `elif` is absent on purpose: `_ELSE_KEYWORDS` is tested first
# and gives it the flat +1 an else-if link is worth, and the `if` it continues
# still owns the one block that `fi` closes.
_SHELL_COUNTING = frozenset({"if", "for", "while", "until", "select", "case"})

# The words that close what `_SHELL_COUNTING` opened. Without them a shell
# function's nesting never rose: a 4-deep `if` scored 4 where the same shape
# scored 10 in Python, TypeScript, PowerShell and Rust.
_SHELL_CLOSERS = frozenset({"fi", "done", "esac"})

# `do`, `then` and `in` introduce the body of a structure already charged. `do`
# is in `_COUNTING` for C-family do-while, and reading it here too charged every
# shell loop twice.
_SHELL_BODY_WORDS = frozenset({"do", "then", "in"})

# What a shell block puts on the nesting stack. The brace rules read a stack
# entry as a brace depth and the python rules as an indent; None is neither, so
# a `}` inside a shell function cannot pop a block that `fi` owns.
_SHELL_BLOCK = None


class _FnState:
    __slots__ = ("total", "stack", "max_depth", "brace_depth", "line_indent",
                 "at_line_start", "pending", "else_pending", "question_pending",
                 "bool_op", "name", "recursed", "body_started", "prev",
                 "label_check", "c_family", "match_kw", "is_shell")

    def __init__(self, name: str, c_family: bool = False, match_kw: bool = False,
                 is_shell: bool = False):
        self.c_family = c_family
        self.match_kw = match_kw
        self.is_shell = is_shell
        self.total = 0
        self.stack = []          # (entry_brace_depth) or python header indents
        self.max_depth = 0       # the deepest the stack has been
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

    FUNCTION_INFO = {"cognitive_complexity": {"caption": " Cog "},
                     "cognitive_nesting": {"caption": " Nest "}}

    def __call__(self, tokens, reader):
        # Keyed on the FunctionInfo itself, never on id(fn): the map would hold no
        # reference, a finished function's address would be recycled under a later
        # one, and that one would inherit a stranger's running total. Measured on
        # the consumer repo: 377-379 rows moved between two runs of the same commit.
        states: dict[object, _FnState] = {}
        reader_name = type(reader).__name__
        is_python = reader_name.lower().startswith("python")
        flags = (reader_name in _DECLARATOR_READERS, reader_name in _MATCH_READERS,
                 reader_name in _SHELL_READERS)
        last = None
        for token in tokens:
            if is_python:
                yield token  # the owner is read after lizard has; see _state_for
            fn = reader.context.current_function
            state = last = _state_for(states, fn, last, flags)
            _step(state, token, is_python)
            fn.cognitive_complexity = state.total
            fn.cognitive_nesting = state.max_depth
            if not is_python:
                yield token


def _state_for(states: dict, fn, last, flags: tuple) -> _FnState:
    """The state that owns this token, standing where the stream stands.

    A Python token's owner is read AFTER the token is yielded (see __call__).
    lizard's PythonReader closes a nested `def` on the first token of the line
    that dedents past it, inside its own `preprocess`, which sits behind this
    extension in the chain; read before the yield, that token still names the
    inner function, the outer function's own `if` on that line is stepped
    under the inner, and the outer resumes one level short with its next `if`
    read as an inline one. Measured before the fix: a helper followed by two
    nested `if`s read outer nesting 1, cognitive 1 and inner 1, 1; the outer
    owns both (2, 3) and the inner nothing. The same token closes the last
    function of a module: a module-level `if __name__` cost it a point and a
    level, and `register()` called at import right after `def register()`
    read as recursion (six rows of crapkit's own 5,258). Brace languages keep
    step-then-yield, because their function ends on the `}` the state must
    still see.

    Line position belongs to the stream, not to a function: the newline and
    indent tokens that opened the line were stepped under whichever function
    lizard named at the time. So a state that was not the last one stepped
    takes the last one's position, or the outer resumes with a stale
    `at_line_start` and reads its `if` as the ternary form, which opens nothing.
    """
    state = states.get(fn)
    if state is None:
        state = states[fn] = _FnState(getattr(fn, "name", ""), *flags)
    if last is not None and state is not last:
        state.line_indent = last.line_indent
        state.at_line_start = last.at_line_start
    return state


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
    if _is_label(state, token):
        state.total += 1  # break/continue TO A LABEL


def _is_label(state: _FnState, token: str) -> bool:
    """Whether the token after a break/continue names something to jump to.

    Shell has no labels. It spells the same jump `break 2`, a count of enclosing
    loops to leave, and a bare `break` is followed by whatever the loop is
    followed by. Without the digit test every `break` before a `fi` or a `done`
    read as a label, and a loop-with-break scored one more in shell than the
    same loop scored in TypeScript.
    """
    if state.is_shell:
        return token.isdigit()
    return token not in (";", "}", ")") and bool(token.strip())


def _nesting(state: _FnState, is_python: bool) -> int:
    return len(state.stack)


def _push(state: _FnState, entry) -> None:
    """One more open block. Every push passes through here, so the deepest the
    stack gets is measured once, in one place."""
    state.stack.append(entry)
    state.max_depth = max(state.max_depth, len(state.stack))


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
        _push(state, state.brace_depth)
        state.pending = False
    state.brace_depth += 1


def _close_brace(state: _FnState) -> None:
    state.brace_depth -= 1
    if state.stack and state.stack[-1] == state.brace_depth:
        state.stack.pop()


def _declarator_and(state: _FnState, token: str) -> bool:
    """True for a C++ or Objective-C++ `&&` that declares instead of deciding.

    `T &&t` and `a && b` tokenize identically, so only position separates them.
    A C++ function's declarator — its parameter list, its `&&` ref-qualifier, the
    name of an `operator&&`, its member-initializer list — is everything before
    the body's opening brace, and no statement can live there. Brace depth 0 in a
    function lizard has already named IS that region.

    A logical `&&` inside a default argument reads as a declarator here and
    loses its point. lizard's cyclomatic column drops the whole parameter list
    too (measured: `bool f(bool a = (kP && kQ))` reads ccn 1), so this makes the
    two columns agree about that region rather than disagreeing about it.

    `||` is left alone. Only `&&` doubles as a type declarator, so the one
    spelling this misses is the NAME of an `operator||` overload, which still
    costs 1.
    """
    return state.c_family and token == "&&" and state.brace_depth == 0


def _bool_op(state: _FnState, token: str) -> None:
    if _declarator_and(state, token):
        return
    op = {"and": "&&", "or": "||"}.get(token, token)
    if op != state.bool_op:
        state.total += 1
        state.bool_op = op


def _keywords(state: _FnState, token: str, is_python: bool) -> None:
    if state.is_shell:
        _shell_keywords(state, token, is_python)
    elif token == "if":
        _if_token(state, is_python)
    elif token in _ELSE_KEYWORDS:
        _else_token(state, token, is_python)
    elif token in _COUNTING or _counting_match(state, token):
        _structure_token(state, token, is_python)
    else:
        _jumps_and_recursion(state, token, is_python)


def _shell_keywords(state: _FnState, token: str, is_python: bool) -> None:
    """Shell's block structure, which is words rather than braces or indent.

    `if` and `case` open a block and every loop keyword opens one; `fi`, `done`
    and `esac` close it. Nothing else can, so the brace rules never see a shell
    block and the stack would otherwise stay empty for a whole function: a
    4-deep `if` scored 4 against 10 everywhere else.

    `else` and `elif` pay the flat +1 of an else-if link and open nothing. One
    `fi` closes the whole chain, so the `if` that opened it is what the chain
    nests inside, and a push here would leak a level past the `fi`.

    `do`, `then` and `in` are free. They introduce the body of a structure this
    function has already charged, and `do` sits in `_COUNTING` for C-family
    do-while, which charged every shell loop a second time.
    """
    if token in _SHELL_CLOSERS:
        _shell_close(state)
    elif token in _ELSE_KEYWORDS:
        state.total += 1
    elif token in _SHELL_COUNTING:
        state.total += 1 + _nesting(state, is_python)
        _push(state, _SHELL_BLOCK)
    elif token not in _SHELL_BODY_WORDS:
        _jumps_and_recursion(state, token, is_python)


def _shell_close(state: _FnState) -> None:
    """A closer with nothing open is a `fi` whose `if` sits outside this function
    (lizard attributes tokens by function, not by block), and pops nothing."""
    if state.stack:
        state.stack.pop()


def _counting_match(state: _FnState, token: str) -> bool:
    """True for a Rust `match`, which is a switch and is charged as one.

    Not in `_COUNTING`, because that set is read by every language and `match`
    is a soft keyword in Python: `match = re.match(...)` would cost a point and
    open a block that never closes. The reader decides, the way it decides
    whether a `&&` is a declarator.

    The block itself pays +1 and the nesting it sits in; the arms pay nothing,
    exactly as a C `case` pays nothing. Rust's cyclomatic column counts the arms
    instead, so the two columns say different things about one block on purpose.
    """
    return token == "match" and state.match_kw


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
        _push(state, state.line_indent)
    else:
        state.pending = True
