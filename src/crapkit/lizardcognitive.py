"""Cognitive complexity (Sonar spec) as a lizard token-stream extension.

Rides lizard's language-aware tokenizers, so TS/TSX/JS/Python/Swift all pay the
same rules with no second parse and no new dependency:
  +1 and +nesting for if / ternary / switch / loops / catch-except
  +1 flat for else / elif / elseif (an else-if chain costs one per link, no
  deepening)
  +1 per boolean-operator run, +1 each time the operator alternates
  +1 for a labeled break/continue or goto, +1 once when the body calls the
  function itself (see `_recursion` for what a call is in each language)
  try / finally / case labels / with are free; nesting rises inside the
  block structures listed above.

Four language-specific rules:
  * in C/C++ and Objective-C/C++ a `&&` before the function's opening brace
    declares an rvalue reference rather than deciding anything, and costs
    nothing. See `_declarator_and`. Past the brace, the C family's reader
    respells a declarator `&&` before this pass sees it.
  * Rust keeps its own structures. `match` is a switch and `loop` a loop, each
    +1 and +nesting with the arms free, and `catch`, `switch`, `foreach`, `do`
    and `except` name nothing. `?` is no ternary: it returns early on an error
    or relaxes a bound, and neither is an increment. A `for` is a loop unless
    a `<` follows it (a `for<'a>` binder) or the trait's name or `>` before
    it makes it the `for` of `impl Trait for Type`; see `_resolve_for`. These
    rules are read only for the Rust readers because `match` is a soft keyword
    in Python and the rest are keywords elsewhere. See `_counting`.
  * in shell a block is delimited by words, not by braces or by indent: `if`
    and `case` open, `fi`, `done` and `esac` close, and `do`, `then` and `in`
    only introduce the body of a structure already charged. See
    `_shell_keywords`.
  * in Zig `||` merges two error sets, a type, and boolean or is spelled `or`,
    so `||` costs nothing. See `_error_set_merge`.

A `?` is a conditional operator only where the reader counts one in ccn, read
off its condition set (`reader.conditions`) when the `?` arrives. Zig's `?`
marks an optional and its reader counts none. Shell's is a glob, except inside
arithmetic, `(( ))` and `$(( ))`, where the shell reader adds `?` to that set
for as long as the arithmetic is open.

A Python def's signature never counts. Its body starts at the token after the
`:` that closes the signature at bracket depth 0, so `def f(x, y): return 1 if
x and y else 2` reads cognitive 2 like its two-line form, and a signature over
several lines reads like the same signature on one. See `_signature_token`. A
Rust fn's signature never counts either: it is every token before the body's
`{`, so a `for<'a>` binder in a where clause is no loop. See `_signature`.

Attribution follows lizard's function splitting (a nested arrow's tokens are
the arrow's), exactly as ccn is attributed today. Ternary branches do not
deepen nesting (a structure inside a ternary arm is rare enough to accept).

The deepest the per-function stack gets is recorded too, as
`cognitive_nesting`. analyze.py reads it as the `nesting` column of a Python or
shell row, because lizard's ND extension closes a level only on a `}` or at a
`;`: a flat Python function of seven `if`s read 7, and seven shell ifs side by
side read 6. Brace languages keep lizard's column.

Where this extension sits in lizard's chain is load-bearing and differs by
reader: the python rules read whitespace tokens that lizard's own
`preprocessing` strips, while SwiftReader and KotlinReader drain the stream
inside their `preprocess` and starve anything placed ahead of it. analyze._chain
owns that placement.

The whitepaper's worked examples in tests/unit/test_cognitive.py are the spec.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from .lizardrust import implements_for

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

# The words after which a shell or PowerShell word is a command. Both languages
# call a function by naming it as a command, with no parentheses, so a function
# calls itself when its name stands here; the same word as an argument (`echo
# fact`, `Write-Output 'Get-Name'`) calls nothing.
_SHELL_LEADS = frozenset({"", ";", "|", "&", "&&", "||", "(", "{", "`", "!", "then", "do",
                          "else", "elif", "if", "while", "until", "time"})
_POWERSHELL_LEADS = frozenset({"", ";", "|", "&", "(", "{", "=", "return", "in", "throw"})

# Rust's structures, in place of `_COUNTING`. `match` is Rust's switch and
# `loop` its unconditional loop, and neither is in `_COUNTING`: `match` is a
# soft keyword in Python and `loop` a name elsewhere. `catch`, `foreach`, `do`,
# `except` and `switch` are left out because Rust names nothing with them, so a
# method `.switch()` or a variable `catch` read as a structure.
_RUST_COUNTING = frozenset({"for", "while", "loop", "match"})

# The condition set of a reader that has none: a `?` is a conditional operator.
_QUESTION = frozenset({"?"})


class _Dialect(NamedTuple):
    """What one language's tokens mean to the rules below.

    `declarator_and`: a `&&` before the body's brace declares an rvalue
    reference (C, C++, Objective-C++). `shell_blocks`: blocks are delimited by words (shell).
    `messages`: a call to a method is a message, `[self sel:arg]`
    (Objective-C). `command_leads`: a call is the function's name as a
    command, after one of these words (shell, PowerShell), and `fold_case`
    says the name matches in any case (PowerShell). `rust`: Rust's own syntax, a
    `for` that loops over nothing, a signature that never counts and a `?` that
    is no conditional (see _resolve_for, _signature, _counts_question).
    `error_sets`: `||` merges two error sets (Zig; see _error_set_merge).
    `conditions`: the reader's condition set, read at each `?`, and set from the
    reader for each file (see LizardExtension.__call__).
    """

    declarator_and: bool = False
    shell_blocks: bool = False
    messages: bool = False
    command_leads: frozenset | None = None
    fold_case: bool = False
    rust: bool = False
    error_sets: bool = False
    conditions: frozenset = _QUESTION


# Keyed on the reader's exact class name, never on an issubclass test: JavaReader,
# CSharpReader and TTCNReader inherit from CLikeReader, and in those languages
# every `&&` is an operator, so the declarator rule reaching them could only ever
# lose a real one. crapkit's CorrectedRustReader sits beside lizard's RustReader
# for the same reason: the discriminator is the language. A reader absent from the
# table reads under the defaults.
_DEFAULT_DIALECT = _Dialect()
_RUST = _Dialect(rust=True)
_ZIG = _Dialect(error_sets=True)
_DIALECTS = {
    "CLikeReader": _Dialect(declarator_and=True),
    "ObjCReader": _Dialect(declarator_and=True, messages=True),
    "RustReader": _RUST,
    "CorrectedRustReader": _RUST,
    "ShellReader": _Dialect(shell_blocks=True, command_leads=_SHELL_LEADS),
    "PowerShellReader": _Dialect(command_leads=_POWERSHELL_LEADS, fold_case=True),
    "ZigReader": _ZIG,
    "CorrectedZigReader": _ZIG,
}

# What a function calls itself through: nothing, one of these receivers, or its
# own qualifier (`Calc::fact`, `K.fact`). A call through any other receiver is
# another object's method with the same name.
_SELF_RECEIVERS = frozenset({"self", "this", "Self", "cls"})
_MEMBER_ACCESS = frozenset({".", "->", "::", "?."})

# How lizard qualifies a name: `detail::pow10`, `K::fact`, `outer.inner`.
_QUALIFIER = re.compile(r"::|\.")

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


# What a bracket does to the depth a Python signature is read at. The signature
# ends at the first `:` with none open, so a colon in a default's lambda, a dict
# default, a slice in an annotation or a type parameter's bound is not the end.
_SIGNATURE_DEPTH = {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}


class _FnState:
    __slots__ = ("total", "stack", "max_depth", "brace_depth", "line_indent",
                 "at_line_start", "pending", "else_pending", "question_pending",
                 "bool_op", "name", "recursed", "body_started", "signature_depth",
                 "prev", "prev2", "label_check", "for_pending", "dialect", "call_pending",
                 "messages")

    def __init__(self, name: str, dialect: _Dialect = _DEFAULT_DIALECT):
        self.dialect = dialect
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
        self.body_started = False  # Python only: past the colon that ends the signature
        self.signature_depth = 0   # brackets open in that signature
        self.prev = ""
        self.prev2 = ""           # the token before prev: a call's receiver
        self.label_check = False  # just saw break/continue
        self.for_pending = False  # Rust only: just saw a `for` that may be a binder's
        self.call_pending = False  # the name was just spelled; a `(` makes it a call
        self.messages = []        # Objective-C: one entry per open `[`, see _message_token


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
        dialect = _DIALECTS.get(reader_name, _DEFAULT_DIALECT)._replace(
            conditions=getattr(reader, "conditions", _QUESTION))
        last = None
        for token in tokens:
            if is_python:
                yield token  # the owner is read after lizard has; see _state_for
            fn = reader.context.current_function
            state = last = _state_for(states, fn, last, dialect)
            _step(state, token, is_python)
            fn.cognitive_complexity = state.total
            fn.cognitive_nesting = state.max_depth
            if not is_python:
                yield token


def _state_for(states: dict, fn, last, dialect: _Dialect) -> _FnState:
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

    The name is read again at every token. lizard builds it while it reads the
    declaration, so the name at a function's first token is often not its name:
    empty in Go and PowerShell, `K::int` for a Java method returning int, the
    class alone for `File File::open(`. By the body it is final.
    """
    state = states.get(fn)
    if state is None:
        state = states[fn] = _FnState("", dialect)
    state.name = getattr(fn, "name", "")
    if last is not None and state is not last:
        state.line_indent = last.line_indent
        state.at_line_start = last.at_line_start
    return state


def _step(state: _FnState, token: str, is_python: bool) -> None:
    if not token.strip():
        _line_event(state, token)
        return
    if token.startswith(("#", "//", "/*")):
        return  # a comment token must never read as code, whatever it contains
    if is_python:
        _python_dedent(state)
    if not _resolve_lookbehinds(state, token, is_python):
        _consume(state, token, is_python)
    state.prev2 = state.prev
    state.prev = token
    state.at_line_start = False


def _line_event(state: _FnState, token: str) -> None:
    """Whitespace arrives split ('\\n' then '    '): the newline opens the line,
    later whitespace extends its indent, and the dedent settles only when the
    first real token of the line arrives."""
    if "\n" in token:
        state.at_line_start = True
        state.bool_op = None
        state.line_indent = len(token) - token.rfind("\n") - 1
    elif state.at_line_start:
        state.line_indent += len(token)


def _python_dedent(state: _FnState) -> None:
    """At a line's first real token, close every block its indent has left."""
    if not state.at_line_start:
        return
    while state.stack and state.line_indent <= state.stack[-1]:
        state.stack.pop()


def _resolve_lookbehinds(state: _FnState, token: str, is_python: bool) -> bool:
    """Signals needing one token of hindsight. True = this token is consumed."""
    if state.for_pending:
        _resolve_for(state, token, is_python)
    if state.question_pending:
        _resolve_question(state, token, is_python)
    if state.label_check:
        _resolve_label(state, token)
    if state.call_pending:
        _resolve_call(state, token)
    if state.else_pending:
        return _resolve_else(state, token)
    return False


def _resolve_else(state: _FnState, token: str) -> bool:
    """The token after a brace language's `else`. True = this token is consumed.

    An else-if: the else already paid the flat +1, and the `if` only opens the
    block. `else =>` is the default prong of a Zig switch, which the switch's
    +1 already covers, so the +1 the `else` paid goes back and nothing opens.
    """
    state.else_pending = False
    if token == "=>":
        state.total -= 1
        return False
    state.pending = True
    return token == "if"


def _resolve_question(state: _FnState, token: str, is_python: bool) -> None:
    state.question_pending = False
    if token not in (".", ":", ")"):  # optional chaining / optional type / trailing
        state.total += 1 + _nesting(state, is_python)


def _resolve_for(state: _FnState, token: str, is_python: bool) -> None:
    """A Rust `for` charged a token late: with a `<` after it, it is a
    `for<'a>` binder, which names a lifetime and loops over nothing."""
    state.for_pending = False
    if token != "<":
        _structure(state, "for", is_python)


def _resolve_label(state: _FnState, token: str) -> None:
    state.label_check = False
    if _is_label(state, token):
        state.total += 1  # break/continue TO A LABEL


def _resolve_call(state: _FnState, token: str) -> None:
    state.call_pending = False
    _count_recursion(state, token == "(")


def _is_label(state: _FnState, token: str) -> bool:
    """Whether the token after a break/continue names something to jump to.

    Shell has no labels. It spells the same jump `break 2`, a count of enclosing
    loops to leave, and a bare `break` is followed by whatever the loop is
    followed by. Without the digit test every `break` before a `fi` or a `done`
    read as a label, and a loop-with-break scored one more in shell than the
    same loop scored in TypeScript.
    """
    if state.dialect.shell_blocks:
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
    if _signature(state, token, is_python):
        return
    _observe(state, token)
    if token in ("{", "}"):
        _brace(state, token)
    elif token in _BOOL_OPS:
        _bool_op(state, token)
    else:
        _keywords(state, token, is_python)


def _observe(state: _FnState, token: str) -> None:
    """What a body token tells the rules that follow expressions across tokens,
    whatever else the token does."""
    if token in _RUN_RESETS:
        state.bool_op = None
    if state.dialect.messages:
        _message_token(state, token)


def _message_token(state: _FnState, token: str) -> None:
    """Follow Objective-C message sends, `[receiver selector:arg ...]`.

    One entry per open `[`, holding the receiver, the first selector word and
    one `part:` per colon, so `[self walk:x to:y]` reads `walk:to:` and a nested
    message keeps its own parts. Only a message to `self` whose whole selector
    is the method's is recursion: `[self narrower:a]` inside `narrower:to:` is
    another method, and `[super viewDidLoad]` runs the superclass's.
    """
    if token == "[":
        state.messages.append([])
    elif token == "]" and state.messages:
        _message_sent(state, state.messages.pop())
    elif state.messages:
        _message_part(state.messages[-1], token, state.prev)


def _message_part(message: list, token: str, prev: str) -> None:
    if len(message) < 2:
        message.append(token)  # the receiver, then the first selector word
    elif token == ":":
        message.append(prev + ":")


def _message_sent(state: _FnState, message: list) -> None:
    if message[:1] != ["self"] or len(message) < 2:
        return
    selector = "".join(message[2:]) or message[1]
    _count_recursion(state, selector == state.name.replace(" ", ""))


def _signature(state: _FnState, token: str, is_python: bool) -> bool:
    """True for a token of a def's or fn's signature, which never counts.

    A Python signature runs to the colon `_signature_token` looks for. A Rust
    one is every token before the body's `{`: brace depth 0 in a function lizard
    has already named, the region `_declarator_and` reads for C++. A `for<'a>`
    binder in a where clause read there as a loop, +1, and the level it left
    pending opened on the body's `{`, so every structure in the body cost 1
    more.
    """
    if is_python and not state.body_started:
        _signature_token(state, token)
        return True
    return state.dialect.rust and state.brace_depth == 0 and token != "{"


def _signature_token(state: _FnState, token: str) -> None:
    """One token of a Python def's signature, which never counts.

    The body starts at the token after the `:` that closes the signature at
    bracket depth 0, the token analyze._PythonBodies marks as the body's first.
    Starting it at the def's first newline instead read a body on the colon
    line as nothing, cognitive 0 and nesting 0, and read a signature's
    continuation lines as body.
    """
    if token == ":" and state.signature_depth == 0:
        state.body_started = True
    else:
        state.signature_depth += _SIGNATURE_DEPTH.get(token, 0)


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

    Past the brace this rule cannot tell, and it does not have to:
    crapkit.lizardclike respells each `&&` that can only declare, such as
    `auto&& x = f()`, `static_cast<T&&>(v)` or `for (auto&& x : r)`, before
    this pass sees the stream, so none of them reaches `_BOOL_OPS`.
    """
    return state.dialect.declarator_and and token == "&&" and state.brace_depth == 0


def _error_set_merge(state: _FnState, token: str) -> bool:
    """True for a Zig `||`, which merges two error sets: `(A || B)!T` is a type."""
    return state.dialect.error_sets and token == "||"


def _bool_op(state: _FnState, token: str) -> None:
    if _declarator_and(state, token) or _error_set_merge(state, token):
        return
    op = {"and": "&&", "or": "||"}.get(token, token)
    if op != state.bool_op:
        state.total += 1
        state.bool_op = op


def _keywords(state: _FnState, token: str, is_python: bool) -> None:
    if state.dialect.shell_blocks:
        _shell_keywords(state, token, is_python)
    elif token == "if":
        _if_token(state, is_python)
    elif token in _ELSE_KEYWORDS:
        _else_token(state, token, is_python)
    elif _counting(state, token):
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


def _counting(state: _FnState, token: str) -> bool:
    """True for a loop or condition charged +1 and the nesting it sits in.

    Rust reads `_RUST_COUNTING`, every other brace or indent language
    `_COUNTING`. A Rust `match` is a switch and is charged as one; it stays out
    of `_COUNTING` because `match` is a soft keyword in Python, where `match =
    re.match(...)` would cost a point and open a block that never closes. The
    reader decides, the way it decides whether a `&&` is a declarator.

    The block itself pays +1 and the nesting it sits in; the arms pay nothing,
    exactly as a C `case` pays nothing. Rust's cyclomatic column counts the arms
    instead, so the two columns say different things about one block on purpose.
    """
    return token in (_RUST_COUNTING if state.dialect.rust else _COUNTING)


def _structure_token(state: _FnState, token: str, is_python: bool) -> None:
    if token == "while" and state.prev == "}":
        return  # the closing half of do-while; the do already paid
    if token == "for" and state.dialect.rust:
        # A binder's `for` shows at the next token (_resolve_for), and an
        # implementation's at the one before it.
        state.for_pending = not implements_for(state.prev)
        return
    _structure(state, token, is_python)


def _jumps_and_recursion(state: _FnState, token: str, is_python: bool) -> None:
    if token == "?":
        # A C ternary, a Swift optional or a Kotlin elvis waits one token to be
        # told apart, where the reader counts a `?` at all. Rust's `?` is none of
        # them: it returns early on an error or relaxes a `?Sized` bound, and an
        # early return is no increment.
        state.question_pending = _counts_question(state)
    elif token in ("break", "continue"):
        state.label_check = not is_python
    elif token == "goto":
        state.total += 1
    else:
        _recursion(state, token)


def _recursion(state: _FnState, token: str) -> None:
    """+1 once when the body calls the function itself (Sonar B1: each method
    in a recursion cycle). The token must name the function and stand where a
    call stands; a local variable, a field or another object's method spelled
    the same way calls nothing."""
    if state.recursed or not _in_body(state):
        return
    if state.dialect.command_leads is not None:
        _count_recursion(state, _commands_itself(state, token))
    else:
        state.call_pending = _names_itself(state, token)


def _in_body(state: _FnState) -> bool:
    """Past the declaration. A shell function's state holds its body alone:
    lizard hands the name and the brace to the enclosing scope."""
    return state.body_started or state.brace_depth > 0 or state.dialect.shell_blocks


def _names_itself(state: _FnState, token: str) -> bool:
    """The function's own name, reached through no receiver, through `self`,
    `this`, `Self` or `cls`, or through its own qualifier. A `(` next makes it a
    call; see _resolve_call."""
    if not state.name.endswith(token):
        return False  # most tokens; spares the split
    parts = _QUALIFIER.split(state.name)
    if token != parts[-1]:
        return False
    return state.prev not in _MEMBER_ACCESS or _own_receiver(state.prev2, parts)


def _own_receiver(receiver: str, parts: list) -> bool:
    return receiver in _SELF_RECEIVERS or (len(parts) > 1 and receiver == parts[-2])


def _commands_itself(state: _FnState, token: str) -> bool:
    """Shell and PowerShell: the name as a command word."""
    at_command = state.at_line_start or state.prev in state.dialect.command_leads
    return at_command and _same_command(state, token)


def _same_command(state: _FnState, token: str) -> bool:
    if state.dialect.fold_case:
        return token.casefold() == state.name.casefold()
    return token == state.name


def _count_recursion(state: _FnState, calls_itself: bool) -> None:
    if calls_itself and not state.recursed:
        state.recursed = True
        state.total += 1


def _counts_question(state: _FnState) -> bool:
    """A `?` waits to be told apart only where the reader counts one in ccn, and
    never in Rust. The reader's condition set is read now, at the `?`."""
    return "?" in state.dialect.conditions and not state.dialect.rust


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
