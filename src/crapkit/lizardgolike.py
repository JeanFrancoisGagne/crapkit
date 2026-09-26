"""Go and Zig readers that end a function's signature where the language ends it.

lizard 1.24.0 reads both languages with one state machine, GoLikeStates, and it
misread a signature four ways:

  * after a parameter list it waited for the next `{` wherever that was. A
    function type has no body, so `var cb func(int) error` in a function, or
    Zig's `const cb: *const fn (u8) void = &f;`, opened a function that took the
    next `if` block as its own, and the enclosing function lost that `if`.
  * at package level it read every `func (` as a method receiver, so after
    `var hooks []func()` the next line's `func` became the method's name and
    the function declared there had no row.
  * a result type with braces (`struct{ a int }`, Zig's `struct { usize, usize
    }`, `error{Oops}!u8`, `union(enum) {...}`) handed its own `{` to that wait,
    and the function ended on its signature line.
  * each comma in the parameter list started a parameter, and lizard names a
    parameter by the word its text ends with. So `f func(int, string) error`
    counted 2, `lessThan: fn (T, T) bool` counted 2, and `v interface{}`
    counted 0.

The fix reads the signature the way the language does:

  * A parameter starts at a comma of the list itself, never at one nested in
    a type's brackets, and the words inside those brackets stay out of the
    count. Every token still goes into the long name, so the key a ratchet mark
    was recorded under does not move.
  * After the parameter list, the result type is read for its brackets, and a
    `{` right after `struct`, `interface` (Go) or `struct`, `enum`, `union`,
    `opaque`, `error` (Zig) opens the type's body. The function's body is the
    first `{` outside all of them.
  * A signature that meets `,` `;` `=` or a closing bracket of its own before
    its body was a type: the function it opened is dropped and the tokens go
    back to the function around it. So does a Go signature at a line break
    after a word or a closing bracket, where the Go spec inserts a semicolon,
    and a Zig `fn` followed by `(`, since a Zig function always has a name.
  * At Go package level, `func (...)` followed by a name and `(` or `[` is a
    method. Anything else after the group is a result type or a body, so the
    group was a literal's parameter list or a function type.

A dropped function has to be dropped before any extension counts the token that
ends it, or that token's condition, nesting and line go to a function that no
longer exists: lizard's extensions see each token before the reader does. So the
reader decides on RAW tokens, newlines included, through `peek`, which
analyze._ReaderLookahead calls ahead of every counter. The signature that is
open registers itself on the context (`crapkit_header`), so a token costs one
attribute read when none is.

Accepted, documented, not solved
--------------------------------
* A composite literal whose element type is a function, `[]func(){f, g}`, reads
  its `{` as a literal's body, as lizard did: one anonymous row at ccn 1.
* A package-level Go literal's parameters were read as a receiver and still
  are, so `var f = func(a int) {...}` reports params 0, as it did.

Registration
------------
The same mechanism as crapkit.lizardrust, whose docstring explains it: rebind the
reader's name in `lizard_languages`, verify through `lizard.get_reader_for`, and
raise when lizard resolves something else. analyze.py calls `register()` at
module scope.
"""
from __future__ import annotations

from ._pygdefer import deferred_pygments

with deferred_pygments():  # lizard's Erlang reader would load pygments here
    import lizard
    import lizard_languages
    from lizard_languages.code_reader import CodeStateMachine
    from lizard_languages.go import GoReader as _StockGoReader
    from lizard_languages.golike import GoLikeStates
    from lizard_languages.zig import ZigReader as _StockZigReader

# What a token does to the depth a result type is read at.
_DEPTH = {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}

# The same for a parameter list, whose own parentheses lizard's `br_count` tracks.
_TYPE_BRACKETS = {"[": 1, "{": 1, "]": -1, "}": -1}

# A token that no type can hold at depth 0: whatever the signature was, it ended.
_ENDERS = frozenset({",", ";", "=", ":=", ")", "]", "}"})

# The states a signature is in once its parameter list has closed, or before it
# opened: the only ones a token can end it in.
_TAIL = frozenset({"_function_name", "_expect_function_dec", "_expect_function_impl"})

# Go keywords that can end a line inside a type without a semicolon following:
# the spec inserts one only after an identifier, a literal, a closing bracket and
# four statement keywords, none of which is a type's.
_GO_TYPE_KEYWORDS = frozenset({"chan", "func", "interface", "map", "struct"})


def _is_line_break(token: str) -> bool:
    """A raw newline, or a block comment spanning lines, which Go reads as one."""
    return token == "\n" or (token.startswith("/*") and "\n" in token)


def _semicolon_after(token) -> bool:
    """Whether Go inserts a semicolon at a line break after this token."""
    return token in (")", "]", "}") or ((token or "").isidentifier() and token not in _GO_TYPE_KEYWORDS)


class _SignatureStates(GoLikeStates):
    """GoLikeStates with a signature read to where the language ends it."""

    # The words whose `{` opens a type's body. Each language sets its own.
    _CONTAINERS = frozenset()

    def __init__(self, context):
        super().__init__(context)
        self._nested = 0              # brackets open inside a parameter list or a result
        self._awaits_type_body = False  # a container word waits for its `{`
        self._name = ""               # a word after `func` that may name a method

    # --- the signature opens ------------------------------------------------------

    def _state_global(self, token):
        super()._state_global(token)
        if token == self.FUNC_KEYWORD:
            self.context.crapkit_header = self

    def _function_name(self, token):
        if token in ("(", "{", "`"):
            return super()._function_name(token)
        if token.isidentifier() and token not in _GO_TYPE_KEYWORDS:
            self._name = token
            self._state = self._expect_function_dec
            return None
        self._result(token)
        self._state = self._expect_function_impl
        return None

    def _expect_function_dec(self, token):
        """A name followed by its parameter list or type parameters is a
        function's name. Anything else makes it the first word of a result."""
        name, self._name = self._name, ""
        if token in ("(", "["):
            self.context.add_to_function_name(name)
            return super()._expect_function_dec(token)
        self._result(name)
        return self._expect_function_impl(token)

    # --- the parameter list ---------------------------------------------------------

    @CodeStateMachine.read_inside_brackets_then("()", "_expect_function_impl")
    def _function_dec(self, token):
        if token not in ("(", ")"):
            self._parameter(token)

    def _parameter(self, token):
        """One token of the list. Only a token outside every nested bracket can
        start a parameter or name one; the rest go to the long name alone."""
        self._nested += _TYPE_BRACKETS.get(token, 0)
        if self.br_count > 1 or self._nested or token in _TYPE_BRACKETS:
            self.context.current_function.add_to_long_name(" " + token)
        else:
            self.context.parameter(token)

    # --- the result and the body ------------------------------------------------------

    def _expect_function_impl(self, token):
        if token == "{" and not (self._nested or self._awaits_type_body):
            return self.next(self._function_impl, token)
        self._result(token)
        return None

    def _result(self, token):
        """One token of a result type, read for the brackets it opens. A
        container word keeps waiting for its `{` across a `(enum)` group."""
        if self._nested == 0:
            self._awaits_type_body = token in self._CONTAINERS or (
                self._awaits_type_body and token in ("(", ")"))
        self._nested += _DEPTH.get(token, 0)

    def _function_impl(self, token):
        self._close()
        super()._function_impl(token)

    # --- the signature was a type ------------------------------------------------------

    def peek(self, token):
        """Drop the function this signature opened once `token` shows it had none."""
        if self._ended_by(token):
            self._drop()

    def _ended_by(self, token) -> bool:
        return self._in_tail() and token in _ENDERS

    def _in_tail(self) -> bool:
        return self._nested == 0 and getattr(self._state, "__name__", "") in _TAIL

    def _drop(self):
        """Hand the context back to the function around the type, with the
        lines the type's own tokens were counted to. lizard starts a function
        at nloc 1 for its first line, which the `func` token had already
        counted to the function around it."""
        context = self.context
        dropped = context.current_function
        context.current_function = context.stacked_functions.pop()
        context.current_function.nloc += dropped.nloc - 1
        self._close()
        self._state = self._state_global

    def _close(self):
        self.context.crapkit_header = None
        self._nested = 0
        self._awaits_type_body = False
        self._name = ""


class GoSignatureStates(_SignatureStates):
    """Go: a line break after a word or a closing bracket ends a signature."""

    _CONTAINERS = frozenset({"struct", "interface"})

    def _ended_by(self, token) -> bool:
        if _is_line_break(token):
            return self._in_tail() and _semicolon_after(self.last_token)
        return super()._ended_by(token)


class ZigSignatureStates(_SignatureStates):
    """Zig: `fn` then `(` is a function type, since a Zig function has a name."""

    FUNC_KEYWORD = "fn"
    _CONTAINERS = frozenset({"struct", "enum", "union", "opaque", "error"})

    # Zig's `type` is a type, not a declaration: lizard's ZigStates reads the
    # token after it as it reads any other.
    _type_definition = _SignatureStates._state_global

    def _ended_by(self, token) -> bool:
        opens_type = token == "(" and getattr(self._state, "__name__", "") == "_function_name"
        return opens_type or super()._ended_by(token)


class _Lookahead:
    """The reader half of analyze._ReaderLookahead: pass a raw token to the
    signature that is open, if one is."""

    def peek(self, token):
        header = self.context.crapkit_header
        if header is not None:
            header.peek(token)


class CorrectedGoReader(_Lookahead, _StockGoReader):
    """lizard's GoReader with its signatures read to where Go ends them."""

    # pylint: disable=too-few-public-methods

    def __init__(self, context):
        super().__init__(context)
        context.crapkit_header = None
        self.parallel_states = [GoSignatureStates(context)]


class CorrectedZigReader(_Lookahead, _StockZigReader):
    """lizard's ZigReader with its signatures read to where Zig ends them."""

    # pylint: disable=too-few-public-methods

    def __init__(self, context):
        super().__init__(context)
        context.crapkit_header = None
        self.parallel_states = [ZigSignatureStates(context)]


# Any filename picks the reader; the file is never opened.
_PROBES = {"GoReader": ("crapkit_registration_probe.go", CorrectedGoReader),
           "ZigReader": ("crapkit_registration_probe.zig", CorrectedZigReader)}


def register() -> None:
    """Make lizard resolve `.go` and `.zig` to the corrected readers. Idempotent.

    Raises RuntimeError when the rebind does not reach lizard's own resolution:
    a file measured with the stock reader scores wrong and looks fine.
    """
    for name, (probe, reader) in _PROBES.items():
        setattr(lizard_languages, name, reader)
        resolved = lizard.get_reader_for(probe)
        if resolved is not reader:
            raise RuntimeError(_unregistered(probe, reader, resolved))


def _unregistered(probe: str, reader: type, resolved: type | None) -> str:
    name = getattr(resolved, "__name__", "no reader")
    return (f"crapkit.lizardgolike.register() did not take: lizard resolves "
            f"'{probe.rpartition('.')[2]}' to {name}, not {reader.__name__}. lizard "
            f"{lizard.version} picks readers some other way than "
            f"lizard_languages.languages(); rewrite register() against the new mechanism.")
