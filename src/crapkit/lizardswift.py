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

Decisions
---------
The McCabe count is 1 + the binary decisions (NIST SP 500-235 sec. 4.1).
lizard counted three Swift spellings that decide nothing and missed one that
does:

  * the `case` of `if case`, `guard case`, `while case` and `for case` belongs
    to that statement's one condition. lizard counted it as a switch case too,
    +1 ccn_std and a nesting level.
  * a keyword spelled as an argument label and followed by the parameter's name
    (`func value(for name: String)`), or on a line after the `(` or `,` it
    follows, is a label. lizard renames a label only when it is written
    `(for:` on one line, so the rest counted as a loop, an if or a catch.
  * a `?` glued to what it follows is an optional mark (`(any Error)?`,
    `[Int]?`, `Set<Int>?`) or an optional chain (`f()?.g`). Swift writes the
    conditional operator with whitespace on both sides of its `?`, so only
    that `?` is a decision; lizard counted both, +1 ccn and a nesting level.
  * `a ?? b` picks one of two values, one decision, and lizard counted none.

A comma inside one parameter's type or default, as in `pair: (Int, Int)`,
`(A, B) -> Void` or `[1, 2]`, no longer counts as another parameter, and `try`
opens no nesting level: it marks an expression that can throw, not a block.

What it keeps
-------------
Every long name, and with it every ratchet key a function lizard read whole
already has. A word read as a name is a `_Name`: a `str` with the same value,
so every count and every name reads it as before, and only
`CorrectedSwiftStates` opens nothing for it. A token that decides nothing is a
`_Plain`: its value carries a leading `_`, the way lizard renames a label
written `(for:`, so no count reads it, and the parameter list names it by its
source spelling.

Registration
------------
The same mechanism as lizardrust: `register()` rebinds `SwiftReader` in the
`lizard_languages` namespace, which `languages()` reads on every call, and
verifies it through `lizard.get_reader_for`. analyze.py calls it at module
scope. The reader still inherits lizard's `SwiftReplaceLabel.preprocess`, which
drains the token stream, so `.swift` keeps analyze.py's second extension chain.
"""
from __future__ import annotations

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
# hashes as opened it; then any `#` word except a compiler directive, which stays
# one token to the end of its line so the `&&` in `#if DEBUG && TRACE` still
# decides nothing.
_HASH_TOKENS = ("".join(r'|\#{%d}".*?"\#{%d}' % (n, n) for n in (4, 3, 2, 1))
                + r"|\#(?!(?:if|elseif|else|endif|sourceLocation|warning|error)\b)\w+")

_FAILABLE_INITS = frozenset({"init?", "init!"})
_WORD = re.compile(r"\w+")

# The keywords lizard counts that Swift also accepts as an argument label, and what
# comes right before the `case` of a case pattern in a condition.
_LABEL_WORDS = frozenset({"if", "for", "while", "catch", "guard", "case"})
_CASE_CONDITIONS = frozenset({"if", "guard", "while", "for", ","})

# What a bracket does to the depth a parameter list is read at: the list's own
# parentheses make 1, and a comma deeper than that is inside one parameter.
_PARAMETER_DEPTH = {"(": 1, "[": 1, "{": 1, "<": 1, ")": -1, "]": -1, "}": -1, ">": -1}


class _Name(str):
    """A declaration word the source uses as a name. Same value, so it counts and
    names as before; CorrectedSwiftStates opens nothing for it."""


class _Plain(str):
    """A keyword or `?` the source uses as no decision. Its value is the source
    spelling behind a `_`, so no count reads it; `spelling` gives the source back."""

    @property
    def spelling(self) -> str:
        return self[1:]


def _spelled(token: str) -> str:
    return token.spelling if isinstance(token, _Plain) else token


def _is_code(token: str) -> bool:
    return token != "\n" and not token.startswith(("//", "/*"))


def _names_its_value(after: list[str]) -> bool:
    """`(newValue) {`: a setter or an observer naming the value it receives."""
    return len(after) == 4 and after[0] == "(" and after[2:] == [")", "{"] and bool(
        _WORD.fullmatch(after[1]))


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
    return before == "." or not (after and _WORD.fullmatch(after[0]))


# The declaration words the reader reads as names where the code around them says so.
_NAME_RULES = {"init": _member_name, "subscript": _member_name, "get": _accessor_name,
               "set": _accessor_name, "willSet": _accessor_name, "didSet": _accessor_name,
               "protocol": _protocol_name}


def _is_name(word: str, before: str, after: list[str]) -> bool:
    rule = _NAME_RULES.get(word)
    return rule is not None and rule(word, before, after)


def _is_label(before: str, after: list[str]) -> bool:
    """`(for name:` or `, for:`: a label, followed by its colon or by a name and a colon."""
    named = after[1:2] == [":"] and bool(_WORD.fullmatch(after[0]))
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


def _optional_marks(tokens):
    """The raw tokens, each `?` glued to the token before it made a `_Plain`. The
    tokenizer already glues one after a word (`String?`); this takes the rest."""
    previous = "\n"
    for token in tokens:
        yield _Plain("_?") if token == "?" and not previous.isspace() else token
        previous = token


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
        self.parameter_depth = 0

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
            self.parameter_depth = 0
        self.parameter_depth += _PARAMETER_DEPTH.get(token, 0)
        if token not in "()":
            _add_parameter(self.context.current_function, _spelled(token), self.parameter_depth > 1)


class CorrectedSwiftReader(_StockSwiftReader):
    """lizard's SwiftReader, with its tokenizer, preprocess and states corrected."""

    # pylint: disable=too-few-public-methods

    _logical_operators = _StockSwiftReader._logical_operators | {"??"}

    # lizard's ND extension reads a reader's `loops` in place of its own set. This is
    # that set without `try`, which in Swift marks an expression and opens no block.
    loops = frozenset({"if", "foreach", "for", "while", "&&", "||", "?", "catch", "case", "def"})

    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [CorrectedSwiftStates(context)]

    @staticmethod
    def generate_tokens(source_code, addition="", token_class=None):
        return _StockSwiftReader.generate_tokens(source_code, _HASH_TOKENS + addition, token_class)

    def preprocess(self, tokens):
        return _read_all(super().preprocess(_optional_marks(tokens)))


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
