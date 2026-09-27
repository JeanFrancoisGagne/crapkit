"""Go and Zig readers that end a function's signature where the language ends it.

lizard 1.24.0 reads both languages with one state machine, GoLikeStates, and it
misread a signature five ways:

  * after a parameter list it waited for the next `{` wherever that was. A
    function type has no body, so `var cb func(int) error` in a function, or
    Zig's `const cb: *const fn (u8) void = &f;`, opened a function that took the
    next `if` block as its own, and the enclosing function lost that `if`.
  * at package level it read every `func (` as a method receiver, so after
    `var hooks []func()` the next line's `func` became the method's name and
    the function declared there had no row, and a package-level literal,
    `var f = func(a, b int) {...}`, counted no parameters.
  * a result type with braces (`struct{ a int }`, Zig's `struct { usize, usize
    }`, `error{Oops}!u8`, `union(enum) {...}`) handed its own `{` to that wait,
    and the function ended on its signature line.
  * each comma in the parameter list started a parameter, and lizard names a
    parameter by the word its text ends with. So `f func(int, string) error`
    counted 2, `lessThan: fn (T, T) bool` counted 2, and `v interface{}`
    counted 0.
  * a Zig name written as a string, `fn @"weird name"(x: i32)`, reached it as
    `@` and a string, so the function had no row.

It also read the `type` of a Go type switch, `switch v.(type) {`, as a type
declaration that took the switch's `{`, so the switch's `}` ended the function.
Its tokenizer read a `//` comment ending in a backslash on into the next line, as
C splices lines, and the text of a Zig multiline string (`\\...` lines) as code,
so a brace in either lost or ended a function.

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
  * At Go package level, `func (...)` followed by a name and `(` is a method.
    Anything else after the group is a result type or a body, so the group was
    a literal's parameter list or a function type, and a literal's parameters
    are counted from it. The long name keeps the group as lizard writes it,
    `(a,b int)`, the key a package-level literal already had.
  * A `func` right after a `]` on the same line is an element type
    (`[]func(){f, g}`, `map[string]func(int) int{...}`), and the `{` after its
    signature opens the composite literal, not a body. lizard read some such
    literals, `[]func(){f, g}` among them, as a function at ccn 1.
  * A Zig `@"..."` is one token, and it names the function it follows `fn`
    in. keys.bare_name cuts it whole, spaces and all.
  * A Go `type` followed by `)` is a type switch's guard and declares nothing.
  * A `//` comment ends at its line's end, and each `\\` line of a Zig
    multiline string is one token.
  * A `}` that reaches the machine reading the file closes nothing and is
    ignored. lizard's machine returned from the file there and never came back,
    so each later function ended at its body's `{` or had no row.

A dropped function has to be dropped before any extension counts the token that
ends it, or that token's condition, nesting and line go to a function that no
longer exists: lizard's extensions see each token before the reader does. So the
reader decides on RAW tokens, newlines included, through `peek`, which
analyze._ReaderLookahead calls ahead of every counter. The signature that is
open registers itself on the context (`crapkit_header`), so a token costs one
attribute read when none is.

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

    # A `//` comment ends at its line's end: Go and Zig splice no lines.
    from .lizardlinecomment import LINE_COMMENT

# What a token does to the depth a result type is read at.
_DEPTH = {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}

# The same for a parameter list, whose own parentheses lizard's `br_count` tracks.
_TYPE_BRACKETS = {"[": 1, "{": 1, "]": -1, "}": -1}

# A token that no type can hold at depth 0: whatever the signature was, it ended.
_ENDERS = frozenset({",", ";", "=", ":=", ")", "]", "}"})

# The states a signature is in once its parameter list has closed, or before it
# opened: the only ones a token can end it in.
_TAIL = frozenset({"_function_name", "_after_group", "_expect_function_dec",
                   "_expect_function_impl"})

# The prongs a Zig switch takes when no other prong matches: `else =>`, and
# `_ =>` over a non-exhaustive enum. NIST SP 500-235 sec. 4.1 counts a switch's
# case labels with the default excluded, as lizard counts a C `case` and not
# `default`.
_DEFAULT_PRONGS = frozenset({"else", "_"})

# A Zig identifier can be any string, written `@"..."`. lizard's tokenizer reads
# the `@` and the string as two tokens, and a name that is not one word named no
# function: `fn @"weird name"(x: i32) i32 {` reported no row, or a row named ''.
_ZIG_QUOTED_NAME = r'|@"(?:\\.|[^"\\\n])*"'

# Each line of a Zig multiline string opens with `\\` and runs to its end. lizard
# read the text as code, so a `}` in it ended the function and an `if` counted.
_ZIG_STRING_LINE = r"|\\\\[^\n]*"

# Go keywords that can end a line inside a type without a semicolon following:
# the spec inserts one only after an identifier, a literal, a closing bracket and
# four statement keywords, none of which is a type's.
_GO_TYPE_KEYWORDS = frozenset({"chan", "func", "interface", "map", "struct"})


def counted_prong(previous) -> bool:
    """Whether a Zig prong's `=>` is a decision, from the token before it."""
    return previous not in _DEFAULT_PRONGS


def _is_name(token: str) -> bool:
    """A word, or a Zig identifier spelled as a string, `@"weird name"`."""
    return token.isidentifier() or token.startswith('@"')


def _may_name_a_method(token: str) -> bool:
    """Whether a token after `func` or a receiver can name a function: a word
    that is no type keyword."""
    return _is_name(token) and token not in _GO_TYPE_KEYWORDS


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
        self._element = False         # `func` came right after `]`: an element type
        self._line = 0                # the line of the last token read
        # The first group after a package-level `func`, as (token, depth) pairs,
        # while it may still be a literal's parameters; None once decided.
        self._group = None

    def __call__(self, token, reader=None):
        exits = super().__call__(token, reader)
        self._line = self.context.current_line
        return exits

    # --- a `}` that closes nothing ------------------------------------------------

    # The machine the reader holds reads the file; each `{` hands a block to a
    # clone, whose `}` returns to the machine that made it.
    _reads_the_file = True

    def statemachine_clone(self):
        clone = super().statemachine_clone()
        clone._reads_the_file = False
        return clone

    def statemachine_return(self):
        """Return from the block a clone reads. A `}` that reaches the machine
        reading the file closes nothing: lizard set its `to_exit` for good, and
        from there each state that returned what its next state returned, such as
        the wait for a body, ended its function at that body's `{`."""
        if not self._reads_the_file:
            super().statemachine_return()

    # --- the signature opens ------------------------------------------------------

    def _state_global(self, token):
        # `[]func()` is a type. A `]` that ends the line before it is not part of
        # it: Go ends the statement there (`x := a[i]`).
        element = self.last_token == "]" and self._line == self.context.current_line
        super()._state_global(token)
        if token == self.FUNC_KEYWORD:
            self.context.crapkit_header = self
            self._element = element
            self._group = []

    def _function_name(self, token):
        if token in ("(", "{", "`"):
            return super()._function_name(token)
        if _may_name_a_method(token):
            self._name = token
            self._state = self._expect_function_dec
            return None
        self._result(token)
        self._state = self._expect_function_impl
        return None

    def _expect_function_dec(self, token):
        """A name followed by its parameter list or type parameters is a
        function's name, and the group before it, if any, was its receiver. A Go
        method has no type parameters, so after a group `[` opens a generic
        result (`func(a int) List[int]`). Anything else makes the name the first
        word of a result."""
        name, self._name = self._name, ""
        if token == "(" or (token == "[" and not self._group):
            self._group = None
            self.context.add_to_function_name(name)
            return super()._expect_function_dec(token)
        self._count_group()
        self._result(name)
        return self._expect_function_impl(token)

    # --- a package-level group: a receiver or a literal's parameters -----------------

    @CodeStateMachine.read_inside_brackets_then("()", "_after_group")
    def _member_function(self, token):
        """lizard reads the group after a package-level `func` as a method's
        receiver, into the long name. The first one is also kept, to be counted
        if the token after it shows it was a literal's parameter list."""
        self.context.add_to_long_function_name(token)
        if self._group is not None:
            self._group.append((token, self.br_count))

    def _after_group(self, token):
        """Only a method's name can follow a receiver. Anything else, a result
        or the body, shows the group was a literal's parameter list."""
        if not _may_name_a_method(token):
            self._count_group()
        return self._function_name(token)

    def _count_group(self):
        """Count the group kept as a receiver as the parameter list it was,
        through the test `_parameter` applies. The long name keeps the group as
        the receiver reading wrote it: `(a int)`, the key such a literal has."""
        group, self._group = self._group, None
        if not group:
            return
        function = self.context.current_function
        long_name = function.long_name
        for token, depth in group:
            if token not in ("(", ")") and self._at_top(token, depth):
                function.add_parameter(token)
        function.long_name = long_name

    # --- the parameter list ---------------------------------------------------------

    @CodeStateMachine.read_inside_brackets_then("()", "_expect_function_impl")
    def _function_dec(self, token):
        if token not in ("(", ")"):
            self._parameter(token)

    def _parameter(self, token):
        """One token of the list. A token at the top of it starts a parameter
        or names one; the rest go to the long name alone."""
        if self._at_top(token, self.br_count):
            self.context.parameter(token)
        else:
            self.context.current_function.add_to_long_name(" " + token)

    def _at_top(self, token, depth) -> bool:
        """Whether a token of a parameter list, `depth` parentheses deep, sits
        outside every bracket nested in the list: only such a token can start
        or name a parameter."""
        self._nested += _TYPE_BRACKETS.get(token, 0)
        return not (depth > 1 or self._nested or token in _TYPE_BRACKETS)

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
        return self._in_tail() and (token in _ENDERS or self._opens_literal(token))

    def _opens_literal(self, token) -> bool:
        """A `{` after an element type's signature opens the composite literal
        around it: `[]func() error{f, g}` is a slice, not a function."""
        return token == "{" and self._element and not self._awaits_type_body

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
        self._group = None


class GoSignatureStates(_SignatureStates):
    """Go: a line break after a word or a closing bracket ends a signature."""

    _CONTAINERS = frozenset({"struct", "interface"})

    def _ended_by(self, token) -> bool:
        if _is_line_break(token):
            return self._in_tail() and _semicolon_after(self.last_token)
        return super()._ended_by(token)

    def _type_definition(self, token):
        """A type declaration names its type next. A `)` there shows the `type`
        was a type switch's guard, `v.(type)`, and the switch's `{` opens a
        block: lizard took that `{` for the declared type's, and the switch's `}`
        then ended the function around it."""
        if token == ")":
            self._state = self._state_global
        else:
            super()._type_definition(token)


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


class _ProngStates(CodeStateMachine):
    """One condition per Zig switch prong, the default prong free.

    lizard's ZigReader counts every `=>` through its condition set, `else =>`
    included, so a switch of two prongs and an else read ccn 4. This runs beside
    the signature states and reports through the hook lizard's own condition
    counter uses. `last_token` is the code token before the `=>`: the reader
    never sees a newline, so a prong spread over lines reads the same.
    """

    def _state_global(self, token):
        if token == "=>" and counted_prong(self.last_token):
            self.context.add_condition()


class _Lookahead:
    """The reader half of analyze._ReaderLookahead: pass a raw token to the
    signature that is open, if one is."""

    def peek(self, token):
        header = self.context.crapkit_header
        if header is not None:
            header.peek(token)


class CorrectedGoReader(_Lookahead, _StockGoReader):
    """lizard's GoReader with its signatures read to where Go ends them, and
    `select` read as the switch it is by analyze's modified rule."""

    # pylint: disable=too-few-public-methods
    _keyword_select = True

    def __init__(self, context):
        super().__init__(context)
        context.crapkit_header = None
        self.parallel_states = [GoSignatureStates(context)]

    @staticmethod
    def generate_tokens(source_code, addition="", token_class=None):
        """lizard's tokens, with a `//` comment ended at its line's end."""
        return _StockGoReader.generate_tokens(source_code, LINE_COMMENT + addition, token_class)


class CorrectedZigReader(_Lookahead, _StockZigReader):
    """lizard's ZigReader with its signatures read to where Zig ends them and
    its switch prongs counted with the default free."""

    # pylint: disable=too-few-public-methods
    # `=>` counts through _ProngStates, which can tell the default prong from
    # the others; analyze's modified rule asks `counts_prong` the same question.
    # Zig has no `?:` either: a `?` marks an optional type or unwraps one, so
    # the cognitive pass, which reads this set, charges it nothing.
    _ternary_operators = set()
    counts_prong = staticmethod(counted_prong)

    # The words lizard's nesting extension reads as opening a structure. Its
    # default set also holds `try`, which holds no block and so never closed
    # (three tries read nesting 3), `?`, and `case`, `def` and `foreach`, which
    # are names in Zig. That extension nests on `&&` and `||` whatever this set
    # holds, so a Zig error-set merge still reads one level there.
    loops = frozenset({"if", "for", "while", "catch"})

    def __init__(self, context):
        super().__init__(context)
        context.crapkit_header = None
        self.parallel_states = [ZigSignatureStates(context), _ProngStates(context)]

    @staticmethod
    def generate_tokens(source_code, addition="", token_class=None):
        """lizard's tokens, with a quoted identifier `@"..."` read as one, a `//`
        comment ended at its line's end, and each line of a multiline string read
        as one."""
        additions = _ZIG_QUOTED_NAME + LINE_COMMENT + _ZIG_STRING_LINE + addition
        return _StockZigReader.generate_tokens(source_code, additions, token_class)


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
