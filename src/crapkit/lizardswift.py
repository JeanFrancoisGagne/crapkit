"""A Swift reader that reads a name as a name. Upstream: lizard 1.24.0's SwiftReader.

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

What it keeps
-------------
A word read as a name keeps its spelling. `_Name` is a `str` with the same
value, so every count and every long name reads it exactly as before, and only
`CorrectedSwiftStates` treats it differently: it opens nothing. A function
lizard already read whole keeps its long name, and with it its ratchet key.

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
    from lizard_languages.swift import SwiftReader as _StockSwiftReader
    from lizard_languages.swift import SwiftStates as _StockSwiftStates

# Any filename picks the reader; the file is never opened.
_PROBE = "crapkit_registration_probe.swift"

# Token patterns tried before lizard's `#`, which would take the rest of the line.
# A raw string first, three-quote form before one-quote form; then any `#` word
# except a compiler directive, which stays one token to the end of its line so the
# `&&` in `#if DEBUG && TRACE` still decides nothing.
_HASH_TOKENS = (r'|\#+""".*?"""\#+'
                r'|\#+"[^\n]*?"\#+'
                r"|\#(?!(?:if|elseif|else|endif|sourceLocation|warning|error)\b)\w+")

_FAILABLE_INITS = frozenset({"init?", "init!"})
_WORD = re.compile(r"\w+")


class _Name(str):
    """A declaration word the source uses as a name. Same value, so it counts and
    names as before; CorrectedSwiftStates opens nothing for it."""


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


def _neighbors(tokens: list[str], code: list[int]):
    """(index, code token before, up to four code tokens after) for each code token."""
    before = [""] + [tokens[i] for i in code[:-1]]
    for n, i in enumerate(code):
        yield i, before[n], [tokens[j] for j in code[n + 1:n + 5]]


def _names(tokens: list[str]) -> list[str]:
    """tokens with each declaration word that reads as a name wrapped in `_Name`.
    Comments and newlines are skipped when looking at a word's neighbors."""
    code = [i for i, token in enumerate(tokens) if _is_code(token)]
    named = list(tokens)
    for i, before, after in _neighbors(tokens, code):
        if _is_name(tokens[i], before, after):
            named[i] = _Name(tokens[i])
    return named


class CorrectedSwiftStates(_StockSwiftStates):
    """lizard's SwiftStates, opening nothing for a name."""

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


class CorrectedSwiftReader(_StockSwiftReader):
    """lizard's SwiftReader, with its tokenizer, preprocess and states corrected."""

    # pylint: disable=too-few-public-methods

    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [CorrectedSwiftStates(context)]

    @staticmethod
    def generate_tokens(source_code, addition="", token_class=None):
        return _StockSwiftReader.generate_tokens(source_code, _HASH_TOKENS + addition, token_class)

    def preprocess(self, tokens):
        return _names(super().preprocess(tokens))


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
