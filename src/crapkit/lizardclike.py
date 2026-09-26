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
  attribute's line and counted the body's first `)` as recursion. A word read
  that way must follow the list of a function with a return type, and
  `decltype`, `typeof` and the rest of `_NOT_A_NAME` never name one, so
  `constexpr decltype(auto) f(int a)` stays f.
* An Objective-C class extension's instance variables, `@interface E () { int
  _a; }`, read as a function named E, and in a `.m` file any word after a C
  function's parameter list, a prototype's `;` included, named a method.
* A member function of a class defined inside a function had no row, and the
  function around the class paid for its decisions (ISO/IEC 14882:2020
  [class.local]). `_LocalClassBody` reads the class and names the member
  `outer.Local::twice`, the way lizard names a function nested in another.
* A class head holding an attribute with arguments, `struct alignas(16) Vec {`
  or `class __declspec(dllexport) Foo {`, read as a function named after the
  attribute whose body was the class, and its members lost the class from
  their names. An export macro, `class Q_CORE_EXPORT QString {`, named them
  `Q_CORE_EXPORTQString::size`. `_ClassHead` reads a head at every scope, and
  `_head_name` takes the class's name from it.
* A function whose declarator sits in parentheses was named after its return
  type (ISO/IEC 9899:2018 6.7.6.3, ISO/IEC 14882:2020 [dcl.fct]). `int
  (*get(int k))(int)`, a function returning a function pointer, read `int( *
  get(int k))( int)` and counted the returned type's parameters, and after a
  return type ending in `*`, `char *(*get(void))(void)`, lizard read no function.
  A name in parentheses, `T (max)()`, read `T( max)()`; so did a name a macro
  builds, `STRINGLIB(find)(...)`. `nested_declarator` finds the name and the
  list beside it, and the rest of the declarator is read as return type.
* A C++20 requires-clause after a parameter list, `void f(T t) requires C<T> {`
  (ISO/IEC 14882:2020 [dcl.decl]), read as an old-style C parameter: the
  function had no row, and one was named after the first statement of its
  body, `if( t)`, or after a constructor's first member initializer. A
  concept's requires-expression, `concept C = requires (T a) { a + 1; };`,
  read as a function named requires. `_RequiresClause` reads a trailing clause
  to the body, and `_state_requires` skips a requires-expression at file scope.

The `&&` of a reference
-----------------------
`for (auto&& x : r)`, `static_cast<Widget&&>(w)` and `[](auto&& x)` declare
references, and lizard's cyclomatic and nesting counters and crapkit's
cognitive pass read each `&&` as a logical and. A token pass respells the ones
that can only declare before any counter sees them; the comment above
`declarator_ands` gives the rules. A parameter list's own `&&` reads like
`f(a && b)`, so the nesting it opened is forgotten at the list's `)`.

Accepted, documented, not solved
--------------------------------
* A lambda parameter of a named type, `[](Widget&& w)`, still costs its `&&`,
  because `(Widget&& w)` after `]` reads like a call's `(a && b)`.
* A default argument holding an unparenthesized `<`, `f(bool b = x < y, int c)`,
  opens an angle bracket that never closes, so the rest of the list reads as one
  parameter. Parenthesized, `(x < y)`, it reads right.
* A bare word between a parameter list and the body followed by a second one,
  `int f() NOINLINE COLD {`, still reads the way lizard reads it: the function
  is lost. One bare word, or `override` and `final`, reads right.
* A function returning a pointer to a member function, `int (S::*pick(int
  k))(int)`, still takes lizard's name, `int( S :: * pick(int k))( int)`.
* A macro with arguments between a specifier and the function's name, `static
  EXPORT(x) f(int a)`, reads as a function named EXPORT with an attribute: the
  shape is the one `int run(int) __attribute__((cold))` has.

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

import itertools

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

# Words with arguments that spell a type or an attribute and never name a
# function: `decltype(auto) f(int a)` declares f.
_NOT_A_NAME = frozenset({"decltype", "typeof", "__typeof__", "__typeof", "_Atomic",
                         "alignas", "_Alignas", "__declspec", "__attribute__", "__attribute"})

# C++'s virt-specifiers, which only ever follow a member function's parameter list.
_VIRT_SPECIFIERS = frozenset({"override", "final"})

# The Objective-C keywords, after `@`, that open a class or protocol head.
_OBJC_CONTAINERS = frozenset({"interface", "implementation", "protocol"})

# The groups a class head may hold, each read to its own closing bracket.
_OBJC_HEAD_GROUPS = {"(": "_state_objc_category", "<": "_state_objc_protocols",
                     "{": "_state_objc_instance_variables"}

# The two declarations that declare no parameter: `(void)` and C's `(...)`.
_NO_PARAMETER = (["void"], ["..."])

# The keywords that open a class head, and what else a base clause holds besides
# words: `: public Base<int>, ns::Other`.
_CLASS_KEYS = frozenset({"struct", "class", "union"})
_BASE_TOKENS = frozenset({"::", "<", ">", ",", "..."})

# What stands before a C++20 `requires` at file scope: a template head's `>`, a
# concept's `=`, a constraint's `&&`, `||`, `(` or `!`, or `requires` itself.
# A `;` too: lizard reads both branches of an `#if`, so a concept's second
# definition, under `#else`, follows the first one's `;`.
_BEFORE_A_REQUIREMENT = frozenset({">", "=", "&&", "||", "(", "!", "requires", ";"})

# What ends a trailing requires-clause outside brackets: the body's `{`, a
# declaration's `;`, a constructor's member-initializer `:`.
_CLAUSE_ENDS = frozenset({"{", ";", ":"})

# What ends a class's name in its head: lizard's own list.
_NAME_ENDS = frozenset({"<", ":", "final", "[", "extends", "implements"})
_BRACE_DEPTH = {"{": 1, "}": -1}

# What stands between a nested declarator's `(` and the name it declares:
# `(*get(int k))`, `(*const get())`, `(&row(int n))`, Objective-C's `(^make(int k))`.
_POINTER_OPS = frozenset({"*", "&", "&&", "^", "const", "volatile"})

# What may follow that function's own parameter list inside the parentheses:
# `int (*get(int k) const)(int)`. lizard spells each of these into a long name.
_LIST_QUALIFIERS = frozenset({"const", "&", "&&"})

# The tokens that end a return type before a nested declarator's `(` at file
# scope, where lizard reads no function: `char *(*get(void))(void)`.
_POINTER_ENDS = frozenset({"*", "&", "&&"})

# What opens one part of the rest of a return type after a nested declarator:
# `(int)` in `int (*get(int k))(int)`, `[4]` in `int (*rows(int n))[4]`.
_SUFFIX_OPENERS = frozenset({"(", "["})


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


def nested_declarator(tokens: list[str]):
    """The name, parameters and qualifiers of the function a nested declarator declares.

    `int (*get(int k))(int)` declares get, a function of `int k` returning a
    pointer to a function of `int` (ISO/IEC 9899:2018 6.7.6.3, ISO/IEC
    14882:2020 [dcl.fct]). Given the tokens inside the first parentheses,
    `* get ( int k )`, this returns (["get"], ["int", "k"], []). None for
    anything else, such as the function pointer `* fp` or the array `* tab [ 4 ]`.
    """
    ops = sum(1 for _ in itertools.takewhile(_POINTER_OPS.__contains__, tokens))
    if ops in (0, len(tokens)):
        return None
    if tokens[ops] == "(":
        return _declarator_in_group(tokens[ops:])
    return _named_function(tokens[ops:])


def _declarator_in_group(tokens: list[str]):
    """`( * pick ( int k ) ) ( double )`: a declarator nested once more, then
    only lists and bounds of the return type."""
    inside, after = _split_group(tokens)
    if inside is None or not _only_suffixes(after):
        return None
    return nested_declarator(inside)


def _named_function(tokens: list[str]):
    """`get ( int k )`, or `S :: get ( int k ) const`: a name, then its list."""
    size = _qualified_name_length(tokens)
    params, after = _split_group(tokens[size:])
    if size == 0 or params is None or not _LIST_QUALIFIERS.issuperset(after):
        return None
    return tokens[:size], params, after


def _split_group(tokens: list[str]):
    """The tokens inside a leading `( ... )` and the ones after it, or (None,
    None) when the tokens open no group or it never closes."""
    depth = 0
    for i, token in enumerate(tokens):
        depth += _PAREN_DEPTH.get(token, 0)
        if depth <= 0:
            return (tokens[1:i], tokens[i + 1:]) if depth == 0 and token == ")" else (None, None)
    return None, None


def _only_suffixes(tokens: list[str]) -> bool:
    """`( double )`, `[ 4 ]`, one after another, and nothing else."""
    depth = 0
    for token in tokens:
        if depth == 0 and token not in _SUFFIX_OPENERS:
            return False
        depth += _BRACKET_DEPTH.get(token, 0)
    return depth == 0


def _qualified_name_length(tokens: list[str]) -> int:
    """How many leading tokens spell a name, `get` or `S :: get`; 0 for none."""
    size = 0
    while size < len(tokens) and _extends_the_name(tokens[size], size):
        size += 1
    return size if size % 2 else 0


def _extends_the_name(token: str, position: int) -> bool:
    return token == "::" if position % 2 else _is_word(token)


def _is_a_name(tokens: list[str]) -> bool:
    """`max` or `std :: max`, and nothing more."""
    return bool(tokens) and _qualified_name_length(tokens) == len(tokens)


def _forget_the_parameters(fn) -> None:
    """Leave lizard's nesting counter where a list with nothing in it leaves it.

    The counter read a parameter list as code: the `&&` of `void take(Widget&&
    w)`, or a `?:` in a default argument, opened a level that nothing closed,
    and a function with no structure read nesting 1. lizard's cyclomatic column
    already starts over at the body. The fields are lizard_ext/lizardnd.py's.
    """
    fn.nesting_depth = fn.max_nesting_depth = fn.hidden_bracket = fn.condition_depth = 0
    fn.in_condition = fn.logical_operator_added = fn.prev_was_else = fn.bracket_loop = False


def _is_word(token: str) -> bool:
    return token[0].isalpha() or token[0] == "_"


def _ends_a_type(token: str | None) -> bool:
    """Whether the token before a function's name can end its return type."""
    return token is not None and (_is_word(token) or token in _TYPE_ENDS)


def name_levels_once(context) -> None:
    """Spell the levels around a function nested in another one once.

    lizard qualifies a new function's name with every level open around it,
    and a function's own name already holds the levels open around that
    function, so they were spelled twice: `ns::ns::outer.Local::twice`, or in
    Java `A::A::go.run`. The name now starts at the enclosing function's.
    """
    fn, outer = context.current_function, context.last_function
    if outer is None:
        return
    own = fn.long_name.find(outer.name + ".")
    if own > 0:
        fn.name = fn.long_name = fn.long_name[own:]


class ParameterCount:
    """A parameter list whose declarations are counted, for any lizard state
    machine built on CLikeStates: C, C++, Objective-C and Java. It takes the
    arguments of the machine it extends: Java's class-body states take three."""

    def __init__(self, *args):
        super().__init__(*args)
        self.crapkit_list = []

    def _state_dec(self, token):
        """lizard's parameter list, whose declarations are counted here.

        The count lands on the function current at the list's closing
        parenthesis as `crapkit_params`, which analyze._record reads ahead of
        lizard's names, and the nesting the list opened is forgotten there.
        lizard's own `bracket_stack` is emptied at each list's opening
        parenthesis: a `<` that never closed in an earlier list left it one
        deep, and every later function in the file read its parameters as
        nested tokens.
        """
        if self.br_count == 0:
            self.bracket_stack, self.crapkit_list = [], []
        else:
            self.crapkit_list.append(token)
        super()._state_dec(token)
        if self.br_count == 0:
            fn = self.context.current_function
            fn.crapkit_params = declared_parameters(self.crapkit_list[:-1])
            _forget_the_parameters(fn)


class _CFixes(ParameterCount):
    """What the C family's function states read differently from lizard's.

    Each override hands every token it does not claim to lizard's own state,
    so a declaration none of these shapes touches reads exactly as before.
    """

    def __init__(self, context):
        super().__init__(context)
        self.crapkit_template = None
        self.crapkit_typed = False
        self.crapkit_last_name = None
        self.crapkit_word = None
        self.crapkit_return = 0
        self.crapkit_head = None
        self.crapkit_class = None
        self.crapkit_held = []
        self.crapkit_named = []
        self.crapkit_suffix = 0
        self.crapkit_declarator = []
        self.crapkit_clause = None

    def try_new_function(self, name):
        """Note whether this name follows a return type. The `>` that closes a
        `template <...>` head ends no type: what follows it opens the declaration.
        `decltype` and the other words of `_NOT_A_NAME` take arguments but name
        no function, so the word after their `)` is read as lizard reads it."""
        after_head = self.crapkit_last_name == "template"
        self.crapkit_typed = (_ends_a_type(self.last_token) and not after_head
                              and name not in _NOT_A_NAME)
        self.crapkit_last_name = name
        super().try_new_function(name)

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
        if token == "requires":
            self.crapkit_clause = _RequiresClause()
            self._state = self._state_requires_clause
        elif self._may_be_an_attribute(token):
            self.crapkit_word = token
            self._state = self._state_attribute_word
        else:
            CLikeStates._state_dec_to_imp(self, token)

    def _may_be_an_attribute(self, token) -> bool:
        return self.crapkit_typed and _is_word(token) and token not in _DECLARATOR_WORDS

    def _state_requires_clause(self, token):
        """A trailing requires-clause, `void f(T t) requires C<T> {` (ISO/IEC
        14882:2020 [dcl.decl]), read to what ends it. lizard read `requires` as
        an old-style C parameter, lost the function, and named a row after the
        body's first statement: `if( t)`. A `&&` in the clause opens no nesting
        level, as one in the parameter list opens none."""
        if self.crapkit_clause.ends(token):
            _forget_the_parameters(self.context.current_function)
            self._state = self._state_dec_to_imp
            self._state(token)

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

    def _state_dec(self, token):
        """lizard's parameter list, then what the list turned out to hold."""
        super()._state_dec(token)
        if self.br_count == 0:
            self._read_declarator(self.crapkit_list[:-1])

    def _read_declarator(self, tokens: list[str]) -> None:
        """A list that holds a nested declarator, `(*get(int k))`, declares the
        function it names. A list that holds only a name, `(max)`, names the
        function whose list follows it. lizard named both after their return
        type: `int( * get(int k))( int)`, `int( max)( int a)`."""
        nested = nested_declarator(tokens)
        if nested is not None:
            self._declare(*nested)
        elif _is_a_name(tokens):
            self.crapkit_named = tokens
            self._state = self._state_after_a_name

    def _state_after_a_name(self, token):
        self._state = self._state_dec_to_imp
        if token == "(":
            self._start_function(self.crapkit_named)
        self._state(token)

    def _declare(self, name: list[str], params: list[str], qualifiers: list[str]) -> None:
        """The function a nested declarator names, with its own list read the
        way any list is; the rest of the declarator belongs to its return type."""
        self._start_function(name)
        for token in ("(", *params, ")"):
            self._state(token)
        for qualifier in qualifiers:
            self.context.add_to_long_function_name(" " + qualifier)
        self.crapkit_suffix = 0
        self._state = self._state_return_suffix

    def _start_function(self, name: list[str]) -> None:
        """A function named `get` or `S :: get`, from the line its declaration starts on."""
        start = self.context.current_function.start_line or self.context.current_line
        self.try_new_function(name[0])
        self.context.current_function.start_line = start
        for token in name[1:]:
            self._state(token)
        self.crapkit_typed = True

    def _state_return_suffix(self, token):
        """`(int)` in `int (*get(int k))(int)`, or `[4]`: lists and bounds of the
        return type, which declare nothing. What follows is read as what follows
        any parameter list."""
        if self.crapkit_suffix == 0 and token not in _SUFFIX_OPENERS:
            self._state = self._state_dec_to_imp
            self._state(token)
            return
        self.crapkit_suffix += _BRACKET_DEPTH.get(token, 0)

    def _state_global(self, token):
        """`struct`, `class` and `union` open a head, read before lizard sees it,
        and so do a `(` after `*` or `&`, which may open a nested declarator, and
        `requires`, which names no function."""
        if self._opens_a_class_head(token):
            self.crapkit_class, self.crapkit_held = _ClassHead(), [token]
            self._state = self._state_class_head
        elif token == "(" and self.last_token in _POINTER_ENDS:
            self.crapkit_declarator = [token]
            self._state = self._state_pointer_declarator
        elif self._opens_a_requirement(token):
            self._state = self._state_requires
        else:
            super()._state_global(token)

    def _opens_a_class_head(self, token) -> bool:
        return token in _CLASS_KEYS and self.last_token != "enum"

    def _opens_a_requirement(self, token) -> bool:
        """`requires` after a template head, an `=`, a `&&` or another
        `requires`. After a type it is a C function's name: `int requires(int a)`."""
        return token == "requires" and self.last_token in _BEFORE_A_REQUIREMENT

    def _state_requires(self, token):
        """The token after `requires` at file scope. A requires-expression,
        `concept C = requires (T a) { a + 1; };`, read as a function named
        requires whose body was the requirements; its parameters and its
        requirements are skipped. A requires-clause before a declaration,
        `requires C<T> void f(T t)`, reads on as lizard reads it."""
        if token == "(":
            self.next(self._state_requirement_parameters, token)
        elif token == "{":
            self.next(self._state_requirements, token)
        else:
            self.next(self._state_global, token)

    @CodeStateMachine.read_inside_brackets_then("()", "_state_after_requirement_parameters")
    def _state_requirement_parameters(self, _):
        """`(T a)`, or the parenthesized constraint of a clause: `(N > 1)`."""

    def _state_after_requirement_parameters(self, token):
        if token == "{":
            self.next(self._state_requirements, token)
        else:
            self.next(self._state_global, token)

    @CodeStateMachine.read_inside_brackets_then("{}", "_state_global")
    def _state_requirements(self, _):
        """`{ a + 1; }`: a requires-expression's requirements, which are no body."""

    def _state_pointer_declarator(self, token):
        """A group after `*` or `&`, held to its `)`: `char *(*get(void))(void)`
        declares get, where lizard read no function at all. A group that is no
        declarator, `a * (b + c)`, and a `;` or `}` before the group closes,
        send the tokens back to be read the way lizard reads them."""
        held = self.crapkit_declarator
        held.append(token)
        inside, _ = _split_group(held)
        if inside is None and token not in (";", "}"):
            return
        self._state = self._state_global
        nested = None if inside is None else nested_declarator(inside)
        if nested is None:
            self._read_again(held)
        else:
            self._declare(*nested)

    def _state_class_head(self, token):
        """A class head, dropped at its `{`, which then reads as lizard reads it.

        lizard read `struct alignas(16) Vec {` as a function named alignas whose
        body was the class. Tokens that turn out to be no class head, `struct S
        *make(int x) {`, are read again the way lizard reads them.
        """
        opened = self.crapkit_class.reads(token)
        if opened is None:
            self.crapkit_held.append(token)
            return
        held, self.crapkit_held = self.crapkit_held, []
        self._state = self._state_global
        if opened:
            self._state(token)
        else:
            self._read_again(held + [token])

    def _read_again(self, tokens: list[str]) -> None:
        super()._state_global(tokens[0])  # the keyword, as lizard reads it
        for token in tokens[1:]:
            self(token)

    def _state_imp(self, token):
        """A function's body, where a class may be defined.

        lizard read a body as braces and nothing else, so a member function of a
        local class had no row and its decisions were charged to the function
        around it. The class's body is read by a machine of its own, the way the
        file's global scope is read, and this body resumes after its `}`.
        """
        if self._opens_a_local_class(token):
            outer = self.context.current_function
            self.sub_state(self.crapkit_class_body(self.context, outer), None, token)
            return
        super()._state_imp(token)

    def _opens_a_local_class(self, token) -> bool:
        """True at the `{` that ends a class head: `struct Local {`."""
        head, self.crapkit_head = self.crapkit_head, None
        if head is not None:
            opened = head.reads(token)
            self.crapkit_head = head if opened is None else None
            return bool(opened)
        if self._opens_a_class_head(token):
            self.crapkit_head = _ClassHead()
        return False


class _LocalClassBody:
    """A local class's body, read to the `}` that closes it.

    Its members are read as the file's global scope is read. Between them the
    function around the class is current again, so a data member's tokens are
    not charged to a function that never opened, and neither is the class's
    closing `}`, which the enclosing function's brace count must see.
    """

    def __init__(self, context, outer=None):
        super().__init__(context)
        self.crapkit_outer = outer
        self.crapkit_braces = 0

    def _state_global(self, token):
        self.crapkit_braces += _BRACE_DEPTH.get(token, 0)
        if self.crapkit_braces == 0:
            self.statemachine_return()
            return
        super()._state_global(token)
        if self._state == self._state_global:
            self.context.current_function = self.crapkit_outer

    def try_new_function(self, name):
        """A member is named `outer.Local::twice`, or `ns::outer.Local::twice`."""
        super().try_new_function(name)
        name_levels_once(self.context)


class _ClassHead:
    """The tokens between `struct`, `class` or `union` and a class body's `{`.

    A head holds at most one name, `final`, attributes (`[[maybe_unused]]`,
    and a word with arguments: `alignas(16)`, `__attribute__((packed))`) and a
    base clause after `:` (ISO/IEC 14882:2020 [class.pre]). Anything else, a
    second name (`struct point p = {1, 2}`), an operator, or a parenthesis
    after anything but a word (`sizeof(struct foo)`), makes the keyword part of
    a declaration or an expression, and the body reads on as lizard reads it.
    """

    def __init__(self):
        self.names = 0
        self.bases = False
        self.attribute = 0  # brackets open in an attribute
        self.word = None    # the last word, counted as a name unless `(` follows it

    def reads(self, token: str):
        """True at the `{` that opens the body, False once the tokens cannot be
        a class head, None while they still can."""
        if self._in_an_attribute(token):
            return None
        if self._count_the_word() > 1:
            return False
        if token == "{":
            return True
        if self.bases:
            return _in_a_base_clause(token)
        return self._reads_the_name(token)

    def _in_an_attribute(self, token: str) -> bool:
        """`[` opens an attribute, and so does a `(` after a word before the name."""
        if self.attribute or token == "[" or self._word_takes_arguments(token):
            self.word = None
            self.attribute += _BRACKET_DEPTH.get(token, 0)
            return True
        return False

    def _word_takes_arguments(self, token: str) -> bool:
        """`alignas(16)` or `EXPORT(x)`, where `struct S f(void)` is a function."""
        return token == "(" and self.word is not None and self.names == 0

    def _count_the_word(self) -> int:
        if self.word is not None:
            self.names += self.word != "final"
            self.word = None
        return self.names

    def _reads_the_name(self, token: str):
        if token == ":":
            self.bases = True
            return None
        if not _is_word(token):
            return False
        self.word = token
        return None


def _in_a_base_clause(token: str):
    return None if _is_word(token) or token in _BASE_TOKENS else False


class _RequiresClause:
    """The tokens of a trailing requires-clause, up to the one that ends it.

    Outside brackets, a `{` opens the body, a `;` ends a declaration and a `:`
    opens a constructor's member initializers. A requires-expression inside the
    clause, `requires requires (T x) { x + 1; }`, holds braces of its own: the
    first `{` after its `requires` opens its requirements, not the body.
    """

    def __init__(self):
        self.depth = 0
        self.expression = False  # a `requires` whose requirements have not opened

    def ends(self, token: str) -> bool:
        if self.depth == 0 and token in _CLAUSE_ENDS and not self.expression:
            return True
        self._read(token)
        return False

    def _read(self, token: str) -> None:
        if self.depth == 0:
            self.expression = token == "requires" or (self.expression and token != "{")
        self.depth += _BRACKET_DEPTH.get(token, 0)


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


class _CClassBodyStates(_LocalClassBody, CFamilyStates):
    """A local class in C or C++."""


class _ObjCClassBodyStates(_LocalClassBody, ObjCFamilyStates):
    """A local class in Objective-C++."""


CFamilyStates.crapkit_class_body = _CClassBodyStates
ObjCFamilyStates.crapkit_class_body = _ObjCClassBodyStates


class CFamilyNestingStates(CLikeNestingStackStates):
    """lizard's namespace and class tracker, with a template head read the way
    `_TemplateBrackets` reads it: the `<` of `bool E = (N < 19)` left lizard's
    tracker inside the template head for the rest of the file."""

    def __init__(self, context):
        super().__init__(context)
        self.crapkit_template = None
        self.crapkit_word = None

    def _template_declaration(self, token):
        if self.crapkit_template is None:
            self.crapkit_template = _TemplateBrackets()
        if self.crapkit_template.closes(token):
            self.crapkit_template = None
            self._state = self._state_global

    def _read_namespace(self, token):
        """A word in a class head waits one token: a `(` after it makes it an
        attribute, `alignas(16)`, `__declspec(dllexport)` or a macro, whose
        arguments are skipped. lizard stopped reading the head at that `(`, and
        the class's members lost the class from their names."""
        if _is_word(token):
            self.crapkit_word = token
            self._state = self._read_head_word
        else:
            super()._read_namespace(token)

    def _read_head_word(self, token):
        if token == "(":
            self.next(self._read_head_attribute, token)
            return
        super()._read_namespace(self.crapkit_word)
        self._state(token)

    @CodeStateMachine.read_inside_brackets_then("()", "_read_namespace")
    def _read_head_attribute(self, _):
        """An attribute's arguments, nested parentheses included."""

    @CodeStateMachine.read_until_then(")({;")
    def _read_namespace_name(self, token, saved):
        """lizard's reading of a head's name, with `_head_name`'s spelling."""
        self._state = self._state_global
        if token == "{":
            self.context.add_namespace(_head_name(saved))


def _head_name(tokens: list[str]) -> str:
    """The class name in a head's tokens: the ones before the first of
    `_NAME_ENDS`, from the last word that follows another word. lizard joined
    them all, so an export macro spelled `Q_CORE_EXPORTQString`; `a::B` keeps
    its qualifier."""
    name = list(itertools.takewhile(lambda token: token not in _NAME_ENDS, tokens))
    start = max((i for i in range(1, len(name))
                 if _is_word(name[i - 1]) and _is_word(name[i])), default=0)
    return "".join(name[start:])


# --- the `&&` that declares a reference --------------------------------------------
#
# `T&& t` and `a && b` are the same three tokens, and lizard's cyclomatic
# counter, its nesting counter and crapkit's cognitive pass all read every `&&`
# as a logical and. lizard refunds one shape in the cyclomatic column (a `&&`
# followed, before any `;{})`, by an `=`), and the cognitive pass frees every
# `&&` before the body's brace; neither reaches `for (auto&& x : r)`,
# `static_cast<Widget&&>(w)` or `[](auto&& x)`, and the nesting column refunded
# nothing. ISO/IEC 14882:2020 [dcl.ref]: the `&&` of a declarator declares a
# reference.
#
# The token pass below respells a `&&` that can only be a declarator as
# DECLARATOR_AND, in the token stream lizard builds, so every counter downstream
# skips it and none of them needs a rule of its own. The reader spells it `&&`
# again for its own states, which build the long name from it. A `&&` is a
# declarator when:
#   * `auto`, `const`, `volatile` or `operator` stands before it;
#   * `>`, `)`, `,`, `...`, `=`, `;` or `{` stands after it, where a logical and
#     would need its right operand;
#   * a name and then `=` follow it: `Widget&& r = make()`, where `a && b = c`
#     assigns to the value of an and;
#   * a name and then `:` follow it inside a `for (...)`: a range-for;
#   * it sits in a `typedef`.
# A `&&` in a parameter list followed by a name, `void take(Widget&& w)`, reads
# like `f(a && b)` and is left alone: the list's closing parenthesis resets the
# nesting it cost (`_forget_the_parameters`), lizard resets the cyclomatic
# column at the body, and the cognitive pass frees every `&&` before the body.
# A lambda taking a named type, `[](Widget&& w)`, is the one spelling missed.

DECLARATOR_AND = "&&(declarator)"
_AND = "&&"

_DECLARES_AFTER = frozenset({"auto", "const", "volatile", "operator"})
_DECLARED_BEFORE = frozenset({">", ")", ",", "...", "=", ";", "{"})


def declarator_ands(tokens):
    """lizard's token stream with each declarator `&&` spelled DECLARATOR_AND."""
    ands = _DeclaratorAnds()
    for token in tokens:
        yield from ands.push(token)
    yield from ands.finish()


def _is_code(token: str) -> bool:
    """Not whitespace, a comment, a preprocessor line or a line continuation."""
    return not token.isspace() and not token.startswith(("//", "/*", "#", "\\"))


class _DeclaratorAnds:
    """Holds each `&&` and the tokens after it until two code tokens decide it."""

    def __init__(self):
        self.prev = None
        self.typedef = False
        self.parens = []  # per open `(`: whether a `for` opened it
        self.held = []

    def push(self, token: str) -> list:
        if self.held:
            self.held.append(token)
            return self._decided(final=False)
        if token == _AND:
            self.held = [token]
            return []
        self._see(token)
        return [token]

    def finish(self) -> list:
        return self._decided(final=True) if self.held else []

    def _decided(self, final: bool) -> list:
        after = [token for token in self.held[1:] if _is_code(token)]
        declares = _declares(self.prev, after, self.typedef, self._in_for())
        if declares is None and not final:
            return []
        return self._release(bool(declares), final)

    def _release(self, declares: bool, final: bool) -> list:
        """The held `&&`, spelled as decided, then the held tokens, read again:
        one of them may be the next `&&`."""
        rest, self.held = self.held[1:], []
        self.prev = _AND
        out = [DECLARATOR_AND if declares else _AND]
        for token in rest:
            out.extend(self.push(token))
        return out + self.finish() if final else out

    def _see(self, token: str) -> None:
        if _is_code(token):
            self._paren(token)
            self.typedef = token == "typedef" or (self.typedef and token != ";")
            self.prev = token

    def _paren(self, token: str) -> None:
        if token == "(":
            self.parens.append(self.prev == "for")
        elif token == ")" and self.parens:
            self.parens.pop()

    def _in_for(self) -> bool:
        return bool(self.parens) and self.parens[-1]


def _declares(prev, after: list, typedef: bool, in_for: bool):
    """True for a declarator `&&`, False for a logical one, None while the
    tokens after it cannot tell yet."""
    if typedef or prev in _DECLARES_AFTER:
        return True
    if not after:
        return None
    if after[0] in _DECLARED_BEFORE:
        return True
    return _declares_a_name(after, in_for)


def _declares_a_name(after: list, in_for: bool):
    """`&& name =` binds a reference, and so does `&& name :` in a range-for."""
    if not _is_word(after[0]):
        return False
    if len(after) < 2:
        return None
    return after[1] == "=" or (after[1] == ":" and in_for)


class _ReferenceTokens:
    """The reader half of the pass: DECLARATOR_AND into lizard's token stream,
    `&&` back out of it for the reader's own states."""

    @staticmethod
    def generate_tokens(source_code, addition="", token_class=None):
        return declarator_ands(_StockCLikeReader.generate_tokens(source_code, addition,
                                                                 token_class))

    def __call__(self, tokens, reader):
        return super().__call__((_AND if token == DECLARATOR_AND else token
                                 for token in tokens), reader)


class CLikeReader(_ReferenceTokens, _StockCLikeReader):
    """lizard's CLikeReader with CFamilyStates in place of CLikeStates.

    lizard's CppRValueRefStates is dropped: the refund it made in the
    cyclomatic column is the token pass's job now, and kept, it would refund a
    `&&` the pass already kept from counting.
    """

    # pylint: disable=too-few-public-methods
    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = (CFamilyStates(context), CFamilyNestingStates(context))


class ObjCReader(_ReferenceTokens, _StockObjCReader):
    """lizard's ObjCReader with ObjCFamilyStates in place of ObjCStates."""

    # pylint: disable=too-few-public-methods
    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [ObjCFamilyStates(context), CFamilyNestingStates(context)]


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
