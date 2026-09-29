"""Cognitive complexity (Sonar spec) as a lizard token-stream extension.

Rides lizard's language-aware tokenizers, so TS/TSX/JS/Python/Swift all pay the
same rules with no second parse and no new dependency:
  +1 and +nesting for if / ternary / switch / loops / catch-except
  +1 flat for else / elif / elseif (an else-if chain costs one per link, no
  deepening)
  +1 per sequence of logical operators, +1 each time the operator changes,
  read left to right through a plain group; a call's arguments, an index,
  a negated group, a group compared and each operand of a conditional hold
  sequences of their own, and a sequence runs on over line breaks (see
  `_follow_runs`); `??` costs nothing
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
  * in C, C++, Objective-C, Java, JavaScript, TypeScript and Zig a body can
    go without braces, `if (a) return b ? 1 : 2;`, and holds a level from its
    header's `)` to the end of its statement. A `{` is a structure's block
    only where the structure still waits for one at its own bracket depth.
    See `_body_token`.
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
deepen the nesting a structure inside them is charged at (a structure inside a
ternary arm is rare enough to accept).

The deepest the per-function stack gets is recorded too, as
`cognitive_nesting`, and analyze.py reads it as the `nesting` column of every
row. lizard's ND extension, which the column used to come from, counted
structures in Python rather than depth (a flat function of seven `if`s read 7),
closed a shell block only on a `}` or at a `;` (seven shell ifs side by side
read 6), and in brace languages it closed a level at every `}` and at the first
`;` after a braceless structure, and opened one for `&&`, `||`, `case`, `try`
and `def`: three nested loops read 2, and a Go condition with three operators
read 4. Two levels the stack itself never holds count toward the depth as
well: the body of a structure that has no braces (`if (a) return 0;`, see
`_open_body`), and the arms of a conditional operator, counted once its `:`
arrives (see `_arms_token`).
A structure's body is the first `{` at its keyword's own bracket depth, so a
literal or a lambda inside its header is not; see `_open_brace`,
`_brace_body` and, for headers without parentheses, `_resolve_reopen`. A body
without braces in the function's declaration ends at the function's `{`
(`_function_body`).
A word spelled like a keyword is a name where the language or the tokens
around it say so: a structure keyword followed by a `:` (see
`_resolve_structure`), a word the language's `counting` set leaves out
(`do` and `while` in Go, `do` in Zig) and a word after a member access
(`_names_a_member`).

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
from collections import deque
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
                             "when", "return", "yield", "await", "and", "or",
                             "else", "do", "until", "catch", "assert", "throw", "guard"})

# The operators that bind tighter than a logical one. A group beside one of
# them is its operand, `x == (b && c)` or `(p, q) == (r, s)`, and its operators
# are a sequence of their own, as a call's arguments are (see _groups and
# _after_group). PowerShell spells its own as `-eq`, `-like` and so on.
_TIGHTER = frozenset({"==", "!=", "===", "!==", "<", ">", "<=", ">=", "+", "-", "*", "/", "%",
                      "**", "//", "<<", ">>", ">>>", "&", "|", "^", "in", "is", "not",
                      "instanceof"})

# After a group, these apply something to it: `(f || g)(x)`, `(a || b).c`.
_APPLIED = frozenset({"(", "[", ".", "?.", "->"})

# The tokens that end a statement, and every run open in it.
_STATEMENT_ENDS = frozenset({";", "{", "}"})

# The phases of a body in a language where it can go without braces (see
# _open_body): the structure's keyword was just read, and a header in
# parentheses may follow; that header is open; nothing has followed the header
# yet; a Zig payload, `|x|`, is open; a token other than `{` followed, so the
# body has no braces; a `{` followed, and the block on the stack holds the
# level.
_FRESH, _HEADER, _AWAITING, _PAYLOAD, _BEGUN, _BRACED = range(6)

# The phases in which a body holds a nesting level of its own. In its header
# a structure sits at its keyword's level, and a braced body's level is on the
# stack.
_HOLDING = frozenset({_AWAITING, _PAYLOAD, _BEGUN})

# Words between a structure's keyword and its header: C++'s `if constexpr (`
# and `if consteval {`, JavaScript's `for await (`.
_HEADER_WORDS = frozenset({"constexpr", "consteval", "await"})

# After a statement's end, these say the statement goes on: a try's `catch` or
# `finally`. An `else` goes on with the if it belongs to; see _else_ends_body.
_STATEMENT_GOES_ON = frozenset({"catch", "finally", "else"})

# The characters an operator starts with, and the operators that can start a
# JavaScript statement of their own. See _continues_line.
_OPERATOR_START = frozenset("=.?:&|+-*/%<>,^!~")
_PREFIX_ONLY = frozenset({"!", "~", "++", "--"})

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


# How the token after a break or continue reads as a label (see _resolve_label).
# A line break in between ends the jump first (see _line_event), so in Go and
# Swift the `case` after a bare `break` is no label.
def _any_label(token: str) -> bool:
    """A reader outside the table: any token that does not end the statement."""
    return token not in (";", "}", ")")


def _named_label(token: str) -> bool:
    """`break outer`: Java, JavaScript, Go, Swift, PowerShell. A comma or a
    value is none."""
    return token[:1].isalpha() or token[:1] == "_"


def _rust_label(token: str) -> bool:
    """`break 'outer`. `break n` returns a value from a `loop`, and a match
    arm's `continue,` jumps nowhere."""
    return token.startswith("'")


def _zig_label(token: str) -> bool:
    """`break :blk value`. A prong's `continue,` jumps nowhere."""
    return token == ":"


def _shell_label(token: str) -> bool:
    """Shell has no labels. It spells the same jump `break 2`, a count of
    enclosing loops to leave, and a bare `break` is followed by whatever the
    loop is followed by, a `fi` or a `done`."""
    return token.isdigit()


class _Dialect(NamedTuple):
    """What one language's tokens mean to the rules below.

    `rust`: Rust's own syntax, a `for` that loops over nothing, a signature
    that never counts and a `?` that is no conditional (see _resolve_for,
    _signature, _counts_question). `error_sets`: `||` merges two error sets
    (Zig; see _error_set_merge). `if_types`: an `if` can be a type, whose arms
    end at the `=` after it (Zig; see _initializer). `conditions`: the reader's condition set,
    read at each `?`, and set from the reader for each file (see
    LizardExtension.__call__). `bare_headers`: a structure's header goes
    without parentheses, so a literal or a block in it sits at the body's depth
    (Go, Rust, Swift; see _resolve_reopen).

    `counting`: the structures besides `if` that cost +1 and the nesting they
    sit in. `do_loops`: the words that open a loop whose `while` comes after
    its block. `goto`: the language has a goto, which costs +1. `labels`:
    whether the token after a break or continue is a label.
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
    number of arguments this one takes and no other function of the name in
    the file takes it too (see _settle_overloads). `braceless`: a structure's body can
    go without braces (C, C++, Objective-C, Java, JavaScript, TypeScript, Zig).
    `line_statements`: a line break can end a statement that has no `;`
    (JavaScript, TypeScript).
    `bracket_lines`: a bracket holds commands, one per line, so a line break
    inside one ends a run of logical operators: a shell subshell `( ... )` or
    `$( ... )`, PowerShell's `$( ... )` and `@( ... )`.
    `imports`: the words that start an import statement, whose list binds
    names (Python's `import`, Rust's `use`).
    `classes`: the words that open a class body in braces, whose methods the
    token stream has to name, because lizard does not (JavaScript,
    TypeScript). `self_only`: a method is reached only through its object or
    its class, and a bare name in its body is looked up outside the class
    (Python, JavaScript, TypeScript, Go, Rust). `binders`: the words before a
    name that bind it in the body, and `assigns`: the operators after one, so a
    call to that name reaches the value bound (see _binds).
    `types`: reads the header of a type whose body defines functions, so its
    name reaches them (`R::spin`, `A.f`, `R.f`), because lizard names none of
    them with it: a Rust impl or trait, a Swift class, struct, enum, actor or
    extension, a Zig container (see _rust_type, _swift_type, _zig_type).
    `argument_labels`: a function's name holds its parameters' argument labels,
    so `d(for: x)` does not call `d(of:)` (Swift; see _labels_fit), a closure
    after a call's `)` is one more argument (see _trailing_closure), and the
    parameters are read off the stream (see _swift_signature).
    """

    rust: bool = False
    error_sets: bool = False
    if_types: bool = False
    conditions: frozenset = _QUESTION
    bare_headers: bool = False
    counting: frozenset = _COUNTING
    do_loops: frozenset = _DO
    goto: bool = True
    labels: object = _any_label
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
    braceless: bool = False
    line_statements: bool = False
    classes: frozenset = frozenset()
    self_only: bool = False
    binders: frozenset = frozenset()
    assigns: frozenset = frozenset()
    imports: frozenset = frozenset()
    bracket_lines: bool = False
    types: object = None
    argument_labels: bool = False


# --- the type a function is defined in, where lizard does not name it --------------
# Each reader sets scopes.pending to the type body the next `{` opens (see
# _brace_scopes), named by the time it opens; a function lizard starts directly
# in that body is its member (see _home).

# `impl` opens a type only where an item starts; `-> impl Iterator` names a
# return type.
_RUST_ITEM_STARTS = frozenset({"", "{", "}", ";", "]", "unsafe"})
_ANGLES = {"<": 1, ">": -1, ">>": -2}


def _rust_type(scopes, token: str) -> None:
    """`impl<T> Walk<T> for R<T> where T: Copy {` names its body R: the last
    word outside angle brackets, after `for` when there is one, before `where`.
    `trait T {` names its body T."""
    if _opens_rust_type(token, _last(scopes.recent)):
        scopes.pending, scopes.angles = _Scope(None, "class"), 0
    elif scopes.pending is not None:
        _rust_header(scopes, token)


def _opens_rust_type(token: str, before: str) -> bool:
    if token == "impl":
        return before in _RUST_ITEM_STARTS
    return token == "trait" and before not in _MEMBER_ACCESS


def _rust_header(scopes, token: str) -> None:
    if scopes.angles is None:
        return  # past `where`: the bounds name no type
    scopes.angles += _ANGLES.get(token, 0)
    if scopes.angles == 0:
        _rust_type_word(scopes, token)


def _rust_type_word(scopes, token: str) -> None:
    if token == "where":
        scopes.angles = None
    elif token == "for":
        scopes.pending = scopes.pending._replace(name="")
    elif _WORD.fullmatch(token):
        scopes.pending = scopes.pending._replace(name=token)


_SWIFT_TYPES = frozenset({"class", "struct", "enum", "actor", "extension", "protocol"})
# Words after `class` that make it a modifier (`class func f`), not a type.
_SWIFT_DECLARATIONS = frozenset({"func", "var", "let", "subscript", "init", "deinit",
                                 "override", "final", "static", "required", "convenience",
                                 "private", "fileprivate", "internal", "public", "open"})


def _swift_type(scopes, token: str) -> None:
    """`class A: B {`, `struct Box<T> {`, `extension A {` name their body by the
    word after the keyword."""
    if scopes.pending is not None and not scopes.pending.name:
        scopes.pending = _swift_named(scopes.pending, token)
    elif token in _SWIFT_TYPES and _last(scopes.recent) not in _MEMBER_ACCESS:
        scopes.pending = _Scope(None, "class")


def _swift_named(pending, token: str):
    return None if token in _SWIFT_DECLARATIONS else _named(pending, token)


_ZIG_CONTAINERS = frozenset({"struct", "union", "enum", "opaque"})
_ZIG_LAYOUTS = frozenset({"extern", "packed"})


def _zig_type(scopes, token: str) -> None:
    """`const R = struct {` names its body R, as do `packed struct`, `extern
    union` and `union(enum)`; a container with no such name opens a body with
    none."""
    if token in _ZIG_CONTAINERS and scopes.pending is None and _last(scopes.recent) != ".":
        scopes.pending = _Scope(None, "class", _zig_name(scopes.recent))


def _zig_name(recent) -> str:
    before = [token for token in recent if token not in _ZIG_LAYOUTS][-2:]
    return before[0] if len(before) == 2 and before[1] == "=" else ""


# Keyed on the reader's exact class name, never on an issubclass test: JavaReader,
# CSharpReader and TTCNReader inherit from CLikeReader, and in those languages
# every `&&` is an operator, so the declarator rule reaching them could only ever
# lose a real one. crapkit's CorrectedRustReader sits beside lizard's RustReader
# for the same reason: the discriminator is the language. A reader absent from the
# table reads under the defaults.
_DEFAULT_DIALECT = _Dialect()
_RUST = _Dialect(counting=_RUST_COUNTING, do_loops=frozenset(), goto=False, labels=_rust_label,
                 self_only=True, binders=frozenset({"let", "mut", "as"}),
                 imports=frozenset({"use"}), types=_rust_type)
_PYTHON = _Dialect(counting=_PYTHON_COUNTING, do_loops=frozenset(), goto=False, word_ops=_AND_OR,
                   openers=_PYTHON_OPENERS, closers=_PYTHON_CLOSERS, self_only=True,
                   binders=frozenset({"import", "as", "for"}), assigns=frozenset({"=", ":="}),
                   imports=frozenset({"import"}))
_JAVASCRIPT = _Dialect(counting=_C_FAMILY_COUNTING, goto=False, labels=_named_label,
                       braceless=True, line_statements=True, classes=frozenset({"class"}),
                       self_only=True,
                       binders=frozenset({"const", "let", "var", "function", "class"}))
_DIALECTS = {
    "CLikeReader": _Dialect(counting=_C_FAMILY_COUNTING, declarator_and=True, word_ops=_AND_OR,
                            elvis=True, overloads=True, braceless=True),
    "ObjCReader": _Dialect(counting=_C_FAMILY_COUNTING, declarator_and=True, messages=True,
                           word_ops=_AND_OR, elvis=True, overloads=True, braceless=True),
    "JavaReader": _Dialect(counting=_C_FAMILY_COUNTING, goto=False, labels=_named_label,
                           overloads=True, braceless=True),
    "JavaScriptReader": _JAVASCRIPT,
    "TypeScriptReader": _JAVASCRIPT,
    "TSXReader": _JAVASCRIPT,
    "VueReader": _JAVASCRIPT,
    "GoReader": _Dialect(counting=_GO_COUNTING, do_loops=frozenset(), labels=_named_label,
                         self_only=True, binders=frozenset({"var"}), assigns=frozenset({":="})),
    "SwiftReader": _Dialect(counting=_SWIFT_COUNTING, do_loops=frozenset({"repeat"}), goto=False,
                            labels=_named_label, overloads=True, binders=frozenset({"let", "var"}),
                            types=_swift_type, argument_labels=True),
    "RustReader": _RUST,
    "CorrectedRustReader": _RUST,
    "ShellReader": _Dialect(labels=_shell_label, shell_blocks=True, command_leads=_SHELL_LEADS,
                            bracket_lines=True),
    "PowerShellReader": _Dialect(counting=_POWERSHELL_COUNTING, goto=False, labels=_named_label,
                                 command_leads=_POWERSHELL_LEADS, fold_case=True,
                                 word_ops=_POWERSHELL_OPS, bracket_lines=True),
    "PythonReader": _PYTHON,
    "PythonSignatureReader": _PYTHON,
    "ZigReader": _Dialect(counting=_ZIG_COUNTING, do_loops=frozenset(), goto=False,
                          labels=_zig_label, word_ops=_AND_OR, braceless=True, types=_zig_type),
}

# The rules crapkit's reader fixes add: Rust's own syntax (see _Dialect.rust),
# the headers without parentheses of Go, Rust and Swift (_Dialect.bare_headers),
# Zig's `||`, which merges error sets, and Zig's `if` in a type. A reader crapkit
# subclasses reads under the rules of the lizard reader it corrects.
_DIALECTS.update(dict.fromkeys(("RustReader", "CorrectedRustReader"),
                               _RUST._replace(rust=True, bare_headers=True)))
_DIALECTS.update({reader: _DIALECTS[reader]._replace(bare_headers=True)
                  for reader in ("GoReader", "SwiftReader")})
_DIALECTS["ZigReader"] = _DIALECTS["ZigReader"]._replace(error_sets=True, if_types=True)
_DIALECTS.update({f"Corrected{stock}": _DIALECTS.get(stock, _DEFAULT_DIALECT)
                  for stock in ("GoReader", "SwiftReader", "ZigReader")})

# What a function calls itself through: nothing (but see _Dialect.self_only),
# one of these receivers, its own qualifier (`Calc::fact`, `K.fact`), its
# class's or type's name (see _Dialect.types) or, in Go, its receiver's name.
# A call through any other receiver is another object's method with the same
# name.
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
    bare_calls: bool        # a call through no receiver reaches it; see _bare_calls
    labels: tuple = ()      # (argument label, has a default) per parameter; see _labels


# How a call to the function's own name reaches it: through no receiver, which
# a name bound in the body can hide (see _cognitive), or through one of its own.
_BARE, _THROUGH = "bare", "through"

# A word as the pass reads a parameter: its name is the first word of `apply`,
# `apply: Fn` or `apply=None`, or the last of `Fn apply`.
_WORD = re.compile(r"[A-Za-z_$][\w$]*")


class _Scope(NamedTuple):
    """A class body, a def or a brace open in the token stream (see _Scopes).
    `indent` is a Python class's or def's; a brace has none. `kind` is
    "class", "def" or "" (a JavaScript block or object literal)."""

    indent: object
    kind: str
    name: str = ""


_BLOCK = _Scope(None, "")


class _Around(NamedTuple):
    """The stream where lizard starts a JavaScript function: the tokens before
    it, padded with two Nones in front and one behind, the brace open around
    it and the innermost class open. See _js_member."""

    recent: tuple
    top: object
    cls: str


# The Python words that open a scope a def can be defined in.
_PYTHON_SCOPES = frozenset({"class", "def"})

# A JavaScript name after one of these is the function's own binding.
_JS_DECLARERS = frozenset({"function", "const", "let", "var"})

# A JavaScript function's name followed by one of these is a method's: `walk(n)
# {` and `walk<T>(n: T) {` in a class body or an object literal, `walk: (n) =>`
# in an object literal or a class field with a type.
_METHOD_AFTER = frozenset({"(", "<", ":"})


class _Scopes:
    """The classes and defs open in the token stream, one per analysis pass.

    lizard names a Python or JavaScript method without its class (`open` for
    `A.open`), and a Rust, Swift or Zig function without its type, and a
    function's own tokens start after its name, so neither can tell a method
    from a function. The stream can: this reads what each function lizard
    starts is defined in (see _home).
    """

    __slots__ = ("classes", "types", "open", "naming", "pending", "home", "recent", "angles")

    def __init__(self, classes: bool, types=None):
        self.classes = classes  # the language has classes in braces; see _Dialect.classes
        self.types = types      # reads a type's header; see _Dialect.types
        self.open = []          # a _Scope per open class or def (Python) or brace
        self.naming = False     # Python: the next token names the scope just opened
        self.pending = None     # a class or type awaiting its name or its `{`
        self.home = None        # Python: the scope the last `def` stands in
        self.recent = deque(maxlen=8)  # the stream's last tokens
        self.angles = 0         # Rust: `<` open in an impl header, None past `where`

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


# What a bracket does to a depth read in brackets: where a Python signature
# ends (the first `:` with none open, so a colon in a default's lambda, a dict
# default, a slice in an annotation or a type parameter's bound is not the end),
# and which `:` finishes a conditional operator (the one at its `?`'s depth, so
# a key in an object literal arm is not it).
_BRACKET_DEPTH = {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}

# Words that start a statement, and so end the conditional operators before them
# where a line break is invisible (the Swift reader drops whitespace) or a
# statement needs no `;`.
_STATEMENT_WORDS = frozenset({"return", "let", "var", "const", "throw"})

# What ends the statement a `?` waiting for its `:` sits in, at the `?`'s depth.
# A `,` is not among them: `c ? f<A, B>() : d` puts one in the first arm.
_QUESTION_ENDS = frozenset({";"}) | _STATEMENT_WORDS

# What ends a conditional operator's second arm at its depth: `f(a ? b : c, d)`.
_ARM_ENDS = _QUESTION_ENDS | {","}

# The first tokens of a line that continue the statement on the line above: a
# call chain's `.` and a conditional operator's `?` and `:`. A line ending in a
# `?` or `:` continues too.
_CONTINUATIONS = frozenset({".", "?", ":"})

# What a bracket does to the depth a Swift parameter list is read at; see
# _swift_signature. A generic's angle brackets hold commas too.
_SWIFT_BRACKETS = {"(": 1, "[": 1, "<": 1, ")": -1, "]": -1, ">": -1, ">>": -2}


class _FnState:
    __slots__ = ("for_pending", "total", "stack", "max_depth", "brace_depth", "line_indent",
                 "at_line_start", "pending", "else_pending", "question_pending",
                 "bool_op", "fn", "own", "recursed", "body_started", "signature_depth",
                 "prev", "prev2", "label_check", "dialect", "call_pending", "call",
                 "messages", "runs", "run_break", "word_op", "braces", "closed_do",
                 "guard_else", "match_indent", "else_payload", "bracket_depth", "bodies", "annotation",
                 "do_tail",
                 "ended", "questions", "arms", "next_rule", "brace_base", "scopes", "home", "bare_call",
                 "shadowed", "importing",
                 "after_group", "own_calls", "closed_call", "signature")

    def __init__(self, fn=None, dialect: _Dialect = _DEFAULT_DIALECT, scopes: _Scopes | None = None):
        self.dialect = dialect
        self.fn = fn
        self.scopes = scopes     # the stream's, shared by every state of the pass
        self.home = _home(scopes) if scopes is not None else None  # what fn is defined in
        self.own = None          # see _own
        self.bare_call = False   # the body calls its own name through no receiver
        self.shadowed = False    # the body binds its own name; see _cognitive
        self.importing = False   # Python: inside an import statement; see _binds
        self.total = 0
        self.stack = []          # (entry_brace_depth) or python header indents
        self.max_depth = 0       # the deepest level reached: see _reach
        self.brace_depth = 0     # counted from where the function started; see _state_for
        self.brace_base = 0      # the stream's brace depth where the function started
        self.line_indent = 0
        self.at_line_start = True
        self.pending = []        # bracket depths of the structures awaiting their '{'
        self.next_rule = None    # what the next token settles: _resolve_reopen, _resolve_structure
        self.bracket_depth = 0   # brackets of every kind open; see _body_token
        self.questions = []      # bracket depths of `?`s whose `:` has not come
        self.arms = []           # bracket depths of conditional operators past their `:`
        self.bodies = []          # [bracket depth, phase, structure] per body; see _open_body
        self.annotation = None    # Zig: the bracket depth of an `if` in a type; see _initializer
        self.ended = None         # the depth a statement just ended at; see _end_statement
        self.else_pending = False
        self.else_payload = False  # Zig: inside an else's `|err|`; see _resolve_else
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
        self.closed_call = None   # Swift: a call's `)` just closed; see _trailing_closure
        self.own_calls = []       # calls that fit the function; see _settle_overloads
        self.signature = [""] if dialect.argument_labels else None  # see _swift_signature
        self.messages = []        # Objective-C: one entry per open `[`, see _message_token
        self.runs = []            # per open bracket: the run outside it; see _open_run
        self.after_group = None   # a plain group just closed; see _after_group
        self.run_break = False    # a line ended; the next token says whether the run did
        self.word_op = None       # `and`/`or` just seen; a `:` next makes it a selector part
        self.braces = []          # per open `{`: whether a `do` opened it; see _loop_tail
        self.closed_do = False    # the last `}` closed a `do`'s block
        self.do_tail = False      # a braceless `do`'s statement just ended; see _loop_tail
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
        scopes = _Scopes(bool(dialect.classes), dialect.types)
        last = None
        for token in tokens:
            if is_python:
                yield token  # the owner is read after lizard has; see _state_for
            fn = reader.context.current_function
            state = last = _state_for(states, fn, last, dialect, scopes)
            _step(state, token, is_python)
            fn.cognitive_complexity = _cognitive(state)
            fn.cognitive_nesting = state.max_depth
            if not is_python:
                yield token
        _settle_overloads(states)


def _state_for(states: dict, fn, last, dialect: _Dialect, scopes: _Scopes) -> _FnState:
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
    The bracket depth the body rules read belongs to the stream too: lizard
    hands a JavaScript arrow the `)` that closes the call it is passed to,
    `xs.map((v) => (v ? 1 : 2))`, and the function around it resumed two
    brackets deep, where no `;` could end its statement. So does the brace
    depth, which a function counts from where it started: lizard hands the
    function around an arrow the arrow's `{` and the arrow its `}`, and the
    function resumed one brace deep, so the block around the arrow never
    closed and every structure after it paid a level more.
    """
    state = states.get(fn)
    if state is None:
        state = states[fn] = _FnState(fn, dialect, scopes)
        state.brace_base = _stream_braces(last)
    if last is not None and state is not last:
        state.line_indent = last.line_indent
        state.at_line_start = last.at_line_start
        state.bracket_depth = last.bracket_depth
        state.brace_depth = _stream_braces(last) - state.brace_base
    return state


def _stream_braces(last) -> int:
    """The braces open in the token stream after the last token: the last
    state's own depth over the depth it started at."""
    return last.brace_depth + last.brace_base if last is not None else 0


# --- what a function is defined in ------------------------------------------------

def _home(scopes: _Scopes):
    """What the function lizard just started is defined in, read when its
    state is made: in Python the scope its `def` stands in; in JavaScript the
    stream around it, which _js_member reads once the name is final; in Rust,
    Swift and Zig the type body it stands directly in. Any other language
    reads None: nothing the rules need."""
    if scopes.types:
        return _type_body(scopes.open)
    if not scopes.classes:
        return scopes.home
    top = scopes.open[-1] if scopes.open else _BLOCK
    return _Around((None, None) + tuple(scopes.recent) + (None,), top, _innermost_class(scopes.open))


def _innermost_class(open_scopes: list) -> str:
    return next((s.name for s in reversed(open_scopes) if s.kind == "class"), "")


def _type_body(open_scopes: list):
    """The type body on top of the stack, or None: a function in a block, or
    in a function inside the type, is no member of it."""
    top = open_scopes[-1] if open_scopes else _BLOCK
    return top if top.kind == "class" else None


def _track_scopes(state: _FnState, token: str, is_python: bool) -> None:
    scopes = state.scopes
    if is_python:
        _python_scopes(scopes, state, token)
    elif scopes.classes or scopes.types:
        _brace_scopes(scopes, token)
    scopes.recent.append(token)


def _python_scopes(scopes: _Scopes, state: _FnState, token: str) -> None:
    """A statement's first token closes the classes and defs its indent has
    left; `class` and `def` open one, which the next token names."""
    if _statement_start(state):
        _leave_scopes(scopes, state.line_indent)
    if scopes.naming:
        scopes.naming = False
        scopes.open[-1] = scopes.open[-1]._replace(name=token)
    elif token in _PYTHON_SCOPES and _header_start(state, scopes):
        _enter_scope(scopes, _Scope(state.line_indent, token))


def _header_start(state: _FnState, scopes: _Scopes) -> bool:
    return _statement_start(state) or _last(scopes.recent) == "async"


def _last(recent) -> str:
    return recent[-1] if recent else ""


def _leave_scopes(scopes: _Scopes, indent: int) -> None:
    while scopes.open and indent <= scopes.open[-1].indent:
        scopes.open.pop()


def _enter_scope(scopes: _Scopes, scope: _Scope) -> None:
    scopes.home = scopes.open[-1] if scopes.open else None
    scopes.open.append(scope)
    scopes.naming = True


def _brace_scopes(scopes: _Scopes, token: str) -> None:
    """One scope per open brace: the class body a `class` word named (a type
    header, in a language with _Dialect.types), or a block. lizard's JSX
    tokens swallow some braces, so the stack can drift in a .tsx file; the
    rules read it only for a class field and a class's name (see _js_member)."""
    if token == "{":
        scopes.open.append(_opened_scope(scopes))
    elif token == "}":
        _close_scope(scopes)
    else:
        (scopes.types or _class_name)(scopes, token)


def _opened_scope(scopes: _Scopes) -> _Scope:
    pending, scopes.pending = scopes.pending, None
    return _BLOCK if pending is None else pending


def _close_scope(scopes: _Scopes) -> None:
    if scopes.open:
        scopes.open.pop()


def _class_name(scopes: _Scopes, token: str) -> None:
    """`class A` names the class body the next `{` opens. `x.class` and a
    `class:` key open nothing."""
    if scopes.pending is not None and not scopes.pending.name:
        scopes.pending = _named(scopes.pending, token)
    elif token == "class" and _last(scopes.recent) not in _MEMBER_ACCESS:
        scopes.pending = _Scope(None, "class")


def _named(pending: _Scope, token: str):
    return pending._replace(name=token) if _WORD.fullmatch(token) else None


def _js_member(around: _Around, bare: str) -> bool:
    """Whether a JavaScript function is a method or a property's value, read
    off the tokens around its name. `function walk` and `const walk =` bind
    the name to the function itself. `walk(n) {` in a class body or an object
    literal, `walk: (n) =>` and `obj.walk = function` do not, and neither
    does a class field, `walk = (n) =>`, whose brace is a class body. A name
    the window does not hold reads as a function, as every name did before."""
    recent = around.recent
    if bare not in recent:
        return False
    i = len(recent) - 1 - recent[::-1].index(bare)
    if _declared(recent[i - 2], recent[i - 1]):
        return False
    return recent[i + 1] in _METHOD_AFTER or (recent[i + 1] == "=" and _property(recent[i - 1], around))


def _declared(second: str, first: str) -> bool:
    """`function walk`, `function* walk`, `const walk`."""
    return first in _JS_DECLARERS or (first == "*" and second == "function")


def _property(before: str, around: _Around) -> bool:
    """`obj.walk = ...`, or a class field."""
    return before == "." or around.top.kind == "class"


def _step(state: _FnState, token: str, is_python: bool) -> None:
    if not token.strip():
        _line_event(state, token)
        return
    if token.startswith(("#", "//", "/*")):
        return  # a comment token must never read as code, whatever it contains
    _track_scopes(state, token, is_python)
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
        state.label_check = False  # a jump's label stands on its line
        state.at_line_start = True
        state.run_break = _may_end_statement(state)
        state.line_indent = len(token) - token.rfind("\n") - 1
    elif state.at_line_start:
        state.line_indent += len(token)


def _may_end_statement(state: _FnState) -> bool:
    """Whether a line break can end the run of logical operators.

    Not inside a bracket, unless the language's brackets hold commands (see
    _Dialect.bracket_lines), and not after an operator or a backslash, which
    continue the expression on the next line. Whether a line that starts with
    an operator continues it is for that line's first token to say; see
    _settle_line.
    """
    after_operator = _is_bool_op(state, state.prev) or state.prev == "\\"
    return (not state.runs or state.dialect.bracket_lines) and not after_operator


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
    has left, and the import statement the line before held. A line inside a
    bracket continues a statement, and its indent closes nothing."""
    if not _statement_start(state):
        return
    state.importing = False
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
    if state.next_rule is not None:
        state.next_rule(state, token)
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
    Zig gives an else a payload, `else |err| if (...)`, and the token after the
    payload's closing `|` is the one that decides: read at the first `|`, the
    else was a plain one and the `if` after the payload paid +1 of its own.
    """
    if token == "|":
        state.else_payload = not state.else_payload  # the payload opens or closes
        return False
    if state.else_payload:
        return False  # a name the payload binds
    state.else_pending = False
    if token == "=>":
        state.total -= 1
        return False
    _else_body(state, token)
    return token == "if"


def _else_body(state: _FnState, token: str) -> None:
    """The else's block, or where a body can go without braces, its body: an
    else-if's is the `if`'s, with a header. Zig's `else =>` is a switch's
    default prong, which has no body of an else."""
    if not state.dialect.braceless:
        state.pending.append(state.bracket_depth)
    elif token == "if":
        state.bodies.append([state.bracket_depth, _FRESH, "if"])
    elif token != "=>":
        state.bodies.append([state.bracket_depth, _AWAITING, "else"])


def _resolve_words(state: _FnState, token: str) -> None:
    """What the word just before this token turned out to be."""
    if state.label_check:
        _resolve_label(state, token)
    if state.call_pending:
        _resolve_call(state, token)
    if state.word_op:
        _resolve_word_op(state, token)


def _resolve_reopen(state: _FnState, token: str) -> None:
    """The token after a `}` that closed a structure's block in Go, Rust or Swift.

    Their headers have no parentheses, so a block in a header sits at the depth
    of the body's `{` and takes the structure's level: a composite literal
    (`range []int{1, 2} {`), a struct type's fields and then its literal
    (`range []struct{ n int }{{1}} {`), `unsafe { ... }`, a function literal
    called in an if's initializer. A `{` or `(` on the same line says the
    block was such an operand, and the structure waits for its body again,
    unless another structure in the header is already waiting at that depth
    (`if match x { ... } {`). A block that was a body ends its line, or goes on
    with `else` or `catch`.
    """
    state.next_rule = None
    depth = state.bracket_depth
    if token in ("{", "(") and not state.at_line_start and depth not in state.pending[-1:]:
        state.pending.append(depth)


def _resolve_question(state: _FnState, token: str, is_python: bool) -> bool:
    """A `?` is a conditional unless the token after it says otherwise. True when
    this token is the second `?` of `??` (or `??=`), which lizard's JavaScript
    and PowerShell tokenizers split in two: a null-coalescing operator costs
    nothing (Sonar v1.7, Ignore shorthand), so the pair is consumed."""
    state.question_pending = False
    if token == "?":
        del state.questions[-1:]
        return True
    if _is_conditional(state, token):
        state.total += 1 + _nesting(state, is_python)
        _conditional_run(state)
    else:
        del state.questions[-1:]  # it opens no arms either; see _colon
    return False


def _conditional_run(state: _FnState) -> None:
    """A conditional ends its condition's run, and each operand has its own.
    In a plain group the conditional makes the group one operand of the
    sequence around it, which resumes after the group as it does after a
    call's arguments: `a && (b ? c : d) && e` is one sequence and the
    conditional, 2. Read left to right through the group, the `&&` after it
    started a new sequence, and the shape read 3. The condition is a
    sequence of its own too; see _operand_group."""
    state.bool_op = None
    if state.runs and state.runs[-1][1]:
        _operand_group(state, state.runs[-1])
        state.runs[-1][1] = False


def _operand_group(state: _FnState, run: list) -> None:
    """A plain group turned out to be an operand, of a conditional or a
    comparison, so the operators in it were a sequence of their own. When the
    first of them continued the run outside the group it paid nothing, and
    it pays its +1 now: `a && (b && c ? d : e) && a` holds three sequences,
    3, where it read 2."""
    state.total += run[2] is True


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
        _charge_structure(state, "for", is_python)


def _resolve_label(state: _FnState, token: str) -> None:
    """A break or continue costs +1 only when it jumps to a label, and the
    token after it says whether it does, as `_Dialect.labels` reads it."""
    state.label_check = False
    if state.dialect.labels(token):
        state.total += 1


def _resolve_call(state: _FnState, token: str) -> None:
    """The name followed by `(` is a call. Where a name can be overloaded the
    call is to this function only if it passes as many arguments as this one
    takes, which is known at the closing bracket (see _follow_call), and no
    other function of the name in the file takes them too (see
    _settle_overloads). The pass sees no types, so a call to an overload that
    takes as many arguments of other types reads as a call to neither, and a
    call a macro qualifies (`FMT_POSIX_CALL(close(fd))` expands to `::close`)
    reads as a call to this one.

    In Swift the name followed by `{` passes a trailing closure and nothing
    else (see _takes_closure). The bare name followed by `=` or `:=` instead
    binds it (see _binds)."""
    kind, state.call_pending = state.call_pending, None
    bare = kind == _BARE
    if token == "(":
        _call(state, bare)
    elif _takes_closure(state, token):
        _called(state, _Call(bare, 1, {0: None}, True))
    elif bare and _assigns(state, token):
        state.shadowed = True


def _call(state: _FnState, bare: bool) -> None:
    if state.dialect.overloads:
        # depth inside, commas, tokens, whether through no receiver, labels by argument
        state.call = [len(state.runs) + 1, 0, -1, bare, {}]
    else:
        _count_recursion(state, True, bare)


def _assigns(state: _FnState, token: str) -> bool:
    """`walk = ...` or `walk := ...`. A `=` inside a bracket passes a keyword
    argument, `dumps(obj, dumps=1)`, and binds nothing."""
    return token in state.dialect.assigns and (token == ":=" or not state.runs)


def _resolve_word_op(state: _FnState, token: str) -> None:
    """An `and` or `or` followed by `:` names an Objective-C selector part
    (`- (int)join:(int)a and:(int)b`); anything else makes it the operator."""
    op, state.word_op = state.word_op, None
    if token != ":":
        _bool_op(state, op)


def _nesting(state: _FnState, is_python: bool) -> int:
    """The blocks open around the token, and the bodies without braces."""
    return len(state.stack) + sum(1 for body in state.bodies if body[1] in _HOLDING)


def _reach(state: _FnState, depth: int) -> None:
    """The deepest level the function reaches, which is its `nesting` column.
    Every level passes through here: a pushed block, a body with no braces and
    the arms of a conditional operator."""
    state.max_depth = max(state.max_depth, depth)


def _push(state: _FnState, entry) -> None:
    """One more open block."""
    state.stack.append(entry)
    _reach(state, _nesting(state, False))


# --- the arms of a conditional operator ------------------------------------------
#
# A conditional operator's arms are a level no block holds (a body without
# braces is the other; see the section on bodies below). They end with the
# statement they sit in: a `;` or a word that starts a statement at the `?`'s
# bracket depth, a `,` for the second arm, the close of a bracket around them,
# and a line break the next line does not continue, since a line may end a
# statement that has no `;`.

def _arms_token(state: _FnState, token: str) -> None:
    """One token of a brace language, read for the conditional operators whose
    `:` has not come and for those past it. Most tokens touch neither, and pay
    three tests."""
    if state.at_line_start and not _continues(state, token):
        _forget_arms(state, state.bracket_depth)
    step = _ARM_STEPS.get(token)
    if step is not None:
        step(state, token)


def _continues(state: _FnState, token: str) -> bool:
    """Whether a line's first token carries on the statement above it: it is,
    or it follows, one of `_CONTINUATIONS`."""
    return token in _CONTINUATIONS or state.prev in _CONTINUATIONS


def _forget_arms(state: _FnState, depth: int) -> None:
    _forget(state.questions, depth)
    _forget(state.arms, depth)


def _arms_bracket(state: _FnState, token: str) -> None:
    """A bracket moves the depth the arms are read at, where the body rules do
    not already keep it, and a closing one ends the operators inside it."""
    if not state.dialect.braceless:
        state.bracket_depth += _BRACKET_DEPTH[token]
    if _BRACKET_DEPTH[token] < 0:
        _forget_arms(state, state.bracket_depth + 1)
        _forget(state.pending, state.bracket_depth + 1)  # no structure waits past its bracket


def _arms_statement(state: _FnState, token: str) -> None:
    depth = state.bracket_depth
    if token in _QUESTION_ENDS:
        _forget(state.questions, depth)
    _forget(state.arms, depth)


def _colon(state: _FnState, _token: str) -> None:
    """The `:` of a conditional operator: its arms sit one level below the
    levels around them and below every conditional operator they sit in, so
    `a ? 1 : b ? 2 : 3` reaches 2."""
    if state.questions and state.questions[-1] == state.bracket_depth:
        state.questions.pop()
        if _opens_arms(state):
            _reach(state, _nesting(state, False) + len(state.questions) + len(state.arms) + 1)
            state.arms.append(state.bracket_depth)


def _opens_arms(state: _FnState) -> bool:
    """A conditional operator's arms are a level, except in shell, whose
    arithmetic `?:` counts in ccn and cognitive only, and in a C-family default
    argument, which is evaluated where the function is called: `int f(int a =
    k ? 1 : 2)` opens nothing in `f`."""
    return not (state.dialect.shell_blocks or state.dialect.declarator_and and state.brace_depth == 0)


def _arm(state: _FnState, _token: str) -> None:
    """A Rust match arm's `=>`: an `if` before it is the arm's guard, which has
    no body, so it stops waiting for one. Waiting on, it took the `{` of the
    next arm with a block for its body, a level no arm opens."""
    if state.dialect.rust:
        _forget(state.pending, state.bracket_depth)


def _forget(depths: list, depth: int) -> None:
    """Drop the entries at `depth` or deeper, whose statement or bracket ended."""
    while depths and depths[-1] >= depth:
        depths.pop()


# The tokens the rules above read, each to the rule that reads it.
_ARM_STEPS = {":": _colon, "=>": _arm, **dict.fromkeys(_BRACKET_DEPTH, _arms_bracket),
              **dict.fromkeys(_ARM_ENDS, _arms_statement)}


def _consume(state: _FnState, token: str, is_python: bool) -> None:
    if _signature(state, token, is_python):
        return
    _observe(state, token)
    if not is_python:
        _arms_token(state, token)
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
    if state.after_group is not None:
        _after_group(state, token)
    _follow_runs(state, token)
    if isinstance(state.signature, list):
        _swift_signature(state, token)
    _follow_calls(state, token)
    if state.dialect.messages:
        _message_token(state, token)
    if state.dialect.braceless:
        _body_token(state, token)


def _swift_signature(state: _FnState, token: str) -> None:
    """Swift: split the parameter list at its own commas, as lizard does not.
    lizard splits it at every comma, inside a closure's type too, so
    `perform handler: (_ r: R, _ done: D) -> Void` read as two parameters,
    and a call that passes one closure fit no function of the name. The
    list is the first bracket before the body; see _parameters."""
    if _before_parameters(state, token):
        return
    inside = state.signature_depth
    state.signature_depth += _SWIFT_BRACKETS.get(token, 0)
    if state.signature_depth == 0:
        state.signature = _closed_signature(state.signature)
    elif inside:
        _parameter_token(state.signature, token, state.signature_depth)


def _closed_signature(pieces: list) -> tuple:
    return tuple(piece.strip() for piece in pieces if piece.strip())


def _before_parameters(state: _FnState, token: str) -> bool:
    return state.brace_depth > 0 or (state.signature_depth == 0 and token != "(")


def _parameter_token(pieces: list, token: str, depth: int) -> None:
    if token == "," and depth == 1:
        pieces.append("")
    else:
        pieces[-1] += " " + token


def _follow_calls(state: _FnState, token: str) -> None:
    if state.closed_call is not None:
        _trailing_closure(state, token)
    if state.call is not None:
        _follow_call(state, token)


def _follow_call(state: _FnState, token: str) -> None:
    """Count the arguments of a call to the function's own name, one per comma
    at the call's own depth, and judge the call when its bracket closes.

    `format(date)` inside `format(Date date, boolean millis)` delegates to
    another overload, and one argument is not two. A statement that ends
    inside the brackets, a lambda's block in an argument, leaves the rest of
    the arguments unknown, and the call is judged by those seen so far.
    """
    call = state.call
    if len(state.runs) < call[0]:
        state.call = None
        _call_ended(state, call, token in state.dialect.closers)
    elif len(state.runs) == call[0]:
        _argument_token(state, token, call)
    call[2] += 1


def _argument_token(state: _FnState, token: str, call: list) -> None:
    """A token at the call's own depth: a comma starts the next argument, and
    in Swift a word right after `(` or `,` and before `:` is its label."""
    if token == ",":
        call[1] += 1
    elif token == ":" and state.dialect.argument_labels and state.prev2 in ("(", ","):
        call[4][call[1]] = state.prev


class _Call(NamedTuple):
    """A call to the function's own name, as far as its arguments go. `labels`
    maps an argument's position to its Swift label, None for a trailing
    closure's. `closed`: its bracket closed, so `passed` is every argument."""

    bare: bool
    passed: int
    labels: dict
    closed: bool


def _call_ended(state: _FnState, call: list, closed: bool) -> None:
    """In Swift a closure after the `)` passes one more argument, which the
    next token shows; see _trailing_closure."""
    ended = _Call(call[3], call[1] + 1 if call[2] else 0, call[4], closed)
    if closed and state.dialect.argument_labels:
        state.closed_call = ended
    else:
        _called(state, ended)


def _trailing_closure(state: _FnState, token: str) -> None:
    """The token after a Swift call's `)`. A `{` there passes a trailing
    closure, `each(n - 1) { body($0) }`, which the call used to lose, so a
    function taking the closure read no recursion."""
    call, state.closed_call = state.closed_call, None
    if _takes_closure(state, token):
        call = call._replace(passed=call.passed + 1, labels={**call.labels, call.passed: None})
    _called(state, call)


def _takes_closure(state: _FnState, token: str) -> bool:
    """A Swift `{` right after a call passes a closure, unless the call ends
    a structure's header, `if valid(n - 1) {`, where the `{` opens the
    structure's block."""
    return token == "{" and state.dialect.argument_labels and not (state.pending or state.guard_else)


def _called(state: _FnState, call: _Call) -> None:
    """A call to the function's own name that passes what the function takes
    is recursion unless another function of the name takes it too, which
    the end of the file shows; see _settle_overloads."""
    if _fits(_own(state), call, state.dialect):
        state.own_calls.append(call)


def _fits(own: _Own, call: _Call, dialect: _Dialect) -> bool:
    """Whether the call passes as many arguments as the function takes, and in
    Swift with its labels. A call whose bracket did not close passed at least
    the arguments seen so far."""
    fewest, most = own.arity
    if call.passed > most or (call.closed and call.passed < fewest):
        return False
    return _labels_fit(own, call, dialect)


def _labels_fit(own: _Own, call: _Call, dialect: _Dialect) -> bool:
    """Swift: each argument takes the next parameter with its label, `_` for
    none; a parameter with a default can be passed over, and one after the
    last argument must have a default. A variadic parameter's labels are not
    followed."""
    if not dialect.argument_labels or own.arity[1] == math.inf:
        return True
    labels = [call.labels.get(number, "_") for number in range(call.passed)]
    return _labels_in_order(labels, own.labels, call.closed)


def _labels_in_order(labels: list, parameters: tuple, closed: bool) -> bool:
    """A call cut short by a closure's block, `cancel(other: { _ in })`,
    fits by the labels seen so far. It used to count whatever they were."""
    rest = list(parameters)
    for label in labels:
        rest = _after_argument(rest, label)
    return rest is not None and (not closed or all(default for _, default in rest))


def _after_argument(rest, label):
    if label is None:
        return _after_closure(rest)
    return _after_label(rest, label)


def _after_label(rest, label: str):
    """The parameters after the one the label takes, or None when none can."""
    for number, (want, default) in enumerate(rest or ()):
        if want == label:
            return rest[number + 1:]
        if not default:
            return None
    return None


def _after_closure(rest):
    """A trailing closure takes the next parameter without a default, past
    those with one, as Swift matches it forward to the parameter that takes a
    function; when every parameter left has a default, it takes the last."""
    if not rest:
        return None
    required = [number for number, (_, default) in enumerate(rest) if not default]
    return rest[required[0] + 1:] if required else []


def _follow_runs(state: _FnState, token: str) -> None:
    """Keep one run of logical operators per open bracket.

    A comma ends the run it sits in, which is one argument's or one element's,
    and so does a `:`, which ends a conditional's middle operand, a key or a
    label. A statement's end ends every run. See _open_run and _close_run for
    the brackets.
    """
    if token in state.dialect.openers:
        _open_run(state, token)
    elif token in state.dialect.closers:
        _close_run(state)
    elif token in (",", ":"):
        state.bool_op = None
    elif token in _STATEMENT_ENDS:
        state.bool_op = None
        state.runs.clear()


def _open_run(state: _FnState, token: str) -> None:
    """A bracket after a name (a call's arguments, an index) or after a negation
    holds a run of its own, and the paper counts `a && !(b && c)` as two runs.
    So does a `[` or a `{`, a list, a dict or an Objective-C message, whose
    elements are expressions of their own. A `(` anywhere else is a plain
    group, which continues the run outside it: `a && (b && c)` is one. See
    _close_run."""
    grouping = token == "(" and _groups(state)
    state.runs.append([state.bool_op, grouping, None])  # see _first_in_groups
    if not grouping:
        state.bool_op = None


def _groups(state: _FnState) -> bool:
    """Whether the bracket opening here is a plain group: not a call's or an
    index's, not negated, and not the operand of a comparison or an arithmetic
    operator (see _TIGHTER)."""
    prev = state.prev
    if prev in _NEGATIONS or _binds_tighter(state, prev):
        return False
    if prev in _GROUPING_WORDS:
        return True
    return not (prev[:1].isalnum() or prev[:1] in ("_", "$", ")", "]"))


def _binds_tighter(state: _FnState, token: str) -> bool:
    if token in _TIGHTER:
        return True
    return token[:1] == "-" and token[1:2].isalpha() and not _is_bool_op(state, token)


def _close_run(state: _FnState) -> None:
    """A call's arguments, an index and a negated group hand the run outside
    them back. A plain group leaves the run where its last operator left it,
    so the operators read left to right through it, as Sonar's reference
    implementation flattens a logical expression: `a && (b || c) && d`
    changes operator twice and costs 3, and `(a && b) && c` is one run.
    Handing the outer run back read the first 2."""
    if not state.runs:
        state.bool_op = None
        return
    run = state.runs.pop()
    outer, grouping, _ = run
    if grouping:
        state.after_group = run  # the next token decides; see _after_group
    else:
        state.bool_op = outer
    _close_comprehensions(state)


def _after_group(state: _FnState, token: str) -> None:
    """The token after a plain group says whether the group was an operand of
    the sequence around it. An operator that binds tighter, a call, an index
    or a member access makes it the operand of something else, `(a || b) ==
    c`, and the run outside the group resumes, as after a call's arguments."""
    run, state.after_group = state.after_group, None
    if token in _APPLIED or _binds_tighter(state, token):
        _operand_group(state, run)
        state.bool_op = run[0]


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
        state.signature_depth += _BRACKET_DEPTH.get(token, 0)


def _brace(state: _FnState, token: str) -> None:
    if "{" in state.dialect.openers:
        return  # a bracket, which _follow_runs keeps; its depth is no block's
    if token == "{":
        _open_brace(state)
    else:
        _close_brace(state)


def _open_brace(state: _FnState) -> None:
    """A `{` is a waiting structure's body only at the structure's own bracket
    depth: one inside its header's brackets (`for (auto x : {1, 2})`,
    `if (f(() -> { ... }))`) is a literal's or a lambda's. The arms pass (or,
    where bodies can go without braces, `_body_token`) has already counted this
    `{`, so the depth outside it is one less."""
    if state.dialect.braceless:
        _brace_body(state)
    elif state.pending and state.pending[-1] == state.bracket_depth - 1:
        state.pending.pop()
        _push(state, state.brace_depth)
    state.braces.append(state.prev in state.dialect.do_loops)
    state.brace_depth += 1


def _close_brace(state: _FnState) -> None:
    """A `}` closes a level opened at its depth, and says whether it closed a
    `do`'s block; see _loop_tail."""
    state.brace_depth -= 1
    state.closed_do = state.braces.pop() if state.braces else False
    if state.stack and state.stack[-1] == state.brace_depth:
        state.stack.pop()
        if state.dialect.bare_headers:
            state.next_rule = _resolve_reopen


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
    _first_in_groups(state, op)
    if op != state.bool_op:
        state.total += 1
        state.bool_op = op


def _first_in_groups(state: _FnState, op: str) -> None:
    """Note, in each plain group open around the operator that holds no
    operator yet, whether this first one continues the run outside the group
    and so pays nothing (see _operand_group)."""
    for run in reversed(state.runs):
        if not run[1] or run[2] is not None:
            return
        run[2] = op == state.bool_op


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
    (see _open_brace), or after the statement a braceless `do` holds, `do
    a--; while (a > 0);` (see _end_statement). Reading every `while` after a
    `}` as a tail made the loop after an `if` block, or after a Python dict,
    cost nothing.
    """
    if token != "while":
        return False
    tail, state.do_tail = state.do_tail, False
    return tail or (state.prev == "}" and state.closed_do)


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
    step = _WORD_STEPS.get(token)
    if step is None:
        _recursion(state, token)
    else:
        step(state, token, is_python)


def _question(state: _FnState, _token: str, _is_python: bool) -> None:
    """A C ternary, a Swift optional or a Kotlin elvis waits one token to be
    told apart, where the reader counts a `?` at all. Rust's `?` is none of
    them: it returns early on an error or relaxes a `?Sized` bound, and an
    early return is no increment."""
    state.question_pending = _counts_question(state)
    if state.question_pending:
        state.questions.append(state.bracket_depth)  # its `:` opens the arms


def _jump(state: _FnState, _token: str, is_python: bool) -> None:
    state.label_check = not is_python


def _goto(state: _FnState, token: str, _is_python: bool) -> None:
    if state.dialect.goto:
        state.total += 1
    else:
        _recursion(state, token)


def _arrow(state: _FnState, _token: str, _is_python: bool) -> None:
    """An arrow's body starts after its `=>`, and it can be an expression that
    no brace opens: `(n) => n ? n * fact(n - 1) : 1`."""
    state.body_started = True


def _import(state: _FnState, token: str, _is_python: bool) -> None:
    """An import statement binds the names it lists (see _binds). In a
    language without one the word is a name."""
    if token in state.dialect.imports:
        state.importing = True
    else:
        _recursion(state, token)


def _statement_end(state: _FnState, _token: str, _is_python: bool) -> None:
    state.importing = False


# The words the jump and recursion rules read, each to the rule that reads it.
_WORD_STEPS = {"?": _question, "break": _jump, "continue": _jump, "goto": _goto,
               "=>": _arrow, "import": _import, "use": _import, ";": _statement_end}

# In an import statement a name after one of these is one the statement binds:
# `from json import loads, dumps`, `use std::os::unix::fs::symlink;`.
_IMPORTED_AFTER = frozenset({",", "(", "{", "::"})


def _recursion(state: _FnState, token: str) -> None:
    """+1 once when the body calls the function itself (Sonar B1: each method
    in a recursion cycle). The token must name the function and stand where a
    call stands; a local variable, a field or another object's method spelled
    the same way calls nothing, and a name the body binds is that value's
    (see _binds)."""
    if state.recursed or not _in_body(state):
        return
    if state.dialect.command_leads is not None:
        _count_recursion(state, _commands_itself(state, token))
    elif _binds(state, token):
        state.shadowed = True
    else:
        state.call_pending = _names_itself(state, token)


def _in_body(state: _FnState) -> bool:
    """Past the declaration. A shell function's state holds its body alone:
    lizard hands the name and the brace to the enclosing scope."""
    return state.body_started or state.brace_depth > 0 or state.dialect.shell_blocks


def _binds(state: _FnState, token: str) -> bool:
    """The token binds the function's own name in the body: after one of the
    language's binders (`const walk`, `import walk`, `for walk in`), or in an
    import statement's list (see _IMPORTED_AFTER). `walk = ...` binds it
    too, which the token after the name says; see _resolve_call."""
    if token != _own(state).bare:
        return False
    return state.prev in state.dialect.binders or (state.importing and state.prev in _IMPORTED_AFTER)


def _names_itself(state: _FnState, token: str):
    """How the function's own name would call it if a `(` came next (see
    _resolve_call): _BARE through no receiver, where a bare call reaches it at
    all (see _bare_calls), _THROUGH one of its own receivers (see
    _SELF_RECEIVERS), and None when it does not name the function."""
    own = _own(state)
    if token != own.bare:
        return None
    if state.prev in _MEMBER_ACCESS:
        return _THROUGH if state.prev2 in own.receivers else None
    return _BARE if own.bare_calls else None


def _own(state: _FnState) -> _Own:
    """Read once, in the body. lizard builds the name while it reads the
    declaration, so the name at a function's first token is often not its
    name: empty in Go and PowerShell, `K::int` for a Java method returning int,
    the class alone for `File File::open(`."""
    if state.own is None:
        state.own = _own_of(state)
    return state.own


def _own_of(state: _FnState) -> _Own:
    fn = state.fn
    name = getattr(fn, "name", "")
    parts = _QUALIFIER.split(name)
    go_receiver = _GO_RECEIVER.findall(getattr(fn, "long_name", ""))
    parameters = _parameters(state)
    method, cls = _membership(state.home, parts[-1])
    receivers = _SELF_RECEIVERS.union(parts[-2:-1], go_receiver, cls)
    return _Own(name, parts[-1], receivers, _arity(parameters),
                _bare_calls(state.dialect, parts[-1], parameters, method or bool(go_receiver)),
                _labels(parameters))


def _parameters(state: _FnState):
    """The function's parameters as spelled, one string each: the ones the
    stream read in Swift (see _swift_signature), lizard's elsewhere."""
    if isinstance(state.signature, tuple):
        return state.signature
    return getattr(state.fn, "full_parameters", ())


def _membership(home, bare: str) -> tuple:
    """Whether the function is a method, and the class whose name reaches it:
    `T.walk(n - 1)`."""
    if isinstance(home, _Around):
        return _js_membership(home, bare)
    if home is not None and home.kind == "class":
        return True, [home.name]
    return False, []


def _js_membership(around: _Around, bare: str) -> tuple:
    member = _js_member(around, bare)
    return member, [around.cls] if member and around.cls else []


def _bare_calls(dialect: _Dialect, bare: str, parameters, method: bool) -> bool:
    """Whether a call to the bare name reaches the function.

    Not from a method, where a method is reached only through its object or
    its class (see _Dialect.self_only): `return open(self.path)` in a method
    `open` calls the builtin. Not where a parameter spelled like the function
    hides it, in the languages whose bindings the pass reads (see
    _Dialect.binders); Java looks a method's name up apart from a variable's.
    """
    if method and dialect.self_only:
        return False
    return not (dialect.binders and _is_parameter(bare, parameters))


def _is_parameter(name: str, parameters) -> bool:
    return any(name in _parameter_names(spelled) for spelled in parameters)


def _parameter_names(spelled: str):
    """The words a parameter may be named by: the last one before a type's
    `:` (`apply: Fn`, Swift's `_ apply: Fn`), otherwise the first or the last
    (`apply func()` in Go, `Fn apply` in C++). A default is no name."""
    head, colon, _ = spelled.split("=")[0].partition(":")
    words = _WORD.findall(head)
    if colon:
        return words[-1:]
    return (words[0], words[-1]) if words else ()


def _arity(parameters) -> tuple:
    """A parameter with a default can be left out, and a variadic one (`...`)
    takes any number, as lizard spells them in C++, Java and Swift."""
    kinds = [_parameter_kind(p.strip()) for p in parameters if p.strip()]
    most = math.inf if "variadic" in kinds else len(kinds)
    return kinds.count("required"), most


def _labels(parameters) -> tuple:
    """(argument label, has a default) per parameter, as Swift spells them:
    `of request: URLRequest` is labeled `of`, `x: Int` is labeled `x`, and
    `_ x: Int` has none, `_`."""
    return tuple((_label(p), "=" in p) for p in parameters if p.strip())


def _label(spelled: str) -> str:
    words = _WORD.findall(spelled.partition(":")[0])
    return words[0] if words else "_"


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


def _settle_overloads(states: dict) -> None:
    """Judge each call to a function's own name once the file has shown every
    function of that name (C++, Java, Swift). A call that another of them
    takes too is that one's as much as this one's, and the pass sees no types
    to tell them apart, so only a call no other one takes is recursion:
    `starts_with(V(s))` inside `starts_with(const char* s)` forwards to
    `starts_with(V sv)`, and read 1."""
    for state in states.values():
        if state.own_calls:
            _settle(state, [other for other in states.values() if _overloads(other, state)])


def _overloads(other: _FnState, state: _FnState) -> bool:
    """Another function of the same name in the same type. One with the same
    parameters is the same function written twice, in two preprocessor
    branches, and no overload."""
    if other is state or other.fn.name != state.fn.name or other.home != state.home:
        return False
    return list(_parameters(other)) != list(_parameters(state))


def _settle(state: _FnState, others: list) -> None:
    for call in state.own_calls:
        if not any(_fits(_own(other), call, state.dialect) for other in others):
            _count_recursion(state, True, call.bare)
    state.fn.cognitive_complexity = _cognitive(state)


def _count_recursion(state: _FnState, calls_itself: bool, bare: bool = False) -> None:
    """A call to the function itself. One through no receiver waits for the
    end of the body, where a binding of the name still hides it."""
    if not calls_itself:
        return
    if bare:
        state.bare_call = True
    else:
        state.recursed = True


def _cognitive(state: _FnState) -> int:
    """The total so far, and +1 for recursion. A bare call counts unless the
    body binds the name anywhere: Python makes a bound name local to the whole
    body, so `dumps(obj)` before `from json import dumps` never reaches the
    function, and JavaScript's `const` and `let` shadow from the start of
    their block."""
    recursed = state.recursed or (state.bare_call and not state.shadowed)
    return state.total + recursed


def _counts_question(state: _FnState) -> bool:
    """A `?` waits to be told apart only where the reader counts one in ccn, and
    never in Rust. The reader's condition set is read now, at the `?`."""
    return "?" in state.dialect.conditions and not state.dialect.rust


def _if_token(state: _FnState, is_python: bool) -> None:
    if is_python and not _statement_start(state):
        state.total += 1 + _nesting(state, is_python)  # ternary expression form
        _conditional_run(state)
        return
    _structure(state, "if", is_python)


def _else_token(state: _FnState, token: str, is_python: bool) -> None:
    if is_python and not _statement_start(state):
        state.bool_op = None  # the else arm of a ternary expression is part of its +1
        return
    if state.guard_else:
        state.guard_else = False
        state.pending.append(state.bracket_depth)  # the guard's block, which the guard paid for
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
    """A structure keyword: `if`, a loop, `switch`, `catch`. A brace language's
    is charged at the token after it, which says whether it is one."""
    if is_python:
        _charge_structure(state, token, is_python)
    else:
        state.next_rule = _resolve_structure


def _resolve_structure(state: _FnState, token: str) -> None:
    """The token after a brace language's structure keyword. No structure is
    followed by a `:`, while an object's key, a type's member and a Swift
    argument label spelled like one are (`{if: 1, do: 2}`, `g(for: x)`). Each
    such word paid +1 and opened a body that the `,` after it never closed, so a
    table of keywords stacked a level per key. Any other token charges the
    structure where its keyword stood: that keyword moved no depth."""
    state.next_rule = None
    if token != ":":
        _charge_structure(state, state.prev, False)


def _charge_structure(state: _FnState, keyword: str, is_python: bool) -> None:
    state.total += 1 + _nesting(state, is_python)
    _push_structure(state, is_python, keyword)


def _push_structure(state: _FnState, is_python: bool, token: str = "") -> None:
    """Open the structure's block, or wait for its `{`.

    A body without braces (`if (a) return 0;`) never pushes, so `_open_body`
    tracks it and reaches its level here. Where every body has braces the `{`
    reaches the level.
    """
    if is_python:
        _push(state, state.line_indent)
    elif state.dialect.braceless:
        _open_body(state, token)
    else:
        state.pending.append(state.bracket_depth)


# --- bodies without braces ------------------------------------------------------
#
# In C, C++, Objective-C, Java, JavaScript, TypeScript and Zig a structure's
# body can go without braces, `if (a) return b ? 1 : 2;`, and it is a level
# like a block: Sonar v1.7 (App. B3) nests what an if or a loop holds, braces or
# not. The pass pushed a level only at a `{`, so a structure in such a body paid
# no nesting, and the structure went on waiting past its `;` and took the next
# block of any kind, a bare `{`, a lambda's body or an object literal, for its
# own. Each structure now keeps a body, whose phase says where it stands.
#
# A body ends with its statement: at a `;` or a `,` at its own bracket depth, at
# the close of a bracket around it, at the `}` of a braced statement that was
# the body (`for (...) if (x) { ... }`), and in JavaScript at a line break that
# neither the line before nor the next one continues. A body in the function's
# declaration, a Zig return type's `if (A) u8 else u16`, ends at the function's
# `{`, and one in a Zig field's or variable's type at the `=` after the type. The token after that end
# decides: an `else` ends the body of the if it belongs to and keeps the bodies
# around that one (`for (...) if (a) b(); else c();`), a `catch` or `finally`
# keeps them all, and anything else ends them all. A Zig loop's own `else`
# inside another body ends that body too: the pass does not tell a loop's else
# from an if's.

def _open_body(state: _FnState, token: str) -> None:
    """A structure's body, waiting for its header, or for a `do`, for its
    first token. Its level is reached here, one below the levels around it,
    whichever way the body turns out."""
    phase = _AWAITING if token in state.dialect.do_loops else _FRESH
    _reach(state, _nesting(state, False) + 1)
    _note_annotation(state, token)
    state.bodies.append([state.bracket_depth, phase, token])


def _note_annotation(state: _FnState, token: str) -> None:
    """An `if` right after a `:` in Zig is a type, a field's or a variable's
    (`called: if (safety) bool else void = ...`), and the `=` after the type
    ends its arms; see _initializer. The `:` is two tokens back, since the
    `if` is charged at the token after it."""
    if token == "if" and state.prev2 == ":" and state.dialect.if_types:
        state.annotation = state.bracket_depth


def _body_token(state: _FnState, token: str) -> None:
    """One token of a language whose bodies can go without braces, read for
    where those bodies start and end."""
    if state.at_line_start:
        _line_break(state, token)
    if state.ended is not None:
        _end_statement(state, token)
    _advance_body(state, token)
    step = _BODY_STEPS.get(token)
    if step is not None:
        step(state, token)


def _line_break(state: _FnState, token: str) -> None:
    """A JavaScript line break ends the statement of a body that has begun,
    unless one of the two lines says the statement goes on."""
    if state.dialect.line_statements and _begun(state) and not _continues_line(state.prev, token):
        state.ended = state.bracket_depth


def _innermost_at(state: _FnState, depth: int):
    """The innermost body, when it stands at `depth`; None otherwise."""
    body = state.bodies[-1] if state.bodies else None
    return body if body is not None and body[0] == depth else None


def _begun(state: _FnState) -> bool:
    """The innermost body has begun, and no bracket opened inside it is open."""
    body = _innermost_at(state, state.bracket_depth)
    return body is not None and body[1] == _BEGUN


def _continues_line(prev: str, token: str) -> bool:
    """The line before ends in an operator, or the next line starts with one
    that cannot start a statement: `.then(f)`, `? a : b`, `&& c`."""
    return _operator(prev) or (_operator(token) and token not in _PREFIX_ONLY)


def _operator(token: str) -> bool:
    return token[:1] in _OPERATOR_START and token not in ("++", "--")


def _end_statement(state: _FnState, token: str) -> None:
    """The token after a statement's end closes the bodies at its depth,
    unless it goes on with the statement (see _STATEMENT_GOES_ON). A `while`
    after the statement a `do` holds goes on with the do-while, which ends at
    the `;` after its condition: it closes the do's body and those inside it,
    and keeps the bodies around the do-while."""
    depth, state.ended = state.ended, None
    if token == "while" and _ends_do(state, depth):
        return
    if token not in _STATEMENT_GOES_ON:
        _close_bodies(state, depth)


def _ends_do(state: _FnState, depth: int) -> bool:
    """Close the bodies through the innermost `do`'s at `depth` or deeper, if
    one is open, and say so; see _loop_tail."""
    held = [body[2] for body in state.bodies if body[0] >= depth]
    if not set(held) & state.dialect.do_loops:
        return False
    while state.bodies.pop()[2] not in state.dialect.do_loops:
        pass
    state.do_tail = True
    return True


def _advance_body(state: _FnState, token: str) -> None:
    """Only a token at the innermost body's own depth moves its phase; the
    tokens inside its header's brackets do not."""
    body = _innermost_at(state, state.bracket_depth)
    step = _PHASE_STEPS.get(body[1]) if body is not None else None
    if step is not None:
        body[1] = step(token)


def _after_fresh(token: str) -> int:
    if token == "(":
        return _HEADER
    return _FRESH if token in _HEADER_WORDS else _after_awaiting(token)


def _after_awaiting(token: str) -> int:
    """A Zig payload opens at `|`, and a Zig loop's continue expression, `while
    (c) : (i += 1)`, is a second header; `{` is the body's block."""
    if token == "|":
        return _PAYLOAD
    if token == ":":
        return _FRESH
    return _AWAITING if token == "{" else _BEGUN


def _after_payload(token: str) -> int:
    return _AWAITING if token == "|" else _PAYLOAD


_PHASE_STEPS = {_FRESH: _after_fresh, _AWAITING: _after_awaiting, _PAYLOAD: _after_payload}


def _brace_body(state: _FnState) -> None:
    """A `{` is the block of the body that waits for one at the `{`'s own
    depth, and of no other: a `{` in a header's brackets (`for (int x : {1,
    2})`, a lambda in a condition) or in a body that has begun (`return {k:
    1}`) is a literal's or a lambda's. `_body_token` has already counted this
    `{`, so the depth outside it is one less. The `{` that starts the
    function's body ends the bodies in its declaration; see _function_body."""
    body = _innermost_at(state, state.bracket_depth - 1)
    if _function_body(state, body):
        _close_bodies(state, 0)
    elif body is not None and body[1] == _AWAITING:
        body[1] = _BRACED
        _push(state, state.brace_depth)


def _function_body(state: _FnState, body) -> bool:
    """Whether a `{` starts the function's body, where lizard starts it, rather
    than a structure's block: it stands outside every bracket and block, after
    a body with no braces has begun. That body sits in the function's
    declaration, as the arms of an `if` in a Zig return type do (`fn f(x:
    anytype) if (A) u8 else u16 {`), and ends there. Held open, the else's body
    put every structure in the function one level down."""
    return state.brace_depth == 0 and body is not None and body[0] == 0 and body[1] == _BEGUN


def _open_bracket(state: _FnState, _token: str) -> None:
    state.bracket_depth += 1


def _close_bracket(state: _FnState, token: str) -> None:
    """A closing bracket ends the bodies inside it. A `}` back at a body's
    depth ends the statement that body holds, and the next token says whether
    it goes on; a `)` back at a body's header ends the header, and the run of
    logical operators the header held: `if (a && b) return c && d;` holds two
    sequences, as its braced form does."""
    state.bracket_depth -= 1
    depth = state.bracket_depth
    _close_bodies(state, depth + 1)
    body = _innermost_at(state, depth)
    if body is None:
        return
    if token == "}":
        state.ended = depth
    elif body[1] == _HEADER:
        body[1] = _AWAITING
        state.bool_op = None


def _semicolon(state: _FnState, _token: str) -> None:
    if _innermost_at(state, state.bracket_depth) is not None:
        state.ended = state.bracket_depth


def _comma(state: _FnState, _token: str) -> None:
    """A comma at the depth of a body that has begun ends it: the next Zig
    prong, `0 => if (a) 1 else 2, 1 => ...`, or C's comma operator. A comma in
    a Zig payload, `for (a, b) |x, y|`, ends nothing."""
    if _begun(state):
        _close_bodies(state, state.bracket_depth)


def _else_ends_body(state: _FnState, _token: str) -> None:
    """An else ends the body of the if it belongs to, the innermost if open at
    its depth, and every body inside that one: `if (a) 1 else 2` in Zig, or
    `if (a) x(); else y();` once the `;` has ended the statement."""
    depth = state.bracket_depth
    while state.bodies and state.bodies[-1][0] >= depth:
        if state.bodies.pop()[2] == "if":
            return


def _initializer(state: _FnState, _token: str) -> None:
    """The `=` after a type ends the arms of an `if` in that type: in
    `called: if (safety) bool else void = if (safety) false else {},` the
    second `if` is the field's value, beside the first, not in its else. In a
    statement, `if (a) x = 1 else y = 2;`, the `=` is in the arm and ends
    nothing."""
    if state.annotation == state.bracket_depth:
        _close_bodies(state, state.bracket_depth)


def _close_bodies(state: _FnState, depth: int) -> None:
    """Close the bodies at `depth` or deeper, whose statement has ended, and
    the type they sat in."""
    while state.bodies and state.bodies[-1][0] >= depth:
        state.bodies.pop()
    if state.annotation is not None and state.annotation >= depth:
        state.annotation = None


# The tokens the body rules read, each to the rule that reads it.
_BODY_STEPS = {";": _semicolon, ",": _comma, "else": _else_ends_body, "=": _initializer,
               **dict.fromkeys(("(", "[", "{"), _open_bracket),
               **dict.fromkeys((")", "]", "}"), _close_bracket)}
