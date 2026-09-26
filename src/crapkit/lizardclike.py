"""C, C++ and Objective-C readers that count every parameter a function declares.

lizard 1.24.0 reads a C-family parameter list token by token and names each
parameter after the last word of its declaration, so a parameter whose
declaration does not end in its name is not counted:

    int f(int*, char)                  1 of 2: an unnamed pointer ends in `*`
    int f(const int arr[4])            0 of 1: an array ends in `]`
    int f(const int (&arr)[4])         0 of 1: so does a reference to one
    int g(void (*r)())                 0 of 1: `void` is dropped wherever it stands
    - (int)pairFor:(int)a to:(int)b    0 of 2: a method's arguments are never read

The count here is the number of declarations the list holds, split at its
top-level commas (ISO/IEC 9899:2018 6.7.6.3, ISO/IEC 14882:2020 [dcl.fct]). A
list that holds only `void` declares none, and neither does `...`. An
Objective-C method declares one parameter per argument name.

The long name keeps lizard's spelling, `g((*r)())` included: it is the
ratchet key, and every C function with a `void *` parameter prints that way.

Accepted, documented, not solved
--------------------------------
* A default argument holding an unparenthesized `<`, `f(bool b = x < y, int c)`,
  opens an angle bracket that never closes, so the rest of the list reads as one
  parameter. Parenthesized, `(x < y)`, it reads right.

Registration
------------
The two readers keep lizard's class names, CLikeReader and ObjCReader. crapkit
chooses language rules by reader name (lizardcognitive's declarator rule is one),
because the name is the language: JavaReader subclasses CLikeReader and must not
inherit C's rules. The module path tells them apart, and it is part of the
analysis cache key, so a record the stock reader wrote is never reused.

`register()` rebinds `CLikeReader` and `ObjCReader` in the `lizard_languages`
namespace, which `lizard_languages.languages()` reads on every call (the way
crapkit.lizardrust registers its reader), and lizard's own `CLikeReader`, the
reader lizard falls back to for a suffix no reader declares. analyze.py calls it
at module scope, so a pool worker registers in its own interpreter.
"""
from __future__ import annotations

from ._pygdefer import deferred_pygments

with deferred_pygments():  # lizard's Erlang reader would load pygments here
    import lizard
    import lizard_languages
    from lizard_languages.clike import CLikeReader as _StockCLikeReader
    from lizard_languages.clike import CLikeStates
    from lizard_languages.objc import ObjCReader as _StockObjCReader
    from lizard_languages.objc import ObjCStates

# What a bracket does to the depth a parameter list is split at. A comma inside
# a nested list, a template argument list, an array bound or a braced default
# separates nothing.
_BRACKET_DEPTH = {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}
_ANGLE_DEPTH = {"<": 1, ">": -1}

# The two declarations that declare no parameter: `(void)` and C's `(...)`.
_NO_PARAMETER = (["void"], ["..."])


def declared_parameters(tokens: list[str]) -> int:
    """How many parameters a list declares, given the tokens between its parentheses."""
    return sum(1 for declaration in _declarations(tokens)
               if declaration and declaration not in _NO_PARAMETER)


def _declarations(tokens: list[str]) -> list[list[str]]:
    """The list split at its top-level commas."""
    declarations, depth, angle = [[]], 0, 0
    for token in tokens:
        if token == "," and depth == angle == 0:
            declarations.append([])
            continue
        declarations[-1].append(token)
        depth += _BRACKET_DEPTH.get(token, 0)
        angle = _angle_depth(angle, token, depth)
    return declarations


def _angle_depth(angle: int, token: str, depth: int) -> int:
    """Angle brackets count only outside other brackets, so a `<` inside a
    parenthesized default is a comparison, and a `>` never takes it below 0."""
    if depth:
        return angle
    return max(0, angle + _ANGLE_DEPTH.get(token, 0))


class _ParameterCount:
    """Counts the declarations of each parameter list lizard's `_state_dec` reads.

    The count lands on the function current at the list's closing parenthesis
    as `crapkit_params`, which analyze._record reads ahead of lizard's names.
    lizard's own `bracket_stack` is emptied at each list's opening parenthesis:
    a `<` that never closed in an earlier list otherwise left it one deep, and
    every later function in the file read its parameters as nested tokens.
    """

    def _state_dec(self, token):
        if self.br_count == 0:
            self.bracket_stack, self.crapkit_list = [], []
        else:
            self.crapkit_list.append(token)
        super()._state_dec(token)
        if self.br_count == 0:
            count = declared_parameters(self.crapkit_list[:-1])
            self.context.current_function.crapkit_params = count


class _ObjCArguments:
    """One parameter per Objective-C argument name, the token after its `(type)`."""

    def _state_objc_param(self, token):
        method = self.context.current_function
        method.crapkit_params = getattr(method, "crapkit_params", 0) + 1
        super()._state_objc_param(token)


class CFamilyStates(_ParameterCount, CLikeStates):
    """lizard's C and C++ function states, with the parameter count above."""


class ObjCFamilyStates(_ObjCArguments, _ParameterCount, ObjCStates):
    """lizard's Objective-C states: its C functions counted as C, its methods by argument."""


class CLikeReader(_StockCLikeReader):
    """lizard's CLikeReader with CFamilyStates in place of CLikeStates."""

    # pylint: disable=too-few-public-methods
    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = (CFamilyStates(context), *self.parallel_states[1:])


class ObjCReader(_StockObjCReader):
    """lizard's ObjCReader with ObjCFamilyStates in place of ObjCStates."""

    # pylint: disable=too-few-public-methods
    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [ObjCFamilyStates(context), *self.parallel_states[1:]]


# Any filename picks the reader; the file is never opened.
_PROBES = {"crapkit_registration_probe.c": CLikeReader,
           "crapkit_registration_probe.m": ObjCReader}


def register() -> None:
    """Make lizard resolve the C family's suffixes to these readers. Idempotent.

    Raises RuntimeError when the rebind does not reach lizard's own resolution.
    A file measured with the stock reader is worse than a crash: the parameter
    count is wrong and looks fine.
    """
    lizard_languages.CLikeReader = CLikeReader
    lizard_languages.ObjCReader = ObjCReader
    lizard.CLikeReader = CLikeReader
    for probe, reader in _PROBES.items():
        resolved = lizard.get_reader_for(probe)
        if resolved is not reader:
            raise RuntimeError(_unregistered(probe, reader, resolved))


def _unregistered(probe: str, reader: type, resolved: type | None) -> str:
    return (f"crapkit.lizardclike.register() did not take: lizard resolves "
            f"'.{probe.rpartition('.')[2]}' to {_qualified(resolved)}, not "
            f"{_qualified(reader)}. lizard {lizard.version} picks readers some other way "
            f"than lizard_languages.languages(); rewrite register() against the new "
            f"mechanism.")


def _qualified(reader: type | None) -> str:
    """Module and name, because both readers carry the same class name."""
    if reader is None:
        return "no reader"
    return f"{reader.__module__}.{reader.__qualname__}"
