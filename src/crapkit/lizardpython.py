"""A Python reader that reads a def's signature to its body colon. Upstream defect: crapkit #72.

lizard 1.24.0's PythonReader ends a def inside its own signature in two shapes,
and the def then reads as two lines at ccn 1 whatever its body holds, so it
passes the complexity ceiling, the commit hook, `rescore --gate` and `verify`:

    def f(value: int) -> tuple[        def make(cls_name, *, bases=(),
        int, int                                slots=False):
    ]:                                     if slots: ...
        if value > 0: ...

The left one is a return annotation opened on the def line. The right one is a
line break after a parameter default that holds brackets, which is what black
and ruff write for a long signature. Measured before the fix: 39 such defs in
the 3,742 stdlib and site-packages files of crapkit's own Python 3.11
environment, and 20 in the 3,130 files of a large consumer repo.

Three mechanics combine, all in lizard_languages/python.py:

  1. `PythonStates._dec` takes the first `)` token for the end of the parameter
     list, so the `)` of a default like `()` ends it early.
  2. `PythonStates._state_colon` follows `:` into the body and sends anything
     else, `->` included, to `_state_global`: the reader never follows a return
     annotation to its colon.
  3. `PythonReader.preprocess` sets nesting from each line's indent once the
     def's long name ends with `)`. A signature line indented deeper than the
     body pushes the def's nesting level there, the body's first line pops it,
     and the pop ends the def before its body.

The fix, and what it keeps
--------------------------
`PythonSignatureStates` counts bracket depth from the def's `(` and leaves the
signature only at a `:` at depth 0, after the parameter list and after any
return annotation. `PythonSignatureReader.preprocess` sets no nesting while the
states are inside a signature, so the body's first line is the one that pushes
the def. A body on the colon line (`) -> None: ...`) pushes no line: see "A
body on the colon line" below.

From the def's `(` on, every token still reaches the long name and the
parameter list exactly as lizard routes it. lizard stops both at the
signature's first `)` whatever its depth, and a def of that shape that lizard
already read whole carries that spelling as its ratchet key, so this reader
keeps it: `make( cls_name , * , bases = ( )` above. The states also end where
lizard ends for the same signature written on one line: `_state_first_line`
after `):`, `_state_global` after a return annotation or after a signature
lizard had closed early.

What moves, only around a def lizard cut off: its end line, ccn, nloc, token
count and cognitive score now cover its body, while its long name stays; an
enclosing def gives back the conditions lizard had charged to it from the
nested body; a def nested inside the cut-off def gains its parent's name as a
prefix, as it has under a parent read whole.

Type parameter lists (PEP 695)
------------------------------
lizard names a def after the last token before its `(`, and a generic def
keeps its type parameter list there: `def f[T](a: int):` reads as
`]( a : int )`, and `def f[T: (int, str)](a):` as `:( int , str )`. That name
is no part of the def's own. It is the ratchet key; it collides with every
other generic def in the file that takes the same parameters; the cognitive
pass reads each `]` in the body as a call to the def; and a type parameter
list over several lines starts the def on the list's last line.

`_type_parameters` counts the list to its `]` with the same `depth` the
parameter list uses and hands none of its tokens to lizard's naming. The def
is named by its name token and its parameters read as lizard spells them:
`f( a : int )`. The reader changes this spelling, and the nested names below,
because neither is a prefix of the def's own name the way
`make( cls_name , * , bases = ( )` is.

Nested def names
----------------
lizard qualifies a new def with the full name of every def on its nesting
stack (`NestingStack.with_namespace`), and each of those names already carries
its own parents. Two deep that reads `a.b`; three deep it read `a.a.b.c`, and
four deep `a.a.b.a.a.b.c.d`. `_name_under_parent` qualifies the def by its
innermost enclosing def alone: `a.b.c`. A class adds nothing to the name, as
under lizard.

A body on the colon line
------------------------
lizard lists a def when a later line pushes a nesting level for it, and
`def g(self): pass` pushes none unless a line of its signature did. Such a def
is never listed and stays pending on the nesting stack: the lines after it at
its own indent are charged to it, so an enclosing def loses its own `if`, and
the next line indented deeper pushes it, so a later def carries its name,
`test_autospec.g.a( self )` for a method `a` of a class declared after `g`.
Measured before the fix over 5,746 stdlib, site-packages and application files:
ast finds 1,826 such defs, lizard left 1,392 of them unlisted, and 213 later
defs carried a one-line def's name.

`_SignatureIndents` ends such a def with the logical line that holds its body:
at the first newline outside brackets, or at the end of the file. A line inside
the body's brackets sets no nesting. A `def` or `class` ends the body even when
the bracket count says it is still open, since no bracket can hold either one:
lizard's f-string expansion reads `f"{x:(>10}"` as `x : ( > 10`, and before
this every def after it replaced the pending one unlisted. The def is then
listed as the same def with its body on the next line is, one line shorter:
`def f(): ...` reads `f( )` at ccn 1 over one line, an `@overload` stub on one
line takes its twin key as a two-line stub does, and the lines after it go back
to its parent. Cognitive and nesting are the exception: crapkit.lizardcognitive
starts a Python body at the def's first newline, so a one-line def reads
cognitive 0 and nesting 0 whatever its body holds, and
`def f(x, y): return 1 if x and y else 2` reads cognitive 0 where its two-line
form reads 2. Both fields are reported and never gated.

The class name
--------------
lizardcognitive picks its Python rules by `type(reader).__name__` starting with
"python". A name like `CorrectedPythonReader` would score every Python file
with the brace rules.

Registration
------------
The same mechanism as crapkit.lizardrust, whose docstring explains it:
`register()` rebinds `lizard_languages.PythonReader`, which
`lizard_languages.languages()` reads on every call, verifies the rebind through
`lizard.get_reader_for`, and raises if it did not take. analyze.py calls it at
module scope beside the other readers' registrations, so pool workers register
too.

Retirement
----------
Four tests in tests/unit/test_lizardpython.py pin the stock reader's wrong
answers: test_stock_reader_still_cuts_the_issue_def_off for #72,
test_stock_reader_still_names_a_generic_def_after_a_bracket for PEP 695,
test_stock_reader_still_repeats_the_outer_names_three_deep for nested names, and
test_stock_reader_still_leaves_a_one_line_def_pending for a body on the colon
line. Each fails on the lizard release that fixes its part. Delete this module,
with the `register()` call, once all four fail; while one still passes, the
override for that part stays.
"""
from __future__ import annotations

from ._pygdefer import deferred_pygments

with deferred_pygments():  # lizard's Erlang reader would load pygments here
    import lizard
    import lizard_languages
    from lizard_languages.python import PythonIndents, PythonStates, count_spaces
    from lizard_languages.python import PythonReader as _StockPythonReader

_OPENERS = frozenset("([{")
_CLOSERS = frozenset(")]}")
# Keywords that start a statement and never sit inside brackets.
_BODY_ENDERS = frozenset({"def", "class"})

# The states between the `def` keyword and the body colon. `_state_colon` is
# the one lizard enters after the parameter list's `)`.
_SIGNATURE_STATES = frozenset({
    "_function", "_type_parameters", "_dec", "_state_parameterized_type_annotation", "_state_colon",
    "_rest_of_signature",
})

# Any filename picks the reader; the file is never opened.
_PROBE = "crapkit_registration_probe.py"


def _is_continuation(token: str) -> bool:
    """A backslash line continuation, which lizard's tokenizer keeps as one token."""
    return token.startswith("\\") and not token[1:].strip()


def _depth_change(token: str) -> int:
    if token in _OPENERS:
        return 1
    if token in _CLOSERS:
        return -1
    return 0


def _name_under_parent(context, token: str) -> None:
    """Name the def lizard just opened by its innermost enclosing def and `token`.

    lizard joins the full name of every def on the nesting stack, and each of
    those already carries its own parents: see "Nested def names" above.
    """
    parent = context.last_function
    name = f"{parent.name}.{token}" if parent is not None else token
    function = context.current_function
    function.long_name = name + function.long_name[len(function.name):]
    function.name = name


class PythonSignatureStates(PythonStates):
    """lizard's PythonStates, reading a signature to the colon at bracket depth 0.

    `depth` counts the brackets open since the def's own `(`, and before
    that since the `[` of a type parameter list. The inherited states keep
    every decision about the long name and the parameter list; these
    overrides choose where a state goes next, keep a type parameter list out
    of the name, and qualify a nested def by its innermost enclosing def.

    `colon_read` turns True on the body colon. `_SignatureIndents` reads it on
    the next code token, and clears it there or at the end of the line.
    """

    def __init__(self, context, reader):
        super().__init__(context, reader)
        self.depth = 0
        self.colon_read = False

    @property
    def in_signature(self) -> bool:
        """True from the `def` keyword until the body colon has been read."""
        return self._state.__name__ in _SIGNATURE_STATES

    def _function(self, token):
        if token == "[":  # PEP 695: `def f[T: int](a):`, whose name came first
            self.depth = 1
            self._state = self._type_parameters
        elif token == "(":
            self.depth = 1
            super()._function(token)
        else:
            super()._function(token)
            _name_under_parent(self.context, token)

    def _type_parameters(self, token):
        """A def's type parameter list: counted to its `]`, never named."""
        self.depth += _depth_change(token)
        if self.depth == 0:
            self._state = self._function

    def _dec(self, token):
        if token == ")" and self.depth > 1:
            # lizard's parameter list ends here too, spelled the same way; the
            # signature does not.
            self.depth -= 1
            self.context.add_to_long_function_name(" )")
            self._state = self._rest_of_signature
            return
        self.depth += _depth_change(token)
        super()._dec(token)

    def _state_parameterized_type_annotation(self, token):
        self.depth += _depth_change(token)
        super()._state_parameterized_type_annotation(token)

    def _state_colon(self, token):
        if token == "->":
            self._state = self._rest_of_signature
            return
        if _is_continuation(token):  # `) \` then `-> ...:` on the next line
            return
        self.colon_read = token == ":"
        super()._state_colon(token)

    def _rest_of_signature(self, token):
        """The tokens lizard sent to `_state_global` before the body: counted, not named."""
        if token == ":" and self.depth == 0:
            self._state = self._state_global
            self.colon_read = True
            return
        self.depth += _depth_change(token)


class _SignatureIndents(PythonIndents):
    """lizard's per-line indent bookkeeping, with no nesting set from a signature line.

    lizard sets nesting from every line's first code token once the def's long
    name ends with `)`. Inside a signature that is the defect: see mechanic 3.
    Here a signature line sets no nesting, and where the body starts decides
    what ends the def:

      * on the next line: that line's first token pushes the def, and a later
        dedent past it ends it, as under lizard
      * on the colon line (`) -> None: ...`, `def f(x): return x`): the end of
        that body ends the def. See "A body on the colon line" above.

    A code token after the body colon on the colon's own line starts a body on
    the colon line. `body_depth` counts that body's brackets and is None
    outside one: its lines inside brackets set no nesting, a newline at depth 0
    or below ends it, and so does a `def` or `class`, which no bracket can hold.
    """

    def __init__(self, context, states):
        super().__init__(context)
        self.states = states
        self.spaces = 0
        self.leading = True
        self.body_depth = None

    def see(self, token: str) -> None:
        if token == "\n":
            self._line_ends()
        elif self.leading:
            self._leading(token)
        elif self.body_depth is not None:
            self._in_body(token)
        elif self.states.colon_read:
            self._after_colon(token)

    def reset(self) -> None:
        if self.body_depth is not None:  # the file ends on a body on its colon line
            self._end_body()
        super().reset()

    def _line_ends(self) -> None:
        self.spaces, self.leading = 0, True
        self.states.colon_read = False  # a colon that ends its line opens the body below
        if self.body_depth is not None and self.body_depth <= 0:
            self._end_body()

    def _leading(self, token: str) -> None:
        if token.isspace():
            self.spaces += count_spaces(token)
            return
        self.leading = False
        if not token.startswith("#"):
            self._line_starts(token)

    def _line_starts(self, token: str) -> None:
        """A line's first code token: no nesting in a signature or inside a body on its colon line.

        Outside both, lizard's own condition always holds: every way out of a
        signature passes the `)` that ends the long name.
        """
        if self.body_depth is not None:
            self._in_body(token)
        elif not self.states.in_signature:
            self.set_nesting(self.spaces, token)

    def _in_body(self, token: str) -> None:
        """A token of a body on its colon line, counted unless it ends the body.

        No bracket can hold a `def` or a `class`, so either ends the body
        whatever the count says. The count can be wrong: lizard's f-string
        expansion hands a format spec's fill character over as a token, so
        `f"{x:(>10}"` leaves a `(` open, and the def after it would otherwise
        replace this one unlisted. The lines before that `def`, a decorator or
        the `async` of an `async def`, stay with this def.
        """
        if token in _BODY_ENDERS:
            self._end_body()
            self.set_nesting(self.spaces, token)
        else:
            self.body_depth += _depth_change(token)

    def _after_colon(self, token: str) -> None:
        if token.isspace() or token.startswith("#"):
            return
        self.states.colon_read = False
        self.body_depth = _depth_change(token)

    def _end_body(self) -> None:
        """End the def whose body sat on its colon line, as a dedent past it would.

        The def is still pending on lizard's nesting stack: pushing it and
        popping it lists it and hands the lines after it back to its parent.
        """
        self.body_depth = None
        self.context.add_bare_nesting()
        self.context.pop_nesting()


class PythonSignatureReader(_StockPythonReader):
    """lizard's PythonReader with a def's signature read to its body colon (#72)."""

    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [PythonSignatureStates(context, self)]

    def preprocess(self, tokens):
        """lizard's preprocess, with the indent rules of `_SignatureIndents`."""
        indents = _SignatureIndents(self.context, self.parallel_states[0])
        for token in self._soft_keyword_lookahead(tokens):
            indents.see(token)
            if not token.isspace() or token == "\n":
                yield token
        indents.reset()


def register() -> None:
    """Make lizard resolve `.py` to PythonSignatureReader. Idempotent.

    Raises RuntimeError when the rebind does not reach lizard's own resolution.
    A Python file measured with the defective reader is worse than a crash: the
    score is wrong and looks fine.
    """
    lizard_languages.PythonReader = PythonSignatureReader
    resolved = lizard.get_reader_for(_PROBE)
    if resolved is not PythonSignatureReader:
        raise RuntimeError(_unregistered(resolved))


def _unregistered(resolved: type | None) -> str:
    name = getattr(resolved, "__name__", "no reader")
    return (f"crapkit.lizardpython.register() did not take: lizard resolves '.py' to "
            f"{name}, not PythonSignatureReader. lizard {lizard.version} picks readers "
            f"some other way than lizard_languages.languages(); rewrite register() "
            f"against the new mechanism, or drop this module if lizard reads these "
            f"signatures itself.")
