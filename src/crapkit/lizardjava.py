"""A Java reader that finds, names and counts what lizard's misses.

Each fix below is an override on lizard 1.24.0's Java state machines. A token
none of them claims goes to lizard's state unchanged, so a file without these
shapes reads exactly as lizard reads it. The Java Language Specification SE 21
is the reference for each shape.

Methods lizard hid, invented or misnamed
----------------------------------------
* An annotation with arguments on a local variable, `@SuppressWarnings("x")
  int n = ...;`, hid every method after the one holding it. lizard read the
  arguments with the counter a method body keeps its braces in, so their `)`
  never closed them.
* The token after a bare annotation was dropped. A second annotation with
  arguments, `@Deprecated @InlineMe(...) int f()`, then read as a method named
  InlineMe, and `@Deprecated record P(int x) {...}` as a method named P that
  hid the record's methods (sec. 9.7: annotations are modifiers).
* An enum constant with a body, `ONE() { int value() {...} }`, read as a method
  named ONE that hid the methods its body declares (sec. 8.9.1).
* An anonymous class in a field of a top-level interface, `X F = new X() {...};`,
  read as a method named X that hid its methods (sec. 15.9.5).
* An annotation element's default, `String[] v() default {};`, read as a body,
  and any other default ran on to the next `{` in the file (sec. 9.6.1).
* A method of a class declared inside a method was named with its class twice,
  `A::A::go.run`; it reads `A::go.run`.

Parameters
----------
lizard names each parameter after the last word of its declaration, so
`String args[]` counted none. crapkit.lizardclike.ParameterCount counts the
declarations between the list's top-level commas, as it does for C.

Registration
------------
lizard's Java state machines start one another by module-global name
(`JavaFunctionBodyStates`, `JavaClassBodyStates`). `sub_state` below swaps each
one lizard starts for crapkit's twin, so the fixes reach every level without
rebinding a name in lizard's modules. JavaReader keeps lizard's class name,
because crapkit chooses language rules by reader name. `register()` rebinds it
in the `lizard_languages` namespace, the way crapkit.lizardclike registers the
C family; analyze.py calls it at module scope.
"""
from __future__ import annotations

from ._pygdefer import deferred_pygments
from .lizardclike import ParameterCount, name_levels_once

with deferred_pygments():  # lizard's Erlang reader would load pygments here
    import lizard
    import lizard_languages
    from lizard_languages.clike import CLikeNestingStackStates
    from lizard_languages.java import JavaReader as _StockJavaReader
    from lizard_languages.java import JavaStates
    from lizard_languages.java_body_states import JavaClassBodyStates, JavaFunctionBodyStates

_PAREN_DEPTH = {"(": 1, ")": -1}
_BRACE_DEPTH = {"{": 1, "}": -1}

# The tokens that end an enum's constant list: `;` before its other
# declarations, or the enum's own `}` when it declares nothing else.
_CONSTANTS_END = frozenset({";", "}"})


class _JavaFixes(ParameterCount):
    """What every Java state machine reads differently from lizard's."""

    def __init__(self, *args):
        super().__init__(*args)
        self.crapkit_enum = False
        self.crapkit_parens = 0
        self.crapkit_braces = 0

    def sub_state(self, state, callback=None, token=None):
        """Every machine lizard's states start is crapkit's twin of it."""
        super().sub_state(_twin(state), callback, token)

    def try_new_function(self, name):
        before = self.context.current_function
        super().try_new_function(name)
        if self.context.current_function is not before:
            name_levels_once(self.context)

    def _try_start_a_class(self, token, after_unqualified_annotation=False):
        self.crapkit_enum = token == "enum"
        return super()._try_start_a_class(token, after_unqualified_annotation)

    def _state_class_declaration(self, token):
        """An enum's body starts with its constants."""
        super()._state_class_declaration(token)
        if token == "{" and self.crapkit_enum:
            self._state.read_enum_constants()

    def _state_post_decorator(self, token):
        """The token after a bare annotation is read, not dropped."""
        super()._state_post_decorator(token)
        if token not in (".", "("):
            self._state(token)

    def _state_annotation_arguments(self, token):
        """An annotation's arguments, read to their own closing parenthesis."""
        if self._closes_the_parentheses(token):
            self._state = self._state_global

    def _closes_the_parentheses(self, token) -> bool:
        self.crapkit_parens += _PAREN_DEPTH.get(token, 0)
        return self.crapkit_parens == 0

    def _state_dec_to_imp(self, token):
        if token == "default":
            self._state = self._state_element_default
        else:
            super()._state_dec_to_imp(token)

    def _state_element_default(self, token):
        """An annotation element's default value, read to its `;`."""
        self.crapkit_braces += _BRACE_DEPTH.get(token, 0)
        if token == ";" and self.crapkit_braces == 0:
            self._state = self._state_global


class JavaFamilyStates(_JavaFixes, JavaStates):
    """lizard's Java file-level states, with the readings above."""

    def _state_global(self, token):
        """A top-level interface's body is read here, and its fields may create
        anonymous classes, which lizard read only in a class's body."""
        if token == "new":
            self.next(self._state_new)
        else:
            super()._state_global(token)


class JavaTypeBodyStates(_JavaFixes, JavaClassBodyStates):
    """lizard's class-body states, with the readings above."""

    def read_enum_constants(self):
        self._state = self._state_enum_constants

    def _state_enum_constants(self, token):
        """An enum's constants: annotations, arguments and class bodies, up to
        the `;` or `}` that ends them. A constant's body declares methods and
        is no method itself."""
        if token == "(":
            self.next(self._state_constant_arguments, token)
        elif token == "{":
            self.sub_state(JavaTypeBodyStates("(anonymous)", False, self.context), None, token)
        elif token in _CONSTANTS_END:
            self.next(self._state_global, token)

    def _state_constant_arguments(self, token):
        if self._closes_the_parentheses(token):
            self._state = self._state_enum_constants


class JavaMethodBodyStates(_JavaFixes, JavaFunctionBodyStates):
    """lizard's method-body states, with the readings above."""


def _twin(state):
    """crapkit's machine in place of one lizard's Java states started."""
    if isinstance(state, _JavaFixes):
        return state
    if isinstance(state, JavaFunctionBodyStates):
        # pylint: disable-next=protected-access
        return JavaMethodBodyStates(state.context, state._exit_with_brace_depth)
    return JavaTypeBodyStates(state.class_name, state.is_record, state.context)


class JavaReader(_StockJavaReader):
    """lizard's JavaReader with JavaFamilyStates in place of JavaStates."""

    # pylint: disable=too-few-public-methods
    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [JavaFamilyStates(context), CLikeNestingStackStates(context)]


# Any filename picks the reader; the file is never opened.
_PROBE = "crapkit_registration_probe.java"


def register() -> None:
    """Make lizard resolve `.java` to JavaReader. Idempotent.

    Raises RuntimeError when the rebind does not reach lizard's own resolution.
    A file measured with the stock reader is worse than a crash: its methods
    are missing and nothing says so.
    """
    lizard_languages.JavaReader = JavaReader
    resolved = lizard.get_reader_for(_PROBE)
    if resolved is not JavaReader:
        raise RuntimeError(_unregistered(resolved))


def _unregistered(resolved: type | None) -> str:
    name = "no reader" if resolved is None else f"{resolved.__module__}.{resolved.__qualname__}"
    return (f"crapkit.lizardjava.register() did not take: lizard resolves '.java' to "
            f"{name}, not crapkit.lizardjava.JavaReader. lizard {lizard.version} picks "
            f"readers some other way than lizard_languages.languages(); rewrite register() "
            f"against the new mechanism.")
