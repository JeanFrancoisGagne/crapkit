"""Cognitive complexity (Sonar spec) as a lizard token-stream extension.

Rides lizard's language-aware tokenizers, so TS/TSX/JS/Python/Swift all pay the
same rules with no second parse and no new dependency:
  +1 and +nesting for if / ternary / switch / loops / catch-except
  +1 flat for else / elif / elseif (an else-if chain costs one per link, no
  deepening)
  +1 per sequence of logical operators, +1 each time the operator changes;
  a call's arguments, an index and a negated group hold sequences of their
  own, and a sequence runs on over line breaks (see `_follow_runs`); `??`
  costs nothing
  +1 for a labeled break/continue or goto, +1 once when the body calls the
  function itself (see `_recursion` for what a call is in each language)
  try / finally / case labels / with are free; nesting rises inside the
  block structures listed above.

Each language charges its own structures, `_Dialect.counting`. A word that
is a keyword in one language is a name in another: `do(n)` in Go and
`cmd.do(1)` in Python are calls, and Swift's `do` opens the scope a `catch`
handles, not a loop. A word right after a member access (`p.catch(h)`,
`Symbol.for(k)`) is a name in every language.

Language-specific rules:
  * in C/C++ and Objective-C/C++ a `&&` before the function's opening brace
    declares an rvalue reference rather than deciding anything, and costs
    nothing. See `_declarator_and`. Past the brace, the C family's reader
    respells a declarator `&&` before this pass sees it.
  * a `match` is a switch, +1 and +nesting with the arms free: always in
    Rust, and in Python only where a match statement stands, because there
    `match` is a soft keyword and an ordinary name everywhere else. See
    `_python_match`.
  * Rust's `loop` is a loop. Its `?` is no ternary: it returns early on an
    error or relaxes a bound, and neither is an increment. A `for` is a loop
    unless a `<` follows it (a `for<'a>` binder) or the trait's name or `>`
    before it makes it the `for` of `impl Trait for Type`; see `_resolve_for`.
  * Swift's `guard` is an if whose block is its `else`: +1 and +nesting, and
    the `else` is free. See `_guard`.
  * the `while` of a do-while (Swift: repeat-while) is the tail of the loop
    its `do` already paid for, and only that `while`. See `_loop_tail`.
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

import math
import re
from typing import NamedTuple

from .lizardrust import implements_for

# The structures that cost +1 and the nesting they sit in, besides `if`, per
# language (see _Dialect.counting). `_COUNTING` is the union the pass read for
# every language before; a reader absent from the dialect table keeps it.
_C_FAMILY_COUNTING = frozenset({"for", "while", "do", "catch", "switch"})
_COUNTING = _C_FAMILY_COUNTING | {"foreach", "except"}
_PYTHON_COUNTING = frozenset({"for", "while", "except", "match"})
_GO_COUNTING = frozenset({"for", "switch", "select"})
_RUST_COUNTING = frozenset({"for", "while", "loop", "match"})
_SWIFT_COUNTING = frozenset({"for", "while", "repeat", "switch", "catch", "guard"})
_ZIG_COUNTING = frozenset({"for", "while", "switch", "catch"})
_POWERSHELL_COUNTING = frozenset({"for", "foreach", "while", "do", "switch", "catch", "trap"})

# The words that open a loop whose `while` follows its block (see _loop_tail).
_DO = frozenset({"do"})

# The logical operators every language spells with symbols. `??` is not one: a
# null-coalescing operator costs nothing (Sonar v1.7, Ignore shorthand). The
# spelled-out operators depend on the language; see `_Dialect.word_ops`.
_BOOL_OPS = frozenset({"&&", "||"})

# `and` and `or` are operators in Python and Zig, and C++'s alternative tokens
# for `&&` and `||`. Everywhere else they are identifiers.
_AND_OR = frozenset({"and", "or"})

# `-and` and `-or` are PowerShell's, where `&&` and `||` chain pipelines instead.
# They reach here as single tokens only because crapkit's PowerShell reader adds
# a `-\w+` rule to lizard's shared pattern.
_POWERSHELL_OPS = frozenset({"-and", "-or"})

# The brackets that hold a run of logical operators of their own: a call's
# arguments, an index, a negated group. See _open_run. Python's braces are
# brackets too; in every other language a brace opens a block.
_OPENERS = frozenset({"(", "["})
_CLOSERS = frozenset({")", "]"})
_PYTHON_OPENERS = _OPENERS | {"{"}
_PYTHON_CLOSERS = _CLOSERS | {"}"}

# A bracket after one of these is a negated group, whose operators are a
# sequence of their own: the paper scores `a && !(b && c)` 3.
_NEGATIONS = frozenset({"!", "not", "-not"})

# A bracket after a name is a call or an index, unless the name is one of these
# words, which a plain group follows.
_GROUPING_WORDS = frozenset({"if", "elif", "while", "for", "foreach", "switch", "match", "case",
                             "when", "return", "yield", "await", "and", "or", "in", "is",
                             "else", "do", "until", "catch", "assert", "throw", "guard"})

# The tokens that end a statement, and every run open in it.
_STATEMENT_ENDS = frozenset({";", "{", "}"})

# The keywords that pay a FLAT +1 with no nesting increment, which is what the
# whitepaper gives an else-if link. `elseif` is one word in PowerShell (and in
# PHP); it belongs here rather than in `_COUNTING`, where it would be charged
# +1 + nesting and a chain inside a loop would cost more than the same chain at
# the top of the function.
_ELSE_KEYWORDS = frozenset({"else", "elif", "elseif"})

# The words after which a shell or PowerShell word is a command. Both languages
# call a function by naming it as a command, with no parentheses, so a function
# calls itself when its name stands here; the same word as an argument (`echo
# fact`, `Write-Output 'Get-Name'`) calls nothing.
_SHELL_LEADS = frozenset({"", ";", "|", "&", "&&", "||", "(", "{", "`", "!", "then", "do",
                          "else", "elif", "if", "while", "until", "time"})
_POWERSHELL_LEADS = frozenset({"", ";", "|", "&", "(", "{", "=", "return", "in", "throw"})

# The condition set of a reader that has none: a `?` is a conditional operator.
_QUESTION = frozenset({"?"})


class _Dialect(NamedTuple):
    """What one language's tokens mean to the rules below.

    `rust`: Rust's own syntax, a `for` that loops over nothing, a signature
    that never counts and a `?` that is no conditional (see _resolve_for,
    _signature, _counts_question). `error_sets`: `||` merges two error sets
    (Zig; see _error_set_merge). `conditions`: the reader's condition set,
    read at each `?`, and set from the reader for each file (see
    LizardExtension.__call__).

    `counting`: the structures besides `if` that cost +1 and the nesting they
    sit in. `do_loops`: the words that open a loop whose `while` comes after
    its block. `goto`: the language has a goto, which costs +1.
    `declarator_and`: a `&&` before the body's brace declares an rvalue
    reference (C, C++, Objective-C++). `shell_blocks`: blocks are delimited
    by words (shell).
    `messages`: a call to a method is a message, `[self sel:arg]`
    (Objective-C). `command_leads`: a call is the function's name as a
    command, after one of these words (shell, PowerShell), and `fold_case`
    says the name matches in any case (PowerShell). `word_ops`: the logical
    operators spelled as words. `elvis`: `a ?: b` is GNU's conditional with its
    middle operand omitted (C, C++, Objective-C). `openers` and `closers`: the
    brackets a run of logical operators is kept per. `overloads`: one name can
    belong to several functions that differ in their parameters (C++, Java,
    Swift), so a call to the name is a call to this one only when it passes a
    number of arguments this one takes.
    """

    rust: bool = False
    error_sets: bool = False
    conditions: frozenset = _QUESTION
    counting: frozenset = _COUNTING
    do_loops: frozenset = _DO
    goto: bool = True
    declarator_and: bool = False
    shell_blocks: bool = False
    messages: bool = False
    command_leads: frozenset | None = None
    fold_case: bool = False
    word_ops: frozenset = frozenset()
    elvis: bool = False
    openers: frozenset = _OPENERS
    closers: frozenset = _CLOSERS
    overloads: bool = False


# Keyed on the reader's exact class name, never on an issubclass test: JavaReader,
# CSharpReader and TTCNReader inherit from CLikeReader, and in those languages
# every `&&` is an operator, so the declarator rule reaching them could only ever
# lose a real one. crapkit's CorrectedRustReader sits beside lizard's RustReader
# for the same reason: the discriminator is the language. A reader absent from the
# table reads under the defaults.
_DEFAULT_DIALECT = _Dialect()
_RUST = _Dialect(counting=_RUST_COUNTING, do_loops=frozenset(), goto=False, rust=True)
_PYTHON = _Dialect(counting=_PYTHON_COUNTING, do_loops=frozenset(), goto=False, word_ops=_AND_OR,
                   openers=_PYTHON_OPENERS, closers=_PYTHON_CLOSERS)
_JAVASCRIPT = _Dialect(counting=_C_FAMILY_COUNTING, goto=False)
_DIALECTS = {
    "CLikeReader": _Dialect(counting=_C_FAMILY_COUNTING, declarator_and=True, word_ops=_AND_OR,
                            elvis=True, overloads=True),
    "ObjCReader": _Dialect(counting=_C_FAMILY_COUNTING, declarator_and=True, messages=True,
                           word_ops=_AND_OR, elvis=True, overloads=True),
    "JavaReader": _Dialect(counting=_C_FAMILY_COUNTING, goto=False, overloads=True),
    "JavaScriptReader": _JAVASCRIPT,
    "TypeScriptReader": _JAVASCRIPT,
    "TSXReader": _JAVASCRIPT,
    "VueReader": _JAVASCRIPT,
    "GoReader": _Dialect(counting=_GO_COUNTING, do_loops=frozenset()),
    "SwiftReader": _Dialect(counting=_SWIFT_COUNTING, do_loops=frozenset({"repeat"}), goto=False,
                            overloads=True),
    "RustReader": _RUST,
    "CorrectedRustReader": _RUST,
    "ShellReader": _Dialect(shell_blocks=True, command_leads=_SHELL_LEADS),
    "PowerShellReader": _Dialect(counting=_POWERSHELL_COUNTING, goto=False,
                                 command_leads=_POWERSHELL_LEADS, fold_case=True,
                                 word_ops=_POWERSHELL_OPS),
    "PythonReader": _PYTHON,
    "PythonSignatureReader": _PYTHON,
    "ZigReader": _Dialect(counting=_ZIG_COUNTING, do_loops=frozenset(), goto=False,
                          word_ops=_AND_OR),
}

# A reader crapkit subclasses reads under the rules of the lizard reader it
# corrects, and Zig's `||` merges error sets under either.
_DIALECTS["ZigReader"] = _DIALECTS["ZigReader"]._replace(error_sets=True)
_DIALECTS.update({f"Corrected{stock}": _DIALECTS.get(stock, _DEFAULT_DIALECT)
                  for stock in ("GoReader", "SwiftReader", "ZigReader")})

# What a function calls itself through: nothing, one of these receivers, its
# own qualifier (`Calc::fact`, `K.fact`) or, in Go, its receiver's name. A call
# through any other receiver is another object's method with the same name.
_SELF_RECEIVERS = frozenset({"self", "this", "Self", "cls"})
_MEMBER_ACCESS = frozenset({".", "->", "::", "?."})

# How lizard qualifies a name: `detail::pow10`, `K::fact`, `outer.inner`.
_QUALIFIER = re.compile(r"::|\.")

# A Go method's receiver, which opens lizard's long name: `(c*Command)Traverse`.
_GO_RECEIVER = re.compile(r"^\((\w+)")


class _Own(NamedTuple):
    """How a function is called, read off lizard's FunctionInfo once its body
    starts, where the name is final (see _own)."""

    name: str
    bare: str               # the name without its qualifier
    receivers: frozenset    # what a call to it can be made through
    arity: tuple            # the fewest and the most arguments a call passes

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
# is a C-family do-while loop, and reading it that way here too charged every
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
                 "bool_op", "fn", "own", "recursed", "body_started", "signature_depth",
                 "prev", "prev2", "label_check", "for_pending", "dialect", "call_pending",
                 "call", "messages", "runs", "run_break", "word_op", "braces",
                 "closed_do", "guard_else", "match_indent")

    def __init__(self, fn=None, dialect: _Dialect = _DEFAULT_DIALECT):
        self.dialect = dialect
        self.fn = fn
        self.own = None          # see _own
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
        self.recursed = False
        self.body_started = False  # Python only: past the colon that ends the signature
        self.signature_depth = 0   # brackets open in that signature
        self.prev = ""
        self.prev2 = ""           # the token before prev: a call's receiver
        self.label_check = False  # just saw break/continue
        self.for_pending = False  # Rust only: just saw a `for` that may be a binder's
        self.call_pending = False  # the name was just spelled; a `(` makes it a call
        self.call = None          # that call's arguments so far; see _follow_call
        self.messages = []        # Objective-C: one entry per open `[`, see _message_token
        self.runs = []            # per open bracket: the run outside it; see _open_run
        self.run_break = False    # a line ended; the next token says whether the run did
        self.word_op = None       # `and`/`or` just seen; a `:` next makes it a selector part
        self.braces = []          # per open `{`: whether a `do` opened it; see _loop_tail
        self.closed_do = False    # the last `}` closed a `do`'s block
        self.guard_else = False   # Swift: a guard awaits its else; see _guard
        self.match_indent = None  # Python: a line starting with `match`; see _python_match


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
    """
    state = states.get(fn)
    if state is None:
        state = states[fn] = _FnState(fn, dialect)
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
    _settle_line(state, token, is_python)
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
        _match_line_ends(state)
        state.at_line_start = True
        state.run_break = _may_end_statement(state)
        state.line_indent = len(token) - token.rfind("\n") - 1
    elif state.at_line_start:
        state.line_indent += len(token)


def _may_end_statement(state: _FnState) -> bool:
    """Whether a line break can end the run of logical operators.

    Not inside a bracket, and not after an operator or a backslash, which
    continue the expression on the next line. Whether a line that starts with
    an operator continues it is for that line's first token to say; see
    _settle_line.
    """
    after_operator = _is_bool_op(state, state.prev) or state.prev == "\\"
    return not state.runs and not after_operator


def _is_bool_op(state: _FnState, token: str) -> bool:
    return token in _BOOL_OPS or token in state.dialect.word_ops


def _settle_line(state: _FnState, token: str, is_python: bool) -> None:
    """A line's first real token closes the blocks a Python dedent left and ends
    the run of logical operators, unless the token continues it."""
    if is_python:
        _python_dedent(state)
    if state.run_break:
        state.run_break = False
        state.bool_op = state.bool_op if _is_bool_op(state, token) else None


def _python_dedent(state: _FnState) -> None:
    """At a statement line's first real token, close every block its indent
    has left. A line inside a bracket continues a statement, and its indent
    closes nothing."""
    if not _statement_start(state):
        return
    _close_comprehensions(state)
    while state.stack and state.line_indent <= state.stack[-1]:
        state.stack.pop()


def _statement_start(state: _FnState) -> bool:
    """A Python statement starts at a line's first real token outside every
    bracket. `if` and `else` anywhere else are a ternary's."""
    return state.at_line_start and not state.runs


class _Bracket(NamedTuple):
    """A Python comprehension's level on the nesting stack: open while the
    bracket it sits in is, at `depth` brackets deep. See _comprehension."""

    depth: int


def _comprehension(state: _FnState) -> None:
    """A comprehension's `for`: +1 and the nesting it sits in, and a level that
    closes with the comprehension's bracket. Kept open to the end of the line,
    it charged the second of `[p for p in a] + [q for q in b]` one level
    deeper than the first."""
    state.total += 1 + len(state.stack)
    _push(state, _Bracket(len(state.runs)))


def _close_comprehensions(state: _FnState) -> None:
    """Close the comprehension levels whose bracket has closed. A statement
    end that clears the brackets (a stray `;`) closes them too."""
    while (state.stack and isinstance(state.stack[-1], _Bracket)
           and state.stack[-1].depth > len(state.runs)):
        state.stack.pop()


def _resolve_lookbehinds(state: _FnState, token: str, is_python: bool) -> bool:
    """Signals needing one token of hindsight. True = this token is consumed."""
    if state.for_pending:
        _resolve_for(state, token, is_python)
    _resolve_words(state, token)
    if state.question_pending and _resolve_question(state, token, is_python):
        return True
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


def _resolve_words(state: _FnState, token: str) -> None:
    """What the word just before this token turned out to be."""
    if state.label_check:
        _resolve_label(state, token)
    if state.call_pending:
        _resolve_call(state, token)
    if state.word_op:
        _resolve_word_op(state, token)


def _resolve_question(state: _FnState, token: str, is_python: bool) -> bool:
    """A `?` is a conditional unless the token after it says otherwise. True when
    this token is the second `?` of `??` (or `??=`), which lizard's JavaScript
    and PowerShell tokenizers split in two: a null-coalescing operator costs
    nothing (Sonar v1.7, Ignore shorthand), so the pair is consumed."""
    state.question_pending = False
    if token == "?":
        return True
    if _is_conditional(state, token):
        state.total += 1 + _nesting(state, is_python)
    return False


def _is_conditional(state: _FnState, token: str) -> bool:
    """`?.` is optional chaining, `?)` and `?:` optional marks in TypeScript. In
    C, C++ and Objective-C, `?:` is GCC's conditional with its middle operand
    omitted, `a ?: b` for `a ? a : b` (GCC manual sec. 6.8), and costs what a
    ternary costs."""
    if token == ":":
        return state.dialect.elvis
    return token not in (".", ")")


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
    """The name followed by `(` is a call. Where a name can be overloaded the
    call is to this function only if it passes as many arguments as this one
    takes, which is known at the closing bracket; see _follow_call. The pass
    sees no types, so an overload that takes as many arguments of other types
    still reads as a call to this one, and so does a call a macro qualifies
    (`FMT_POSIX_CALL(close(fd))` expands to `::close`)."""
    state.call_pending = False
    if token != "(":
        return
    if state.dialect.overloads:
        state.call = [len(state.runs) + 1, 0, -1]  # depth inside, commas, tokens
    else:
        _count_recursion(state, True)


def _resolve_word_op(state: _FnState, token: str) -> None:
    """An `and` or `or` followed by `:` names an Objective-C selector part
    (`- (int)join:(int)a and:(int)b`); anything else makes it the operator."""
    op, state.word_op = state.word_op, None
    if token != ":":
        _bool_op(state, op)


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
    elif token in state.dialect.word_ops:
        state.word_op = token  # the next token decides; see _resolve_word_op
    else:
        _keywords(state, token, is_python)


def _observe(state: _FnState, token: str) -> None:
    """What a body token tells the rules that follow expressions across tokens,
    whatever else the token does."""
    _follow_runs(state, token)
    if state.call is not None:
        _follow_call(state, token)
    if state.dialect.messages:
        _message_token(state, token)


def _follow_call(state: _FnState, token: str) -> None:
    """Count the arguments of a call to the function's own name, one per comma
    at the call's own depth, and judge the call when its bracket closes.

    `format(date)` inside `format(Date date, boolean millis)` delegates to
    another overload, and one argument is not two. A statement that ends
    inside the brackets, a lambda's block in an argument, leaves the count
    unknown, and the call counts.
    """
    call = state.call
    if len(state.runs) < call[0]:
        state.call = None
        _count_recursion(state, token not in state.dialect.closers or _fits(state, call))
    elif token == "," and len(state.runs) == call[0]:
        call[1] += 1
    call[2] += 1


def _fits(state: _FnState, call: list) -> bool:
    passed = call[1] + 1 if call[2] else 0
    fewest, most = _own(state).arity
    return fewest <= passed <= most


def _follow_runs(state: _FnState, token: str) -> None:
    """Keep one run of logical operators per open bracket.

    A comma ends the run it sits in, which is one argument's or one element's.
    A statement's end ends every run. See _open_run and _close_run for the
    brackets.
    """
    if token in state.dialect.openers:
        _open_run(state)
    elif token in state.dialect.closers:
        _close_run(state)
    elif token == ",":
        state.bool_op = None
    elif token in _STATEMENT_ENDS:
        state.bool_op = None
        state.runs.clear()


def _open_run(state: _FnState) -> None:
    """A bracket after a name (a call's arguments, an index) or after a negation
    holds a run of its own, and the paper counts `a && !(b && c)` as two runs.
    Any other bracket is a plain group, which continues the run outside it:
    `a && (b && c)` is one."""
    grouping = _groups(state.prev)
    state.runs.append((state.bool_op, grouping))
    if not grouping:
        state.bool_op = None


def _groups(prev: str) -> bool:
    if prev in _NEGATIONS:
        return False
    if prev in _GROUPING_WORDS:
        return True
    return not (prev[:1].isalnum() or prev[:1] in ("_", "$", ")", "]"))


def _close_run(state: _FnState) -> None:
    """The run outside the bracket resumes. A group opened where no run was
    open hands its own run out instead, so `(a && b) && c` is one run."""
    if not state.runs:
        state.bool_op = None
        return
    outer, grouping = state.runs.pop()
    if outer is not None or not grouping:
        state.bool_op = outer
    _close_comprehensions(state)


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
    _count_recursion(state, selector == _own(state).name.replace(" ", ""))


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
    if "{" in state.dialect.openers:
        return  # a bracket, which _follow_runs keeps; its depth is no block's
    if token == "{":
        _open_brace(state)
    else:
        _close_brace(state)


def _open_brace(state: _FnState) -> None:
    if state.pending:
        _push(state, state.brace_depth)
        state.pending = False
    state.braces.append(state.prev in state.dialect.do_loops)
    state.brace_depth += 1


def _close_brace(state: _FnState) -> None:
    """A `}` closes a level opened at its depth, and says whether it closed a
    `do`'s block; see _loop_tail."""
    state.brace_depth -= 1
    state.closed_do = state.braces.pop() if state.braces else False
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
    elif _names_a_member(state):
        _recursion(state, token)
    else:
        _structures(state, token, is_python)


def _names_a_member(state: _FnState) -> bool:
    """A word right after a member access on its line names a member,
    `p.catch(h)`, `Symbol.for(k)`, never a structure. A `.` that ends a line
    is PowerShell's current directory, `Push-Location .`, and the next line's
    first word starts a statement."""
    return state.prev in _MEMBER_ACCESS and not state.at_line_start


def _structures(state: _FnState, token: str, is_python: bool) -> None:
    if token == "if":
        _if_token(state, is_python)
    elif token in _ELSE_KEYWORDS:
        _else_token(state, token, is_python)
    elif token in state.dialect.counting:
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
    function has already charged, and reading `do` as a C-family do-while
    charged every shell loop a second time.
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


def _structure_token(state: _FnState, token: str, is_python: bool) -> None:
    """A word in the language's `counting` set.

    A `match` (Rust, Python) is a switch: the block pays +1 and the nesting it
    sits in, and the arms pay nothing, exactly as a C `case` pays nothing.
    Rust's cyclomatic column counts the arms instead, so the two columns say
    different things about one block on purpose.
    """
    if is_python:
        _python_structure(state, token)
    elif token == "guard":
        _guard(state, is_python)
    elif not _charged_elsewhere(state, token):
        _structure(state, token, is_python)


def _charged_elsewhere(state: _FnState, token: str) -> bool:
    """True for a word whose charge is decided elsewhere: a Rust `for` shows a
    binder's at the next token (see _resolve_for) and an implementation's at the
    one before it, and a do-while's `while` was paid for by its `do`."""
    if token == "for" and state.dialect.rust:
        state.for_pending = not implements_for(state.prev)
        return True
    return _loop_tail(state, token)


def _python_structure(state: _FnState, token: str) -> None:
    if token == "match":
        _python_match(state, token)
    elif token == "for" and state.runs:
        _comprehension(state)
    else:
        _structure(state, token, True)


def _loop_tail(state: _FnState, token: str) -> bool:
    """True for the `while` of a do-while (Swift: repeat-while), which the
    `do` already paid for.

    Only the `while` right after the `}` that closes a block a `do` opened
    (see _open_brace). Reading every `while` after a `}` as a tail made the
    loop after an `if` block, or after a Python dict, cost nothing.
    """
    return token == "while" and state.prev == "}" and state.closed_do


def _guard(state: _FnState, is_python: bool) -> None:
    """Swift's `guard` is an if whose block is its `else`: +1 and the nesting
    it sits in, and the block, which opens at the `else`, is one level deeper.
    The `else` itself is free (see _else_token). The level waits for the
    `else` rather than the next `{`, so a closure in the condition,
    `guard xs.contains(where: { $0 > 0 }) else {`, opens nothing."""
    state.total += 1 + _nesting(state, is_python)
    state.guard_else = True


def _python_match(state: _FnState, token: str) -> None:
    """A Python `match` that starts a statement may open a match statement.

    `match` is a soft keyword: `match = re.match(...)`, `match(s)` and
    `match: int = 0` use the same word as a name. A match statement is the
    only one of them whose line ends in a `:` outside every bracket, which
    the line's end decides; see _match_line_ends.
    """
    if state.at_line_start and not state.runs:
        state.match_indent = state.line_indent
    _recursion(state, token)


def _match_line_ends(state: _FnState) -> None:
    """At a line break: a line that started with `match` and ended in a `:`
    outside every bracket is a match statement, +1 and the nesting it sits in,
    and a level at the match's indent holds its cases. A break inside a bracket
    or after a backslash continues the line."""
    if state.match_indent is None or state.runs or state.prev == "\\":
        return
    if state.prev == ":":
        state.total += 1 + len(state.stack)
        _push(state, state.match_indent)
    state.match_indent = None


def _jumps_and_recursion(state: _FnState, token: str, is_python: bool) -> None:
    if token == "?":
        # A C ternary, a Swift optional or a Kotlin elvis waits one token to be
        # told apart, where the reader counts a `?` at all. Rust's `?` is none of
        # them: it returns early on an error or relaxes a `?Sized` bound, and an
        # early return is no increment.
        state.question_pending = _counts_question(state)
    elif token in ("break", "continue"):
        state.label_check = not is_python
    elif token == "goto" and state.dialect.goto:
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
    """The function's own name, reached through no receiver or through one of
    its own (see _SELF_RECEIVERS). A `(` next makes it a call; see
    _resolve_call."""
    own = _own(state)
    if token != own.bare:
        return False
    return state.prev not in _MEMBER_ACCESS or state.prev2 in own.receivers


def _own(state: _FnState) -> _Own:
    """Read once, in the body. lizard builds the name while it reads the
    declaration, so the name at a function's first token is often not its
    name: empty in Go and PowerShell, `K::int` for a Java method returning int,
    the class alone for `File File::open(`."""
    if state.own is None:
        state.own = _own_of(state.fn)
    return state.own


def _own_of(fn) -> _Own:
    name = getattr(fn, "name", "")
    parts = _QUALIFIER.split(name)
    receivers = _SELF_RECEIVERS.union(parts[-2:-1],
                                      _GO_RECEIVER.findall(getattr(fn, "long_name", "")))
    return _Own(name, parts[-1], receivers, _arity(getattr(fn, "full_parameters", ())))


def _arity(parameters) -> tuple:
    """A parameter with a default can be left out, and a variadic one (`...`)
    takes any number, as lizard spells them in C++, Java and Swift."""
    kinds = [_parameter_kind(p.strip()) for p in parameters if p.strip()]
    most = math.inf if "variadic" in kinds else len(kinds)
    return kinds.count("required"), most


def _parameter_kind(spelled: str) -> str:
    if "..." in spelled:
        return "variadic"
    return "optional" if "=" in spelled else "required"


def _commands_itself(state: _FnState, token: str) -> bool:
    """Shell and PowerShell: the name as a command word."""
    at_command = state.at_line_start or state.prev in state.dialect.command_leads
    return at_command and _same_command(state, token)


def _same_command(state: _FnState, token: str) -> bool:
    name = _own(state).name
    if state.dialect.fold_case:
        return token.casefold() == name.casefold()
    return token == name


def _count_recursion(state: _FnState, calls_itself: bool) -> None:
    if calls_itself and not state.recursed:
        state.recursed = True
        state.total += 1


def _counts_question(state: _FnState) -> bool:
    """A `?` waits to be told apart only where the reader counts one in ccn, and
    never in Rust. The reader's condition set is read now, at the `?`."""
    return "?" in state.dialect.conditions and not state.dialect.rust


def _if_token(state: _FnState, is_python: bool) -> None:
    if is_python and not _statement_start(state):
        state.total += 1 + _nesting(state, is_python)  # ternary expression form
        return
    state.total += 1 + _nesting(state, is_python)
    _push_structure(state, is_python)


def _else_token(state: _FnState, token: str, is_python: bool) -> None:
    if is_python and not _statement_start(state):
        return  # the else arm of a ternary expression is part of its +1
    if state.guard_else:
        state.guard_else = False
        state.pending = True  # the guard's block, which the guard paid for
        return
    _else_link(state, token, is_python)


def _else_link(state: _FnState, token: str, is_python: bool) -> None:
    """The flat +1 of an else or else-if link. `elif` and `elseif` carry their
    own condition and their own block, so the block opens here, as it does for
    every Python link; a brace language's bare `else` has to wait one token to
    find out whether an `if` follows it."""
    state.total += 1
    if is_python or token != "else":
        _push_structure(state, is_python)
    else:
        state.else_pending = True


def _structure(state: _FnState, token: str, is_python: bool) -> None:
    state.total += 1 + _nesting(state, is_python)
    _push_structure(state, is_python)


def _push_structure(state: _FnState, is_python: bool) -> None:
    if is_python:
        _push(state, state.line_indent)
    else:
        state.pending = True
