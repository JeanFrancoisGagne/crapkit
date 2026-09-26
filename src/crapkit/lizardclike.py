"""C, C++ and Objective-C readers that find, name and count what lizard's miss.

Each fix below is an override on lizard 1.24.0's own state machines. A token
none of them claims goes to lizard's state unchanged, so a file without these
shapes reads exactly as lizard reads it.

Parameters
----------
lizard names each parameter after the last word of its declaration, so a
parameter whose declaration does not end in its name was not counted:

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

Functions lizard hid, invented or misnamed
------------------------------------------
* A `<` comparison, in a default template argument (`bool E = (N < 19)`) or in
  a member initializer (`static constexpr bool v = N < 19;`), opened a template
  bracket that only the next `>` in the file closed. Every function after it
  had no row: fmt's chrono.h kept rows for its first thousand lines.
  `_TemplateBrackets` reads an angle bracket inside parentheses as a comparison,
  and a statement's end ends a list that was never a template's.
* A trailing return type holding braces, `-> decltype(all(Tag{}))`, gave a
  declaration a row: the `{` read as its body.
* An attribute between a parameter list and the body took the function's
  name: `int run(int a) __attribute__((noinline)) {` read `__attribute__`, and
  an Objective-C method with `API_AVAILABLE(ios(10))` read `)`, started on the
  attribute's line and counted the body's first `)` as recursion.
* An Objective-C class extension's instance variables, `@interface E () { int
  _a; }`, read as a function named E, and in a `.m` file any word after a C
  function's parameter list, a prototype's `;` included, named a method.

Accepted, documented, not solved
--------------------------------
* A default argument holding an unparenthesized `<`, `f(bool b = x < y, int c)`,
  opens an angle bracket that never closes, so the rest of the list reads as one
  parameter. Parenthesized, `(x < y)`, it reads right.
* A bare word between a parameter list and the body followed by a second one,
  `int f() NOINLINE COLD {`, still reads the way lizard reads it: the function
  is lost. One bare word, or `override` and `final`, reads right.

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
    from lizard_languages.clike import CLikeNestingStackStates
    from lizard_languages.clike import CLikeReader as _StockCLikeReader
    from lizard_languages.clike import CLikeStates
    from lizard_languages.code_reader import CodeStateMachine
    from lizard_languages.objc import ObjCReader as _StockObjCReader
    from lizard_languages.objc import ObjCStates

# What a bracket does to the depth a parameter list is split at. A comma inside
# a nested list, a template argument list, an array bound or a braced default
# separates nothing.
_BRACKET_DEPTH = {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}
_ANGLE_DEPTH = {"<": 1, ">": -1}
_PAREN_DEPTH = {"(": 1, ")": -1}

# What ends a statement outside parentheses, and so ends anything that was only
# read as a template argument list.
_STATEMENT_ENDS = frozenset({";", "{", "}"})

# The tokens other than a word that can end a return type: `int *f()`,
# `Foo& f()`, `vector<int> f()`.
_TYPE_ENDS = frozenset({"*", "&", "&&", ">"})

# The words lizard itself reads after a parameter list.
_DECLARATOR_WORDS = frozenset({"const", "throw", "throws", "noexcept"})

# C++'s virt-specifiers, which only ever follow a member function's parameter list.
_VIRT_SPECIFIERS = frozenset({"override", "final"})

# The Objective-C keywords, after `@`, that open a class or protocol head.
_OBJC_CONTAINERS = frozenset({"interface", "implementation", "protocol"})

# The groups a class head may hold, each read to its own closing bracket.
_OBJC_HEAD_GROUPS = {"(": "_state_objc_category", "<": "_state_objc_protocols",
                     "{": "_state_objc_instance_variables"}

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


class _TemplateBrackets:
    """One `<...>` list, read to the `>` that closes it.

    lizard counts every `<` and `>` it meets, so the `<` of a comparison in a
    default template argument, `bool E = (N < 19)`, opened a bracket that never
    closed. Inside parentheses an angle bracket is a comparison and counts
    nothing, which is how the C++ grammar reads it ([temp.param]).
    """

    def __init__(self):
        self.parens = 0
        self.angles = 0

    def closes(self, token: str) -> bool:
        self.parens += _PAREN_DEPTH.get(token, 0)
        if self.parens == 0:
            self.angles += _ANGLE_DEPTH.get(token, 0)
        return self.angles <= 0


def _is_word(token: str) -> bool:
    return token[0].isalpha() or token[0] == "_"


def _ends_a_type(token: str | None) -> bool:
    """Whether the token before a function's name can end its return type."""
    return token is not None and (_is_word(token) or token in _TYPE_ENDS)


class _CFixes:
    """What the C family's function states read differently from lizard's.

    Each override hands every token it does not claim to lizard's own state,
    so a declaration none of these shapes touches reads exactly as before.
    """

    def __init__(self, context):
        super().__init__(context)
        self.crapkit_list = []
        self.crapkit_template = None
        self.crapkit_typed = False
        self.crapkit_last_name = None
        self.crapkit_word = None
        self.crapkit_return = 0

    def try_new_function(self, name):
        """Note whether this name follows a return type. The `>` that closes a
        `template <...>` head ends no type: what follows it opens the declaration."""
        after_head = self.crapkit_last_name == "template"
        self.crapkit_typed = _ends_a_type(self.last_token) and not after_head
        self.crapkit_last_name = name
        super().try_new_function(name)

    def _state_dec(self, token):
        """lizard's parameter list, whose declarations are counted here.

        The count lands on the function current at the list's closing
        parenthesis as `crapkit_params`, which analyze._record reads ahead of
        lizard's names. lizard's own `bracket_stack` is emptied at each list's
        opening parenthesis: a `<` that never closed in an earlier list left it
        one deep, and every later function in the file read its parameters as
        nested tokens.
        """
        if self.br_count == 0:
            self.bracket_stack, self.crapkit_list = [], []
        else:
            self.crapkit_list.append(token)
        super()._state_dec(token)
        if self.br_count == 0:
            count = declared_parameters(self.crapkit_list[:-1])
            self.context.current_function.crapkit_params = count

    def _state_template_in_name(self, token):
        """A template argument list in a name, read to its own closing `>`.

        A `;`, `{` or `}` outside parentheses ends it unread: it was a
        comparison in an initializer, `static constexpr bool v = N < 19;`,
        which lizard read as a template list running to the next `>` in the file.
        """
        brackets = self._template_brackets()
        if token in _STATEMENT_ENDS and brackets.parens == 0:
            self.crapkit_template = None
            self.next(self._state_global, token)
            return
        self.context.add_to_function_name(token)
        if brackets.closes(token):
            self.crapkit_template = None
            self._state = self._state_function

    def _template_brackets(self) -> _TemplateBrackets:
        if self.crapkit_template is None:
            self.crapkit_template = _TemplateBrackets()
        return self.crapkit_template

    def _state_trailing_return(self, token):
        """A trailing return type, read to the `;` or `{` outside its parentheses.

        `-> decltype(all(Tag{}))` holds braces, and lizard took the first `{`
        for the body of what is only a declaration.
        """
        self.crapkit_return += _PAREN_DEPTH.get(token, 0)
        if self.crapkit_return == 0 and token in (";", "{"):
            self._state = self._state_dec_to_imp
            self._state(token)

    def _state_dec_to_imp(self, token):
        """After the parameter list: a word here may be an attribute.

        lizard read a word between a parameter list and the body as an old-style
        C parameter declaration, and a `(` after it as a new function, so
        `int run(int a) __attribute__((noinline)) {` was a function named
        `__attribute__`. A word that follows the list of a function declared
        with a return type waits one token: before a `(` it is an attribute
        (`__attribute__((...))`, `API_AVAILABLE(ios(10))`) whose arguments are
        skipped. A word before anything else keeps lizard's reading, and so
        does a function with no return type. That is what keeps `MACRO(x)` on
        one line and `TEST(a, b) {` on the next two declarations, and
        `FMT_VISIBILITY("hidden") auto f(int a) {` after a template head one
        function named f. `override` and `final` are read and dropped, as lizard
        drops a lone one, so a word after them is read as it would be without.
        """
        if token in _VIRT_SPECIFIERS:
            return
        if self.crapkit_typed and _is_word(token) and token not in _DECLARATOR_WORDS:
            self.crapkit_word = token
            self._state = self._state_attribute_word
            return
        CLikeStates._state_dec_to_imp(self, token)

    def _state_attribute_word(self, token):
        """The token after that word: a `(` opens the attribute's arguments."""
        if token == "(":
            self.next(self._state_attribute_arguments, token)
            return
        CLikeStates._state_dec_to_imp(self, self.crapkit_word)
        self._state(token)

    @CodeStateMachine.read_inside_brackets_then("()", "_state_dec_to_imp")
    def _state_attribute_arguments(self, _):
        """An attribute's arguments, nested parentheses included."""


class _ObjCFixes:
    """What the Objective-C states read differently from lizard's.

    A method is named by the word after a `- (type)` or `+ (type)`, and lizard
    named a function after any word that follows a parameter list, so in a `.m`
    file an attribute after a C function's list, or the `;` of a prototype,
    became a method. Only a list opened right after `-` or `+` names a method
    here; any other list is a C function's and reads as C.
    """

    def __init__(self, context):
        super().__init__(context)
        self.crapkit_method = False

    def _state_global(self, token):
        """`@interface` and `@implementation` open a class, whose head this reads."""
        if self.last_token == "@" and token in _OBJC_CONTAINERS:
            self._state = self._state_objc_class_name
            return
        super()._state_global(token)

    def _state_dec(self, token):
        if self.br_count == 0:
            self.crapkit_method = self.last_token in ("-", "+")
        super()._state_dec(token)

    def _state_dec_to_imp(self, token):
        if self.crapkit_method:
            ObjCStates._state_dec_to_imp(self, token)
        else:
            _CFixes._state_dec_to_imp(self, token)

    def _state_objc_param(self, token):
        """One parameter per argument name, the token after its `(type)`."""
        method = self.context.current_function
        method.crapkit_params = getattr(method, "crapkit_params", 0) + 1
        super()._state_objc_param(token)

    def _state_objc_dec(self, token):
        """A word after an argument waits one token: before a `:` it is the
        next part of the selector, before anything else an attribute."""
        if _is_word(token):
            self.crapkit_word = token
            self._state = self._state_objc_word
        else:
            super()._state_objc_dec(token)

    def _state_objc_word(self, token):
        if token == ":":
            ObjCStates._state_objc_dec(self, self.crapkit_word)
            ObjCStates._state_objc_dec_begin(self, token)
        else:
            self._state = self._state_objc_attributes
            self._state(token)

    def _state_objc_dec_begin(self, token):
        """A word right after the method's name is an attribute: NS_REQUIRES_SUPER."""
        if _is_word(token):
            self._state = self._state_objc_attributes
        else:
            super()._state_objc_dec_begin(token)

    def _state_objc_attributes(self, token):
        """Attributes between a method's selector and its body or its `;`.
        lizard took the first one for the method's name, or for a new function."""
        if token == "(":
            self.next(self._state_objc_attribute_arguments, token)
        elif token == "{":
            self.next(self._state_entering_imp, token)
        elif not _is_word(token):
            self.next(self._state_global, token)

    @CodeStateMachine.read_inside_brackets_then("()", "_state_objc_attributes")
    def _state_objc_attribute_arguments(self, _):
        """An attribute's arguments, nested parentheses included."""

    def _state_objc_class_name(self, _):
        self._state = self._state_objc_class_head

    def _state_objc_class_head(self, token):
        """A class's head after its name: `: Super`, `(Category)`, `<Protocols>`.

        An instance-variable block follows the head, and lizard read the `{`
        after `@interface Extension ()` as the body of a function named
        `Extension`. The block is skipped whole; anything else ends the head.
        """
        if token == ":":
            self._state = self._state_objc_superclass
        elif token in _OBJC_HEAD_GROUPS:
            self.next(getattr(self, _OBJC_HEAD_GROUPS[token]), token)
        else:
            self.next(self._state_global, token)

    def _state_objc_superclass(self, _):
        self._state = self._state_objc_class_head

    @CodeStateMachine.read_inside_brackets_then("()", "_state_objc_class_head")
    def _state_objc_category(self, _):
        """`(Category)`, or `()` for a class extension."""

    @CodeStateMachine.read_inside_brackets_then("<>", "_state_objc_class_head")
    def _state_objc_protocols(self, _):
        """`<NSCopying, NSCoding>`."""

    @CodeStateMachine.read_inside_brackets_then("{}", "_state_global")
    def _state_objc_instance_variables(self, _):
        """`{ int _count; }`, which declares no function."""


class CFamilyStates(_CFixes, CLikeStates):
    """lizard's C and C++ function states, with the readings above."""


class ObjCFamilyStates(_ObjCFixes, _CFixes, ObjCStates):
    """lizard's Objective-C states: its C functions read as C, its methods as methods."""


class CFamilyNestingStates(CLikeNestingStackStates):
    """lizard's namespace and class tracker, with a template head read the way
    `_TemplateBrackets` reads it: the `<` of `bool E = (N < 19)` left lizard's
    tracker inside the template head for the rest of the file."""

    def __init__(self, context):
        super().__init__(context)
        self.crapkit_template = None

    def _template_declaration(self, token):
        if self.crapkit_template is None:
            self.crapkit_template = _TemplateBrackets()
        if self.crapkit_template.closes(token):
            self.crapkit_template = None
            self._state = self._state_global


class CLikeReader(_StockCLikeReader):
    """lizard's CLikeReader with CFamilyStates in place of CLikeStates."""

    # pylint: disable=too-few-public-methods
    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = (CFamilyStates(context), CFamilyNestingStates(context),
                                *self.parallel_states[2:])


class ObjCReader(_StockObjCReader):
    """lizard's ObjCReader with ObjCFamilyStates in place of ObjCStates."""

    # pylint: disable=too-few-public-methods
    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [ObjCFamilyStates(context), CFamilyNestingStates(context),
                                *self.parallel_states[2:]]


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
