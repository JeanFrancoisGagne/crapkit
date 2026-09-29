"""A Swift reader that reads a name as a name and a decision as a decision.
Upstream: lizard 1.24.0's SwiftReader.

Names
-----
lizard's SwiftReader finds functions with a state machine borrowed from its Go
reader, and it reads several Swift words as declarations wherever they appear.
Each misreading opens a function, or skips a brace, where the code has neither,
so the function holding the word runs on to a later `}` and the functions after
it lose their rows:

  * `init` after a `.` is a call (`super.init(...)`, `self.init(...)`,
    `.init(...)`). Read as a declaration, it took the next brace block as its
    body.
  * `get`, `set`, `willSet` and `didSet` open an accessor only before its block.
    `r.get()`, `case .get`, `[.get, .head]`, `private(set)` and a call to a
    function named `set` opened one anyway.
  * `protocol` starts a declaration only before a name. As an argument label or
    a member (`Socket(protocol: p)`, `self.protocol`) it started a protocol body
    whose brace count never came back to zero, so no later function in the file
    had a row.
  * `type` declares nothing in Swift. Go's rule skipped the two tokens after
    it, and `return type` before a `}` lost that brace.
  * `init?` and `init!` declare failable initializers. The tokenizer reads each
    as one word, not `init`, so they had no row. They are listed as `init`.
  * `#` starts a preprocessor line in the C reader lizard's tokenizer shares.
    In Swift only a compiler directive (`#if`, `#elseif`, `#else`, `#endif`,
    `#sourceLocation`, `#warning`, `#error`) runs to the end of its line.
    `#fileID`, `#available(...)`, `#selector(...)` and every other `#` word are
    one token, and a raw string (`#"..."#`) is one string, so the `)`, `{` or
    decision after one on its line is read.
  * a `{` right after a `,` is a closure argument (`run(1, { x in ... })`).
    lizard read the token after every `,` as a declared name and dropped the
    brace, so the closure's `}` ended the function.
  * a raw identifier (SE-0451) is any text between backticks, spaces included,
    as Swift Testing names a test: ``func `keeps onboarding if offline`() {``.
    lizard read a backtick name only when it is one word (`default`), so the
    name split into a lone backtick and its words. The function had no row, and
    an `if`, `for` or `while` among the words counted as a decision wherever the
    name was called. The whole name, backticks included, is one token now.

Decisions
---------
The McCabe count is 1 + the binary decisions (NIST SP 500-235 sec. 4.1).
lizard counted three Swift spellings that decide nothing and missed two that
do:

  * the `case` of `if case`, `guard case`, `while case` and `for case` belongs
    to that statement's one condition. lizard counted it as a switch case too,
    +1 ccn_std and a nesting level.
  * a keyword spelled as an argument label and followed by the parameter's name
    (`func value(for name: String)`), or on a line after the `(` or `,` it
    follows, is a label. lizard renames a label only when it is written
    `(for:` on one line, so the rest counted as a loop, an if or a catch.
  * a `?` glued to what it follows is an optional mark (`(any Error)?`,
    `[Int]?`, `Set<Int>?`, and `Int?.self`, `Empty?.none` or `Int?.some(1)`,
    which name the optional type or one of its own members) or the start of an
    optional chain.
    Swift writes the conditional operator with whitespace on both sides of its
    `?`, so a glued one is never that operator; lizard read it as one after
    `)`, `]` or `>`, +1 ccn and a nesting level. A mark decides nothing.
  * an optional chain (`a?.b`, `f()?.g`, `c?()`, `d?[0]`) reads the rest of
    the chain only when the value before its `?` is not nil: one short-circuit
    decision per `?`, as `a?.b` is in TypeScript. It adds 1 to ccn and nothing
    to cognitive or nesting. lizard counted none after a name.
  * `a ?? b` picks one of two values, one decision, and lizard counted none.

A comma inside one parameter's type or default, as in `pair: (Int, Int)`,
`(A, B) -> Void` or `[1, 2]`, no longer counts as another parameter, and `try`
opens no nesting level: it marks an expression that can throw, not a block. A
`<` opens a generic clause (`Set<Int>`, `Result<Int, Error>`) only when its own
`>` comes first, before a `:`, `=`, `;` or brace beside it and before the
bracket around it closes, since a clause is a list of types and holds none of
these outside its own brackets. Every other `<` compares, spaced or not:
`1 < 2`, `x<0, b: Int`, `1<<2`, `0..<n`, `{ $0<$1 }`. A `>` closes a clause
only while a `<` is open, so `x > 0` and `x>0` close nothing.

Strings
-------
`\\( )` in a string literal holds an expression, and that expression can hold a
string of its own: `"\\(d["key"] ?? "none")"`. lizard's string rule ends a string
at its first unescaped quote, so it ended this one at the quote before `key`,
read `key` and `none` as code, and a `{` or `}` inside the inner string moved
the brace count: the function holding it lost its row. A string with no inner
quote came out as one token, so the `&&` or `??` in its `\\( )` counted nothing.

The tokenizer now takes a string whole with each interpolation inside it,
including a multi-line string between triple quotes, which lizard read as an
empty string and then a string ending at the first quote of its text. It then reads each
`\\( )`, or `\\#( )` in a raw string, again as code, at any depth, and yields
the text around it as a string token that keeps every newline it held.

KNOWN LIMIT: an interpolation holding parentheses more than four deep, or a
multi-line string inside an interpolation, does not match; that string reads
as lizard read it.

What it keeps
-------------
Every long name, and with it every ratchet key a function lizard read whole
already has. A word read as a name is a `_Name`: a `str` with the same value,
so every count and every name reads it as before, and only
`CorrectedSwiftStates` opens nothing for it. A token that decides nothing is a
`_Plain`: its value carries a leading `_`, the way lizard renames a label
written `(for:`, so no count reads it, and the parameter list names it by its
source spelling. A `<` that compares is an `_Operator`, the same value again,
which only the parameter count reads differently. An optional chain's decision
is a `_Chain`, a token added after its `?` with the value `?.`, which ccn counts
and the parameter list skips, so a long name reads as before.

Registration
------------
The same mechanism as lizardrust: `register()` rebinds `SwiftReader` in the
`lizard_languages` namespace, which `languages()` reads on every call, and
verifies it through `lizard.get_reader_for`. analyze.py calls it at module
scope. The reader still inherits lizard's `SwiftReplaceLabel.preprocess`, which
drains the token stream, so `.swift` keeps analyze.py's second extension chain.
"""
from __future__ import annotations

import functools
import re

from ._pygdefer import deferred_pygments

with deferred_pygments():  # lizard's Erlang reader would load pygments here
    import lizard
    import lizard_languages
    from lizard_languages.code_reader import CodeStateMachine
    from lizard_languages.swift import SwiftReader as _StockSwiftReader
    from lizard_languages.swift import SwiftStates as _StockSwiftStates

# Any filename picks the reader; the file is never opened.
_PROBE = "crapkit_registration_probe.swift"

# Token patterns tried before lizard's `#`, which would take the rest of the line.
# A raw string first, one line or three quotes over several, closed by as many
# hashes as opened it, however many; then any `#` word except a compiler
# directive, which stays one token to the end of its line so the `&&` in
# `#if DEBUG && TRACE` still decides nothing. The group is named so it cannot
# clash with a group number in lizard's own pattern.
_HASH_TOKENS = (r'|(?P<swift_raw>\#+)".*?"(?P=swift_raw)'
                r"|\#(?!(?:if|elseif|else|endif|sourceLocation|warning|error)\b)\w+")

# A raw identifier (SE-0451): any text between backticks on one line, spaces included,
# as Swift Testing names a test. lizard's own pattern takes one word (`default`). A tab
# is left out, since a name has to fit one field of a tab-separated marks file.
_RAW_IDENTIFIER = r"`[^`\t\r\n]+`"


def _nested(depth: int) -> str:
    """What an interpolation holds, parentheses up to DEPTH deep: code, an escape such
    as the `\\` of a key path, a one-line string, and a parenthesized group of the same.
    Each alternative starts with a different character, so a failed match costs no
    backtracking."""
    body = r'[^()"\\]|\\.|"(?:\\.|[^"\\\n])*"'
    for _ in range(depth):
        body = r'[^()"\\]|\\.|"(?:\\.|[^"\\\n])*"|\((?:' + body + r')*\)'
    return body


_INTERPOLATED = _nested(4)


def _interpolation(hashes: str) -> str:
    """`\\( )`, or `\\#( )` in a string opened with that many hashes."""
    return r"\\" + hashes + r"\((?:" + _INTERPOLATED + r")*\)"


# String patterns tried before lizard's, which ends a string at its first unescaped
# quote. A string here takes each interpolation whole, so a quote inside one pairs
# inside it; a string these cannot match falls through to lizard's rule.
_STRING_TOKENS = (r'|"""(?:' + _interpolation("") + r'|\\[^(]|[^\\])*?"""'
                  r'|"(?:' + _interpolation("") + r'|\\[^(]|[^"\\])*"')
_STRING_START = re.compile(r'#*"')


@functools.cache
def _pieces(hashes: int) -> re.Pattern:
    """An interpolation (group 1), or any other escape, in a string opened with HASHES hashes."""
    return re.compile("(" + _interpolation("#" * hashes) + r")|\\" + "#" * hashes + ".", re.S)


def _holes(token: str) -> list:
    """TOKEN's interpolations, when it is a string literal. Every interpolation starts
    with a backslash, and most tokens hold none, which is the cheapest test to fail."""
    if "\\" not in token or not (start := _STRING_START.match(token)):
        return []
    return [piece for piece in _pieces(len(start.group()) - 1).finditer(token) if piece.group(1)]


def _open_interpolations(tokens, addition: str, token_class):
    for token in tokens:
        holes = _holes(token)
        if holes:
            yield from _interpolated(token, holes, addition, token_class)
        else:
            yield token


def _interpolated(token: str, holes: list, addition: str, token_class):
    """TOKEN's interpolations as code, and the text around each as a string token. The
    text is quoted again so none of it reads as code, and it keeps every newline it held,
    so lizard's line count sees each one."""
    start = 0
    for hole in holes:
        yield '"' + token[start:hole.start()].strip('#"') + '"'
        code = hole.group(1)
        yield from CorrectedSwiftReader.generate_tokens(code[code.index("(") + 1:-1], addition, token_class)
        start = hole.end()
    yield '"' + token[start:].strip('#"') + '"'


_FAILABLE_INITS = frozenset({"init?", "init!"})
_IDENTIFIER = re.compile(r"\w+|" + _RAW_IDENTIFIER)

# The keywords lizard counts that Swift also accepts as an argument label, and what
# comes right before the `case` of a case pattern in a condition.
_LABEL_WORDS = frozenset({"if", "for", "while", "catch", "guard", "case"})
_CASE_CONDITIONS = frozenset({"if", "guard", "while", "for", ","})

# Each bracket a parameter list opens, and the token that closes it. A comma with more
# than the list's own `(` open is inside one parameter.
_CLOSERS = {"(": ")", "[": "]", "{": "}", "<": ">"}
_BRACKET_CLOSERS = frozenset({")", "]", "}"})
# The tokens a generic argument clause, a comma-separated list of types, never holds
# outside its own ( ) or [ ]: read beside an open `<`, one shows that `<` compared.
_CLAUSE_ENDS = frozenset({":", "=", ";", "{"}) | _BRACKET_CLOSERS


class _Name(str):
    """A declaration word the source uses as a name. Same value, so it counts and
    names as before; CorrectedSwiftStates opens nothing for it."""


class _Operator(str):
    """A `<` the source uses as an operator (`a < b`, `x<0`, `1 << 2`, `0..<n`), not to
    open a generic clause. Same value, so it counts and names as before; the parameter
    list opens nothing for it."""


class _Plain(str):
    """A keyword or `?` the source uses as no decision. Its value is the source
    spelling behind a `_`, so no count reads it; `spelling` gives the source back."""

    @property
    def spelling(self) -> str:
        return self[1:]


class _Chain(str):
    """The one decision an optional chain makes (`a?.b`, `f()?.g`, `c?()`, `d?[0]`): the
    rest of the chain runs only when the value before its `?` is not nil. It follows
    that `?` in the token stream with the value `?.`, which ccn counts and cognitive and
    nesting do not. It has no source spelling, so the parameter list skips it."""


_CHAIN = _Chain("?.")
# A word the tokenizer glued to its `?` (`a?`), the marks that are keywords and chain
# nothing (`try?`, `as?`, a failable `init?`), and the members that name the optional
# type itself rather than a value's member: its metatype (`Int?.self`, `T?.Type`) and
# Optional's own case and initializers (`Empty?.none`, `Int?.some(1)`, `Int?.init(1)`).
_GLUED_MARK = re.compile(r"\w+\?")
_KEYWORD_MARKS = frozenset({"try?", "as?", "init?"})
_TYPE_MEMBERS = (["self"], ["Type"], ["Protocol"], ["none"], ["some"], ["init"])


def _spelled(token: str) -> str:
    return token.spelling if isinstance(token, _Plain) else token


def _is_code(token: str) -> bool:
    return token != "\n" and not token.startswith(("//", "/*"))


def _names_its_value(after: list[str]) -> bool:
    """`(newValue) {`: a setter or an observer naming the value it receives."""
    return len(after) == 4 and after[0] == "(" and after[2:] == [")", "{"] and bool(
        _IDENTIFIER.fullmatch(after[1]))


def _opens_accessor(word: str, after: list[str]) -> bool:
    """An accessor's word is followed by its block, by an effect (`get async throws {`),
    or, for a setter or an observer, by the name it gives the new or old value."""
    return after[:1] in (["{"], ["async"], ["throws"]) or (word != "get" and _names_its_value(after))


def _member_name(word: str, before: str, after: list[str]) -> bool:
    """`super.init(...)`, `.init(...)`: an initializer or subscript after a `.` is used."""
    return before == "."


def _accessor_name(word: str, before: str, after: list[str]) -> bool:
    return before == "." or not _opens_accessor(word, after)


def _protocol_name(word: str, before: str, after: list[str]) -> bool:
    """A protocol declaration names the protocol next; `protocol:` and `.protocol` do not."""
    return before == "." or not (after and _IDENTIFIER.fullmatch(after[0]))


# The declaration words the reader reads as names where the code around them says so.
_NAME_RULES = {"init": _member_name, "subscript": _member_name, "get": _accessor_name,
               "set": _accessor_name, "willSet": _accessor_name, "didSet": _accessor_name,
               "protocol": _protocol_name}


def _is_name(word: str, before: str, after: list[str]) -> bool:
    rule = _NAME_RULES.get(word)
    return rule is not None and rule(word, before, after)


def _is_label(before: str, after: list[str]) -> bool:
    """`(for name:` or `, for:`: a label, followed by its colon or by a name and a colon."""
    named = after[1:2] == [":"] and bool(_IDENTIFIER.fullmatch(after[0]))
    return before in ("(", ",") and (after[:1] == [":"] or named)


def _decides_nothing(word: str, before: str, after: list[str]) -> bool:
    """A keyword label, or the `case` of a case pattern in a condition."""
    if word in _LABEL_WORDS and _is_label(before, after):
        return True
    return word == "case" and before in _CASE_CONDITIONS


def _read(token: str, before: str, after: list[str]) -> str:
    if _is_name(token, before, after):
        return _Name(token)
    if _decides_nothing(token, before, after):
        return _Plain("_" + token)
    return token


def _neighbors(tokens: list[str], code: list[int]):
    """(index, code token before, up to four code tokens after) for each code token."""
    before = [""] + [tokens[i] for i in code[:-1]]
    for n, i in enumerate(code):
        yield i, before[n], [tokens[j] for j in code[n + 1:n + 5]]


def _read_all(tokens: list[str]) -> list[str]:
    """tokens with each name a `_Name` and each non-decision a `_Plain`. Comments and
    newlines are skipped when looking at a token's neighbors."""
    code = [i for i, token in enumerate(tokens) if _is_code(token)]
    read = list(tokens)
    for i, before, after in _neighbors(tokens, code):
        read[i] = _read(tokens[i], before, after)
    return read


def _is_mark(tokens: list[str], index: int) -> bool:
    """A `?` glued to what it follows: `)?`, `]?`, `>?`, or the tokenizer's `a?`."""
    token = tokens[index]
    if token == "?":
        return index > 0 and not tokens[index - 1].isspace()
    return token not in _KEYWORD_MARKS and bool(_GLUED_MARK.fullmatch(token))


def _next_code(tokens: list[str], index: int) -> int:
    """The index of the first token after `index` that is not whitespace."""
    index += 1
    while index < len(tokens) and tokens[index].isspace():
        index += 1
    return index


def _chains(tokens: list[str], index: int) -> bool:
    """The mark at `index` starts an optional chain: a `(` or `[` right after it, or a
    `.` after it, on its line or the next, that names no member of the optional type
    itself (`_TYPE_MEMBERS`)."""
    if tokens[index + 1:index + 2] in (["("], ["["]):
        return True
    dot = _next_code(tokens, index)
    member = _next_code(tokens, dot)
    return tokens[dot:dot + 1] == ["."] and tokens[member:member + 1] not in _TYPE_MEMBERS


def _optional_marks(tokens):
    """The raw tokens, each `?` glued to the token before it made a `_Plain`, and a
    `_Chain` after each mark that starts an optional chain. The tokenizer already glues
    one after a word (`String?`); this takes the rest."""
    tokens = list(tokens)
    for index in range(len(tokens)):
        yield from _marked(tokens, index)


def _marked(tokens: list[str], index: int) -> list[str]:
    """The token at `index` as the reader keeps it, then a `_Chain` if it starts one."""
    token = tokens[index]
    if not _is_mark(tokens, index):
        return [token]
    kept = _Plain("_?") if token == "?" else token
    return [kept, _CHAIN] if _chains(tokens, index) else [kept]


class _Clauses:
    """One pass over the tokens that finds each `<` opening a generic clause. A `<` waits
    on a stack with the brackets around it. Its own `>`, read while it is the innermost
    open bracket, makes it a clause. A token in `_CLAUSE_ENDS` read while it is
    innermost makes it a comparison, and so does the closer of a bracket around it
    (`{ $0<$1 }`)."""

    def __init__(self):
        self.opened: list[tuple[str, int]] = []  # (the closer awaited, the opener's index)
        self.found: set[int] = set()

    def read(self, index: int, token: str) -> None:
        if token in _CLAUSE_ENDS:
            self._compare()
        if token in _CLOSERS:
            self.opened.append((_CLOSERS[token], index))
        elif token == ">":
            self._close_clause()
        elif token in _BRACKET_CLOSERS:
            self._close_to(token)

    def _innermost_is_less_than(self) -> bool:
        return bool(self.opened) and self.opened[-1][0] == ">"

    def _compare(self) -> None:
        while self._innermost_is_less_than():
            self.opened.pop()

    def _close_clause(self) -> None:
        if self._innermost_is_less_than():
            self.found.add(self.opened.pop()[1])

    def _close_to(self, token: str) -> None:
        awaited = [closer for closer, _ in self.opened]
        if token in awaited:
            del self.opened[len(awaited) - 1 - awaited[::-1].index(token):]


def _comparing_less_thans(tokens) -> list[str]:
    """The raw tokens, each `<` that opens no generic clause an `_Operator`: `x<0`,
    `1 << 2`, `0..<n`. Swift reads a `<` as a clause only when a list of types parses
    up to its `>`, whatever the spacing around it."""
    tokens = list(tokens)
    clauses = _Clauses()
    for index, token in enumerate(tokens):
        clauses.read(index, token)
    return [_Operator(token) if token == "<" and index not in clauses.found else token
            for index, token in enumerate(tokens)]


def _nest(opened: list[str], token: str) -> None:
    """Track the brackets open in a parameter list, as the closers they wait for. A
    closer closes back to its opener, and with it any `<` left open inside, which
    compared rather than opened; a `>` with no `<` open closes nothing."""
    if isinstance(token, _Operator):
        return
    if token in _CLOSERS:
        opened.append(_CLOSERS[token])
    elif token in opened:
        del opened[len(opened) - 1 - opened[::-1].index(token):]


def _add_parameter(fn, token: str, inside: bool) -> None:
    """lizard's FunctionInfo.add_parameter, except that a comma inside one parameter
    continues it. The long name is spelled the same either way."""
    if token == "," and inside:
        fn.add_to_long_name(" " + token)
        fn.full_parameters[-1] += " " + token
    else:
        fn.add_parameter(token)


class CorrectedSwiftStates(_StockSwiftStates):
    """lizard's SwiftStates, opening nothing for a name."""

    def __init__(self, context):
        super().__init__(context)
        self.parameter_brackets: list[str] = []

    def _state_global(self, token):
        if isinstance(token, _Name) or token == "type":
            return
        super()._state_global("init" if token in _FAILABLE_INITS else token)

    def _expect_declaration_name(self, token):
        """The token after `let`, `var`, `case` or `,` is a name, unless it is a
        brace: a closure passed after a comma opens a block like any other."""
        self._state = self._state_global
        if token in ("{", "}"):
            self._state_global(token)

    @CodeStateMachine.read_inside_brackets_then("()", "_expect_function_impl")
    def _function_dec(self, token):
        if token == "(" and self.br_count == 1:
            self.parameter_brackets = []
        _nest(self.parameter_brackets, token)
        if token not in "()" and not isinstance(token, _Chain):
            inside = len(self.parameter_brackets) > 1
            _add_parameter(self.context.current_function, _spelled(token), inside)


class CorrectedSwiftReader(_StockSwiftReader):
    """lizard's SwiftReader, with its tokenizer, preprocess and states corrected."""

    # pylint: disable=too-few-public-methods

    # `a ?? b` and an optional chain's `?.` each decide once, the way `&&` does.
    _logical_operators = _StockSwiftReader._logical_operators | {"??", _CHAIN}

    # lizard's ND extension reads a reader's `loops` in place of its own set. This is
    # that set without `try`, which in Swift marks an expression and opens no block.
    loops = frozenset({"if", "foreach", "for", "while", "&&", "||", "?", "catch", "case", "def"})

    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [CorrectedSwiftStates(context)]

    @staticmethod
    def generate_tokens(source_code, addition="", token_class=None):
        """lizard's tokenizer with Swift's strings, `#` tokens and raw identifiers, every
        interpolation read as code. A generator over lizard's, so the token stage still
        yields as it reads."""
        additions = _HASH_TOKENS + _STRING_TOKENS + "|" + _RAW_IDENTIFIER + addition
        return _open_interpolations(
            _StockSwiftReader.generate_tokens(source_code, additions, token_class), addition, token_class)

    def preprocess(self, tokens):
        return _read_all(super().preprocess(_comparing_less_thans(_optional_marks(tokens))))


def register() -> None:
    """Make lizard resolve `.swift` to CorrectedSwiftReader. Idempotent.

    Raises RuntimeError when the rebind does not reach lizard's own resolution. A
    Swift file measured with the defective reader is worse than a crash: the
    functions it hides pass every gate unseen.
    """
    lizard_languages.SwiftReader = CorrectedSwiftReader
    resolved = lizard.get_reader_for(_PROBE)
    if resolved is not CorrectedSwiftReader:
        raise RuntimeError(_unregistered(resolved))


def _unregistered(resolved: type | None) -> str:
    name = getattr(resolved, "__name__", "no reader")
    return (f"crapkit.lizardswift.register() did not take: lizard resolves '.swift' to "
            f"{name}, not CorrectedSwiftReader. lizard {lizard.version} picks readers "
            f"some other way than lizard_languages.languages(); rewrite register() "
            f"against the new mechanism.")
