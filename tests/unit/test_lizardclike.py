"""The C family's readers: every parameter a function declares is counted.

lizard names each C-family parameter after the last word of its declaration,
so a declaration that does not end in its name went uncounted: an unnamed
parameter (`int*`), an array (`int a[4]`, `const int (&a)[4]`), a lone
function pointer returning void (`void (*r)()`), and every Objective-C method
argument. The hand counts come from the declarations themselves (ISO/IEC
9899:2018 6.7.6.3, ISO/IEC 14882:2020 [dcl.fct], Apple's Programming with
Objective-C, "Methods Can Take Arguments").
"""
import pytest

import lizard
from crapkit import lizardclike
from crapkit.analyze import analyze_source
from crapkit.lizardclike import declared_parameters

PARAMETERS = [  # (label, path, source, hand count)
    ("two named", "p.cpp", "int f(const int& a, int* b) {\n    return a + *b;\n}\n", 2),
    ("no list", "p.c", "int f() {\n    return 0;\n}\n", 0),
    ("void list", "p.c", "int f(void) {\n    return 0;\n}\n", 0),
    ("variadic tail", "p.c", "int f(const char *fmt, ...) {\n    return 0;\n}\n", 1),
    ("function pointer", "p.c", "int f(int (*cb)(int)) {\n    return cb(1);\n}\n", 1),
    ("void function pointer", "p.c", "int g(void (*r)()) {\n    r();\n    return 0;\n}\n", 1),
    ("unnamed with default", "p.cpp", "int f(int = 0) {\n    return 1;\n}\n", 1),
    ("unnamed pointer and char", "p.cpp", "int f(int*, char) {\n    return 0;\n}\n", 2),
    ("unnamed template reference", "p.cpp",
     "template <typename T>\nbool g(T&, char*) {\n    return false;\n}\n", 2),
    ("four unnamed", "p.cpp", "void v(FILE*, string_view, const args&, bool) {\n}\n", 4),
    ("array", "p.c", "int f(const int arr[4]) {\n    return arr[0];\n}\n", 1),
    ("main", "p.c", "int main(int argc, char *argv[]) {\n    return argc;\n}\n", 2),
    ("reference to array", "p.cpp", "int f(const int (&arr)[4]) {\n    return arr[0];\n}\n", 1),
    ("pointer to array", "p.cpp", "int f(int (*rows)[4]) {\n    return rows[0][0];\n}\n", 1),
    ("template argument list", "p.cpp",
     "int f(std::map<int, char> m, int n) {\n    return n;\n}\n", 2),
    ("braced default", "p.cpp", "int f(Pair p = {1, 2}, int n = 0) {\n    return n;\n}\n", 2),
    ("parameter pack", "p.cpp",
     "template <typename... A>\nint f(int n, A&&... rest) {\n    return n;\n}\n", 2),
    ("C function in Objective-C", "p.m", "static int f(int *, char c) {\n    return c;\n}\n", 2),
    ("method with two arguments", "p.m",
     "@implementation S\n- (int)pairFor:(int)a to:(int)b {\n    return a + b;\n}\n@end\n", 2),
    ("method taking a block", "p.m",
     "@implementation S\n- (void)finish:(void (^)(int))done {\n    done(1);\n}\n@end\n", 1),
    ("method with no argument", "p.m",
     "@implementation S\n- (void)viewDidLoad {\n    [super viewDidLoad];\n}\n@end\n", 0),
]


@pytest.mark.parametrize("path,source,hand", [case[1:] for case in PARAMETERS],
                         ids=[case[0] for case in PARAMETERS])
def test_params_counts_every_declared_parameter(path, source, hand):
    (record,) = analyze_source(path, source, note=False)

    assert record.params == hand


def test_a_less_than_in_a_parenthesized_default_leaves_later_lists_alone():
    """lizard pushes the `<` of `(1 < 2)` on the stack it reads parameter lists
    with and pops it with the `)`, which leaves the stack one deep for the rest
    of the file: every later function read its parameters as nested tokens,
    params 0 and a long name printed without spaces."""
    source = "int f(bool b = (1 < 2)) {\n    return b;\n}\nint g(int a, int c) {\n    return a;\n}\n"

    later = analyze_source("p.cpp", source, note=False)[1]

    assert (later.long_name, later.params) == ("g( int a , int c)", 2)


def test_the_long_name_keeps_lizards_spelling():
    """The long name is the ratchet key, so the count moves and the name does not."""
    (record,) = analyze_source("p.c", "int g(void (*r)()) {\n    r();\n    return 0;\n}\n",
                               note=False)

    assert record.long_name == "g((*r)())"


def test_a_method_keeps_its_selector_for_a_name():
    source = "@implementation S\n- (int)pairFor:(int)a to:(int)b {\n    return a + b;\n}\n@end\n"

    (record,) = analyze_source("p.m", source, note=False)

    assert record.long_name == "pairFor:( int ) to:( int )"


@pytest.mark.parametrize("tokens,hand", [
    ([], 0), (["void"], 0), (["..."], 0), (["int", ",", "..."], 1),
    (["int", "(", "*", ")", "(", "int", ",", "char", ")"], 1),
    (["std", "::", "map", "<", "int", ",", "int", ">", "m"], 1),
    (["bool", "b", "=", "(", "x", "<", "y", ")", ",", "int", "c"], 2),
    (["bool", "b", "=", "x", ">", "y", ",", "int", "c"], 2),
])
def test_declared_parameters_splits_at_top_level_commas(tokens, hand):
    assert declared_parameters(tokens) == hand


@pytest.mark.parametrize("suffix,reader", [
    (".c", lizardclike.CLikeReader), (".cpp", lizardclike.CLikeReader),
    (".h", lizardclike.CLikeReader), (".m", lizardclike.ObjCReader),
    (".mm", lizardclike.ObjCReader)])
def test_every_c_family_suffix_resolves_to_crapkits_reader(suffix, reader):
    assert lizard.get_reader_for(f"probe{suffix}") is reader


def test_a_suffix_no_reader_declares_falls_back_to_crapkits_c_reader():
    """lizard answers an undeclared suffix with its own module's CLikeReader.
    The analysis cache names the reader through `get_reader_for("fallback.c")`,
    so the two have to be the same class."""
    assert lizard.CLikeReader is lizardclike.CLikeReader


def test_register_twice_changes_nothing():
    lizardclike.register()
    lizardclike.register()

    assert lizard.get_reader_for("probe.cpp") is lizardclike.CLikeReader


def test_register_names_both_classes_when_the_rebind_does_not_take(monkeypatch):
    """Both readers are called CLikeReader, so the message has to carry the module."""
    monkeypatch.setattr(lizard, "get_reader_for", lambda _name: None)

    with pytest.raises(RuntimeError, match=r"'\.c' to no reader, not "
                                           r"crapkit\.lizardclike\.CLikeReader"):
        lizardclike.register()


# --- functions lizard hid, merged or misnamed --------------------------------------

BODY = " {\n    if (a) {\n        go();\n    }\n}\n"

HIDDEN_AFTER = {  # a construct, then a function lizard never listed after it
    "comparison in a default template argument": "template <int N, bool E = (N < 19)>\n"
                                                 "struct S {\n  static constexpr int v = 1;\n};\n",
    "comparison in a member initializer": "template <int N>\n"
                                          "struct S {\n  static constexpr bool v = N < 19;\n};\n",
}


@pytest.mark.parametrize("head", HIDDEN_AFTER.values(), ids=HIDDEN_AFTER.keys())
def test_a_less_than_comparison_hides_no_function_after_it(head):
    """lizard read `<` as a template bracket wherever it stood and read on to the
    next `>` in the file, so every function after it had no row: fmt's chrono.h
    kept its rows only for its first thousand lines."""
    rows = analyze_source("p.cpp", head + "int after(int a)" + BODY, note=False)

    assert [(r.long_name, r.start, r.ccn) for r in rows] == [("after( int a)", 5, 2)]


DECLTYPE_BRACES = {
    "member": "struct S {\n    static auto check(int) -> decltype(all(Tag{}));\n"
              "    int value(int a) {\n        return a;\n    }\n};\n",
    "free": "auto check(int) -> decltype(all(Tag{}));\n",
}


@pytest.mark.parametrize("source", DECLTYPE_BRACES.values(), ids=DECLTYPE_BRACES.keys())
def test_a_declaration_whose_return_type_holds_braces_has_no_row(source):
    """`-> decltype(all(Tag{}))` declares check and defines nothing; lizard read
    the braces as its body."""
    names = [r.long_name for r in analyze_source("p.cpp", source, note=False)]

    assert not [name for name in names if "check" in name]


ATTRIBUTED = {  # path: (source, (bare name, start, cognitive)), worked by hand
    "same-line.m": ("@implementation K\n- (void)run:(int)a\n      with:(int)b API_AVAILABLE(ios(10))"
                    + BODY + "@end\n", ("run", 2, 1)),
    "next-line.m": ("@implementation K\n- (void)run:(int)a API_AVAILABLE(ios(10))\n{\n"
                    "    if (a) {\n        go();\n    }\n}\n@end\n", ("run", 2, 1)),
    "gnu.m": ("@implementation K\n- (void)run:(int)a __attribute__((noinline))" + BODY + "@end\n",
              ("run", 2, 1)),
    "swift-name.m": ("@implementation K\n- (void)run:(int)a NS_SWIFT_NAME(run(_:))" + BODY
                     + "@end\n", ("run", 2, 1)),
    "bare-word.m": ("@implementation K\n- (void)run NS_REQUIRES_SUPER {\n    go();\n}\n@end\n",
                    ("run", 2, 0)),
    "c-gnu.c": ("int run(int a) __attribute__((noinline))" + BODY, ("run", 1, 1)),
    "c-gnu-next.c": ("int run(int a)\n    __attribute__((noinline))" + BODY, ("run", 1, 1)),
    "c-avail.c": ("int run(int a) API_AVAILABLE(ios(10))" + BODY, ("run", 1, 1)),
    "cpp-gnu.cpp": ("int run(int a) __attribute__((noinline))" + BODY, ("run", 1, 1)),
    "cpp-override.cpp": ("struct W {\n  int run(int a) const override __attribute__((cold))" + BODY
                         + "};\n", ("run", 2, 1)),
    "c-gnu.m": ("int run(int a) __attribute__((noinline))" + BODY, ("run", 1, 1)),
}


@pytest.mark.parametrize("path", ATTRIBUTED)
def test_an_attribute_before_the_body_leaves_the_name_and_the_start_alone(path):
    """lizard named the row after the attribute (`__attribute__`,
    `API_AVAILABLE`) or, for a method, after the attribute's last `)`, which then
    read as recursion; an attribute on a later line moved the start there."""
    source, hand = ATTRIBUTED[path]

    rows = analyze_source(path, source, note=False)

    assert [(_bare(r.long_name), r.start, r.cognitive) for r in rows] == [hand]


def _bare(long_name: str) -> str:
    """`W::run( int a) const` and `run:( int ) with:( int )` both read `run`."""
    return long_name.split("(")[0].split("::")[-1].split(":")[0].strip()


UNCHANGED = {  # source: the one long name lizard already gave it
    "MACRO(x)\nTEST(a, b)" + BODY: "TEST( a , b)",
    'template <typename T>\nFMT_VISIBILITY("hidden")\nauto parse(T& ctx) -> int {\n  return 0;\n}\n':
        "parse( T & ctx)",
    "template <typename T>\nJSON_HEDLEY_NON_NULL(1)\nvoid grisu2(T* buf) {\n}\n": "grisu2( T * buf)",
    "static __attribute__((unused)) int f(int a)" + BODY: "f( int a)",
    "constexpr decltype(auto) iter_move(const It& it)" + BODY: "iter_move( const It & it)",
    "static decltype(x) f(int a)" + BODY: "f( int a)",
    "const decltype(x)& f(int a)" + BODY: "f( int a)",
    "struct S {\n  friend constexpr decltype(auto) g(const S& s) { return 1; }\n};\n":
        "S::g( const S & s)",
    "static __typeof__(x) f(int a)" + BODY: "f( int a)",
}


@pytest.mark.parametrize("source", UNCHANGED, ids=["macro line", "template head", "hedley macro",
                                                   "prefix attribute", "decltype(auto)",
                                                   "static decltype", "const decltype",
                                                   "friend decltype", "typeof"])
def test_a_word_and_parentheses_before_a_declaration_stay_where_lizard_put_them(source):
    """A word with arguments is an attribute only right after the parameter list
    of a function that has a return type. Before a declaration it keeps lizard's
    reading, which is what keeps these named right. `decltype(auto)` reads like
    a function with a return type, `constexpr`, and a list, `(auto)`, so the
    function's own name after it read as an attribute and the row was named
    `decltype( auto)`; decltype and typeof name no function."""
    (record,) = analyze_source("p.cpp", source, note=False)

    assert record.long_name == UNCHANGED[source]


NEXT = "int g(void) {\n  return 1;\n}\n"

NESTED_DECLARATORS = {  # label: (path, source, rows as (long name, start, params)), by hand
    "returns a function pointer": (
        "p.c", "int (*get(int k))(int) {\n  if (k) {\n    return 0;\n  }\n  return 0;\n}\n" + NEXT,
        [("get( int k)", 1, 1), ("g()", 7, 0)]),
    "signal": ("p.c", "void (*signal(int sig, void (*func)(int)))(int) {\n  return func;\n}\n",
               [("signal( int sig ,(*func)(int))", 1, 2)]),
    "qualified name": ("p.cpp", "int (*S::get(int k))(int) {\n  return 0;\n}\n",
                       [("S::get( int k)", 1, 1)]),
    "member": ("p.cpp", "struct S {\n  int (*get(int k))(int) { return 0; }\n};\n",
               [("S::get( int k)", 2, 1)]),
    "const member": ("p.cpp", "struct S {\n  int (*get(int k) const)(int) { return 0; }\n};\n",
                     [("S::get( int k) const", 2, 1)]),
    "const pointer": ("p.c", "int (*const get(int k))(int) {\n  return 0;\n}\n",
                      [("get( int k)", 1, 1)]),
    "pointer to array": ("p.c", "int (*rows(int n))[4] {\n  return 0;\n}\n",
                         [("rows( int n)", 1, 1)]),
    "reference to array": ("p.cpp", "const int (&row(int n))[4] {\n  return t[n];\n}\n",
                           [("row( int n)", 1, 1)]),
    "two levels": ("p.c", "void (*(*pick(int k))(double))(char) {\n  return 0;\n}\n",
                   [("pick( int k)", 1, 1)]),
    "pointer return type": ("p.c", "char *(*get(void))(void) {\n  return 0;\n}\n" + NEXT,
                            [("get()", 1, 0), ("g()", 4, 0)]),
    "reference return type": ("p.cpp", "Foo& (*get())() {\n  return 0;\n}\n",
                              [("get()", 1, 0)]),
    "block": ("p.m", "static void (^make(int k))(int) {\n  return nil;\n}\n" + NEXT,
              [("make( int k)", 1, 1), ("g()", 4, 0)]),
    "parenthesized name": ("p.cpp", "int (min)(int a, int b) {\n  return a < b ? a : b;\n}\n",
                           [("min( int a , int b)", 1, 2)]),
    "parenthesized member": (
        "p.cpp", "struct L {\n  static constexpr int (max)() noexcept { return 1; }\n};\n",
        [("L::max()", 2, 0)]),
    "name built by a macro": ("p.c", "static int TEMPLATE(push)(int a) {\n  return a;\n}\n",
                              [("push( int a)", 1, 1)]),
}


@pytest.mark.parametrize("path,source,rows", NESTED_DECLARATORS.values(),
                         ids=NESTED_DECLARATORS.keys())
def test_a_nested_declarator_names_the_function_it_declares(path, source, rows):
    """ISO/IEC 9899:2018 6.7.6.3 and [dcl.fct]: in `int (*get(int k))(int)` the
    identifier is get and its parameters are the list beside it; `(int)` belongs
    to the return type. lizard named such a function after its return type, `int(
    * get(int k))( int)`, whose bare name is `int`, and read none at all after a
    return type ending in `*`. A name in parentheses, `(min)`, names the function
    the list after it declares."""
    records = analyze_source(path, source, note=False)

    assert [(r.long_name, r.start, r.params) for r in records] == rows


def test_a_nested_declarator_charges_its_body_to_the_function_it_names():
    """The row is the one the same body gets under `int get(int k)`."""
    nested = "int (*get(int k))(int) {\n  if (k) {\n    return get(k - 1);\n  }\n  return 0;\n}\n"
    plain = "int get(int k) {\n  if (k) {\n    return get(k - 1);\n  }\n  return 0;\n}\n"

    (row,) = analyze_source("p.c", nested, note=False)
    (same,) = analyze_source("p.c", plain, note=False)

    assert row == same


NOT_A_FUNCTION = ("int (*fp)(int);\nint (*tab[4])(int) = {0};\n"
                  "void (*signal(int, void (*)(int)))(int);\nint x = a * (b + c);\n"
                  "int y = a * (b)(c);\nstatic int (max)(int a);\n" + NEXT)


def test_pointers_prototypes_and_products_declare_no_function():
    """A function pointer, an array of them, a prototype and an expression in an
    initializer read as they did: no row, and the function after them keeps its."""
    records = analyze_source("p.c", NOT_A_FUNCTION, note=False)

    assert [(r.long_name, r.start) for r in records] == [("g()", 7)]


CONSTRAINED = "template <class T>\nvoid f(T t) {} {\n  if (t) {}\n}\nint g() { return 1; }\n"
F_AND_G = [("f( T t)", 2, 2, 1, 1), ("g()", 5, 1, 0, 0)]  # (long name, start, ccn, cog, nest)

REQUIRES_CLAUSES = {  # label: (source, rows), by hand from [dcl.decl] and [temp.pre]
    "concept": (CONSTRAINED.replace("{}", "requires C<T>", 1), F_AND_G),
    "conjunction": (CONSTRAINED.replace("{}", "requires A<T> && B<T>", 1), F_AND_G),
    "parenthesized": (CONSTRAINED.replace("{}", "requires (sizeof(T) > 1)", 1), F_AND_G),
    "requires-expression": (CONSTRAINED.replace("{}", "requires requires (T x) { x + 1; }", 1),
                            F_AND_G),
    "leading": ("template <class T> requires C<T>\nvoid f(T t) {\n  if (t) {}\n}\n"
                "int g() { return 1; }\n", F_AND_G),
    "leading requires-expression": (
        "template <class T> requires requires (T x) { x + 1; }\nvoid f(T t) {\n  if (t) {}\n}\n"
        "int g() { return 1; }\n", F_AND_G),
    "const member": ("struct S {\n  void f(int t) const requires C<int> {\n    if (t) {}\n  }\n"
                     "  int g() { return 1; }\n};\n",
                     [("S::f( int t) const", 2, 2, 1, 1), ("S::g()", 5, 1, 0, 0)]),
    "constructor with initializers": (
        "struct S {\n  constexpr S(const S& o) noexcept(N<T>)\n    requires C<T>\n    : x{o.x} {\n"
        "    if (x) {}\n  }\n  int g() { return 1; }\n};\n",
        [("S::S( const S & o)", 2, 2, 1, 1), ("S::g()", 7, 1, 0, 0)]),
    "declaration": ("template <class T>\nvoid f(T t) requires C<T>;\nint g() { return 1; }\n",
                    [("g()", 3, 1, 0, 0)]),
    "deleted": ("struct S {\n  void f() requires C<T> = delete;\n  int g() { return 1; }\n};\n",
                [("S::g()", 3, 1, 0, 0)]),
}


@pytest.mark.parametrize("source,rows", REQUIRES_CLAUSES.values(), ids=REQUIRES_CLAUSES.keys())
def test_a_requires_clause_leaves_the_function_its_row(source, rows):
    """lizard read a trailing `requires` as an old-style C parameter: the function
    had no row, and one was named after its body's first statement, `if( t)`,
    with the if's decision lost. A `&&` in the clause decides nothing."""
    records = analyze_source("p.cpp", source, note=False)

    assert [(r.long_name, r.start, r.ccn_std, r.cognitive, r.nesting) for r in records] == rows


@pytest.mark.parametrize("concept", ["concept C = requires (T a) { a + 1; };",
                                     "concept D = C<T> && requires { typename T::x; };"])
def test_a_requires_expression_is_no_function(concept):
    """A concept's requires-expression read as a function named `requires`,
    whose body was the requirements: MSVC STL's <concepts> had 9 such rows."""
    records = analyze_source("p.cpp", f"template <class T>\n{concept}\n{NEXT}", note=False)

    assert [(r.long_name, r.start) for r in records] == [("g()", 3)]


def test_a_c_function_named_requires_keeps_its_row():
    """`requires` is a keyword in C++20 and an identifier in C."""
    source = "int requires(int a) {\n  if (a) {\n    return 1;\n  }\n  return 0;\n}\n"

    (record,) = analyze_source("p.c", source, note=False)

    assert (record.long_name, record.ccn_std) == ("requires( int a)", 2)


INSTANCE_VARIABLES = {
    "class extension": "@interface Extension () {\n    int _first;\n    int _second;\n}\n@end\n",
    "extension adopting a protocol": "@interface Adopting () <NSCopying> {\n    int _phase;\n"
                                     "    unsigned long long _offset;\n}\n@end\n",
    "implementation": "@implementation Owner {\n    int _count;\n}\n@end\n",
}


@pytest.mark.parametrize("source", INSTANCE_VARIABLES.values(), ids=INSTANCE_VARIABLES.keys())
def test_an_instance_variable_block_is_no_function(source):
    """lizard read `@interface Extension () {` as a function named Extension, and
    `() <NSCopying> {` as one named after its last instance variable."""
    assert analyze_source("p.m", source, note=False) == []


def test_a_c_function_at_the_top_of_an_implementation_keeps_its_row():
    source = ("@implementation Foo\nstatic int helper(int x) {\n    return x;\n}\n"
              "- (void)m {\n    helper(1);\n}\n@end\n")

    rows = analyze_source("p.m", source, note=False)

    assert [(r.long_name, r.start) for r in rows] == [("helper( int x)", 2), ("m", 5)]


def test_a_prototype_in_objective_c_opens_no_function():
    """lizard named a method after whatever followed any parameter list in a `.m`
    file, a prototype's `;` included, and the next brace, here an array
    initializer's, became that method's body."""
    source = "int f(int);\nstatic int table[] = {1, 2};\nint g(int a) {\n    return a;\n}\n"

    rows = analyze_source("p.m", source, note=False)

    assert [r.long_name for r in rows] == ["g( int a)"]


# --- the && of a reference declarator decides nothing ---------------------------------

REFERENCES = [  # (label, source, hand (ccn_std, cognitive, nesting)): NIST SP 500-235
    # sec. 4.1, the Sonar paper v1.7, ISO/IEC 14882:2020 [dcl.ref]
    ("parameter", "void take(Widget&& w) {\n    use(w);\n}\n", (1, 0, 0)),
    ("forwarding parameter", "template <typename T>\nvoid pass(T&& value) {\n    use(value);\n}\n",
     (1, 0, 0)),
    ("local auto&&", "void f() {\n    auto&& w = make();\n    use(w);\n}\n", (1, 0, 0)),
    ("local Widget&&", "void f(Widget w) {\n    Widget&& r = static_cast<Widget&&>(w);\n"
                       "    use(r);\n}\n", (1, 0, 0)),
    ("range-for auto&&", "void f(Range& r) {\n    for (auto&& x : r) {\n        use(x);\n    }\n}\n",
     (2, 1, 1)),
    ("range-for Widget&&", "void f(Range& r) {\n    for (Widget&& x : r) {\n        use(x);\n"
                           "    }\n}\n", (2, 1, 1)),
    ("cast", "void f(Widget w) {\n    sink(static_cast<Widget&&>(w));\n}\n", (1, 0, 0)),
    ("lambda auto&&", "void f() {\n    apply([](auto&& x) { use(x); });\n}\n", (1, 0, 0)),
    ("trailing decltype", "template <typename T>\nauto end_of(T&& r) -> decltype(static_cast<T&&>(r)"
                          ".end()) {\n    return r.end();\n}\n", (1, 0, 0)),
    ("parameter pack", "template <typename... T>\nvoid all(T&&... args) {\n    use(args...);\n}\n",
     (1, 0, 0)),
    ("local typedef", "void f() {\n    typedef Widget&& Ref;\n    use(Ref());\n}\n", (1, 0, 0)),
]


@pytest.mark.parametrize("path", ["p.cpp", "p.mm"])
@pytest.mark.parametrize("source,hand", [case[1:] for case in REFERENCES],
                         ids=[case[0] for case in REFERENCES])
def test_a_reference_declarator_costs_nothing(path, source, hand):
    """lizard read each of these `&&` as a logical and: ccn 2 for a cast or a
    lambda parameter, 3 for a range-for, cognitive 1 for `auto&& w = make()`,
    and nesting 1 for an rvalue parameter. `.mm` is Objective-C++ and reads the
    same."""
    (record,) = analyze_source(path, source, note=False)

    assert (record.ccn_std, record.cognitive, record.nesting) == hand


def test_a_parameter_list_opens_no_nesting_level():
    """A default argument is evaluated where the function is called. lizard's
    cyclomatic column starts over at the body; its nesting column read the `?`
    as a level the function opened."""
    source = "int f(int a = kWide ? 1 : 2) {\n    return a;\n}\n"

    (record,) = analyze_source("p.cpp", source, note=False)

    assert (record.ccn_std, record.nesting) == (1, 0)


LOGICAL_ANDS = [  # (label, source, hand (ccn_std, cognitive))
    ("if", "int f(int a, int b) {\n    if (a && b) {\n        return 1;\n    }\n    return 0;\n}\n",
     (3, 2)),
    ("return", "bool f(bool a, bool b) {\n    return a && b;\n}\n", (2, 1)),
    ("assignment", "void f(bool a, bool b) {\n    bool c = a && b;\n    use(c);\n}\n", (2, 1)),
    ("ternary", "int f(bool c, bool a, bool b) {\n    return c ? a && b : 0;\n}\n", (3, 2)),
    ("assignment inside the condition",
     "int f(int n, int *p) {\n    while (n > 0 && (p = next(p)) != 0) {\n        n--;\n    }\n"
     "    return n;\n}\n", (3, 2)),
    ("C", "int f(int a, int b) {\n    return a && b;\n}\n", (2, 1)),
]


@pytest.mark.parametrize("source,hand", [case[1:] for case in LOGICAL_ANDS],
                         ids=[case[0] for case in LOGICAL_ANDS])
def test_a_logical_and_still_counts(source, hand):
    """The other direction. `while (n > 0 && (p = next(p)) != 0)` is also a
    repair: lizard refunded any `&&` an `=` followed before the next `;{})`,
    and took this one for a reference bound to `p`."""
    (record,) = analyze_source("p.cpp", source, note=False)

    assert (record.ccn_std, record.cognitive) == hand


@pytest.mark.parametrize("source,respelled", [
    ("auto&& x = f();", [True]),
    ("a && b", [False]),
    ("static_cast<T&&>(v)", [True]),
    ("Widget&& r = w;", [True]),
    ("x = a && b == c;", [False]),
    ("for (auto&& x : r)", [True]),
    ("for (W&& x : r)", [True]),
    ("c ? a && b : d", [False]),
    ("typedef T&& R;", [True]),
    ("a && b && c", [False, False]),
    ("T&&... args", [True]),
    ("a &&", [False]),
    ("a && // why\n  b", [False]),
])
def test_declarator_ands_respells_only_a_declarator(source, respelled):
    tokens = _passed(source)

    assert [token == lizardclike.DECLARATOR_AND for token in _ands(tokens)] == respelled
    assert "".join(tokens).replace(lizardclike.DECLARATOR_AND, "&&") == source


def _passed(source: str) -> list[str]:
    from lizard_languages.clike import CLikeReader as StockReader

    return list(lizardclike.declarator_ands(StockReader.generate_tokens(source)))


def _ands(tokens: list[str]) -> list[str]:
    return [token for token in tokens if token in ("&&", lizardclike.DECLARATOR_AND)]


def test_the_long_name_spells_the_reference_as_written():
    (record,) = analyze_source("p.cpp", "void take(Widget&& w, T&&... rest) {\n}\n", note=False)

    assert record.long_name == "take( Widget && w , T && ... rest)"


# --- a class defined inside a function ------------------------------------------------

LOCAL_CLASS = ("int outer(int a) {\n    struct Local {\n        int twice(int x) {\n"
               "            if (x) {\n                return 2 * x;\n            }\n"
               "            return 0;\n        }\n    };\n    return Local().twice(a);\n}\n")


@pytest.mark.parametrize("path", ["p.cpp", "p.mm"])
def test_a_local_class_member_is_a_function_of_its_own(path):
    """ISO/IEC 14882:2020 [class.local]: a member function defined in a local
    class is a function definition. lizard read the whole class as outer's
    body, so twice had no row and its if cost outer 1 ccn and 1 cognitive."""
    rows = analyze_source(path, LOCAL_CLASS, note=False)

    assert [(r.long_name, r.start, r.end, r.ccn_std, r.cognitive) for r in rows] == [
        ("outer.Local::twice( int x)", 3, 8, 2, 1), ("outer( int a)", 1, 11, 1, 0)]


def test_every_member_of_a_local_class_has_a_row_and_the_rest_stays_with_the_function():
    """A constructor, an access label, a data member between two members and a
    class nested in the local one: the members get rows, and the if around the
    class and the code after it stay outer's."""
    source = ("int outer(int a) {\n    if (a) {\n        class Local final : public Base {\n"
              "          public:\n            Local(int v) : v_(v) {}\n            int v_ = 0;\n"
              "            int twice() const { return v_ ? 2 * v_ : 0; }\n"
              "            struct In { int one() { return 1; } } in;\n        };\n"
              "        return Local(a).twice();\n    }\n    return a > 0 ? 1 : 0;\n}\n"
              "int after(int b) {\n    return b;\n}\n")

    rows = analyze_source("p.cpp", source, note=False)

    assert [(r.long_name.split("(")[0], r.ccn_std) for r in rows] == [
        ("outer.Local::Local", 1), ("outer.Local::twice", 2), ("outer.Local::In::one", 1),
        ("outer", 3), ("after", 1)]


def test_a_member_of_a_class_local_to_a_member_is_a_function_too():
    source = ("int outer() {\n    struct A {\n        int f() {\n            struct B {\n"
              "                int g() { return 1; }\n            };\n"
              "            return B().g();\n        }\n    };\n    return A().f();\n}\n")

    rows = analyze_source("p.cpp", source, note=False)

    assert [r.long_name for r in rows] == ["outer.A::f.B::g()", "outer.A::f()", "outer()"]


LAST_MEMBERS = {  # label: a data member that reads like a call, last in a local class
    "decltype": "decltype(a < 2) c;",
    "macro": "FIELD(int) c;",
    "function pointer": "int (*fp)(int);",
    "array of function pointers": "void (*table[2])(void);",
}


@pytest.mark.parametrize("path", ["p.cpp", "p.mm"])
@pytest.mark.parametrize("member", LAST_MEMBERS.values(), ids=LAST_MEMBERS.keys())
def test_a_local_class_that_ends_on_a_data_member_leaves_the_function_its_row(member, path):
    """`decltype(a < 2) c;` reads like a call and then an old-style C parameter,
    and when the class closed after it, that call was left as the current
    function: the function around the class ended as a row named
    `outer.Res::decltype( a<2)`, and its own row was gone. libstdc++'s
    std::__min_cmp holds the shape."""
    source = ("int outer(int a) {\n    struct Res {\n        int m;\n"
              f"        {member}\n    }};\n    if (a) {{\n        return 1;\n    }}\n"
              "    return 0;\n}\nint after(int b) {\n    return b;\n}\n")

    rows = analyze_source(path, source, note=False)

    assert [(r.long_name, r.start, r.end, r.ccn_std, r.cognitive) for r in rows] == [
        ("outer( int a)", 1, 10, 2, 1), ("after( int b)", 11, 13, 1, 0)]


NOT_A_LOCAL_CLASS = {  # a class keyword in a body that defines no class
    "brace-initialized variable": "struct point p = {1, 2};\n    struct point q {3, 4};",
    "enum class": "enum class E { A = f(1), B };",
    "sizeof": "int n = sizeof(struct foo) + sizeof(union bar);",
    "pointer": "struct foo *p = make();",
    "compound literal": "use((struct foo){ .x = f(1) });",
    "generic lambda": "auto g = []<class T>(T x) { return x; };",
    "friend": "friend class Other;",
}


@pytest.mark.parametrize("statement", NOT_A_LOCAL_CLASS.values(), ids=NOT_A_LOCAL_CLASS.keys())
def test_a_class_keyword_that_defines_no_class_leaves_the_body_whole(statement):
    source = f"int outer(int a) {{\n    {statement}\n    if (a) {{\n        return 1;\n    }}\n" \
             "    return 0;\n}\n"

    rows = analyze_source("p.cpp", source, note=False)

    assert [(r.long_name, r.ccn_std, r.cognitive) for r in rows] == [("outer( int a)", 2, 1)]


def test_a_local_struct_in_c_holds_no_function_and_costs_nothing():
    source = ("int outer(int a) {\n    struct pair { int x; int y; } p = {a, 2};\n"
              "    typedef struct { int z; } Z;\n    if (a) {\n        return p.x;\n    }\n"
              "    return p.y;\n}\n")

    rows = analyze_source("p.c", source, note=False)

    assert [(r.long_name, r.start, r.end, r.ccn_std, r.cognitive, r.nloc) for r in rows] == [
        ("outer( int a)", 1, 8, 2, 1, 8)]


def test_a_local_class_member_is_named_once_after_its_namespace():
    """lizard spelled the levels around the function twice: `ns::ns::outer.L::f`."""
    source = ("namespace ns {\nint outer() {\n    struct L {\n        int f() { return 1; }\n"
              "    };\n    return L().f();\n}\n}\n")

    rows = analyze_source("p.cpp", source, note=False)

    assert [r.long_name for r in rows] == ["ns::outer.L::f()", "ns::outer()"]


ATTRIBUTED_HEADS = ["struct [[maybe_unused]] Local", "struct alignas(16) Local",
                    "struct __attribute__((packed)) Local", "struct __declspec(align(16)) Local"]


@pytest.mark.parametrize("head", ATTRIBUTED_HEADS)
def test_an_attribute_in_a_local_class_head_names_no_class(head):
    """[dcl.attr.grammar]: an attribute between the class key and the name is
    part of the head, and neither it nor its arguments is a second name."""
    source = (f"int outer(int a) {{\n    {head} {{\n        int twice(int x) {{\n"
              "            if (x) {\n                return 2 * x;\n            }\n"
              "            return 0;\n        }\n    };\n    return Local().twice(a);\n}\n")

    rows = analyze_source("p.cpp", source, note=False)

    assert [(r.long_name, r.ccn_std) for r in rows] == [("outer.Local::twice( int x)", 2),
                                                       ("outer( int a)", 1)]


CLASS_HEADS = {  # a class head at file scope: the long name of its member
    "struct alignas(16) Vec {\n  float dot() const { return 1; }\n};\n": "Vec::dot() const",
    "class __declspec(dllexport) Foo {\n public:\n  int f(int a) { return a; }\n};\n":
        "Foo::f( int a)",
    "struct __attribute__((packed)) P {\n  int f() { return 1; }\n};\n": "P::f()",
    'class FMT_SO_VISIBILITY("default") error : public base {\n  int f() { return 1; }\n};\n':
        "error::f()",
    "class Q_CORE_EXPORT QString final : public Base {\n  int size() const { return 0; }\n};\n":
        "QString::size() const",
    "class FOO_API a::B {\n  int f() { return 1; }\n};\n": "a::B::f()",
    "struct S *make(int x) {\n  return 0;\n}\n": "make( int x)",
    "struct S make(int x) {\n  return s;\n}\n": "make( int x)",
    "namespace a::b {\nint f() { return 1; }\n}\n": "a::b::f()",
}


@pytest.mark.parametrize("source", CLASS_HEADS, ids=["alignas", "declspec", "gnu attribute",
                                                     "attribute macro", "export macro",
                                                     "export macro, qualified name",
                                                     "returns a pointer", "returns a struct",
                                                     "nested namespace"])
def test_a_class_is_named_after_its_name_whatever_its_head_holds(source):
    """[class.pre]: the class name is the last name in the head. lizard's class
    tracker stopped at the first `(`, so `alignas(16)` or `__declspec(dllexport)`
    dropped the class from its members' names, and joined every word it read, so
    an export macro spelled `Q_CORE_EXPORTQString::size`."""
    (record,) = analyze_source("p.cpp", source, note=False)

    assert record.long_name == CLASS_HEADS[source]


NAMESPACE_BODY = ("\nint f(int a) {\n  if (a) {\n    return 1;\n  }\n  return 0;\n}\n"
                  "int g(int b) {\n  return b;\n}\n}\n")

NAMESPACE_HEADS = {  # label: (head, what its functions are qualified with), by hand
    "visibility macro": ("namespace std _GLIBCXX_VISIBILITY(default)\n{", "std::"),
    "gnu attribute": ('namespace ns __attribute__((visibility("default"))) {', "ns::"),
    "standard attribute": ("namespace [[deprecated]] ns {", "ns::"),
    "word after the name": ("namespace ns ABI_TAG {", "ns::"),
    "nested, inline": ("namespace a::inline b {", "a::b::"),
    "nested": ("namespace a::b {", "a::b::"),
    "plain": ("namespace ns {", "ns::"),
    "unnamed": ("namespace {", ""),
}


@pytest.mark.parametrize("head,qualifier", NAMESPACE_HEADS.values(), ids=NAMESPACE_HEADS)
def test_a_namespace_head_names_its_functions_and_is_no_function(head, qualifier):
    """[namespace.def]: a namespace's name comes right after `namespace` and its
    attribute-specifiers, qualified for a nested definition (`a::inline b` is
    a::b). A GNU attribute after the name, or a macro that expands to one,
    `_GLIBCXX_VISIBILITY(default)` in every libstdc++ header, read as a function
    whose body was the whole namespace, and a word after the name took its place."""
    rows = analyze_source("p.cpp", head + NAMESPACE_BODY, note=False)

    assert [(r.long_name, r.ccn_std) for r in rows] == [(qualifier + "f( int a)", 2),
                                                       (qualifier + "g( int b)", 1)]


NOT_A_NAMESPACE = {  # source: the rows lizard gives it, as (long name, ccn_std)
    "namespace fs = std::filesystem;\nint f(int a) {\n  return a;\n}\n": [("f( int a)", 1)],
    "using namespace std;\nint f(int a) {\n  return a;\n}\n": [("f( int a)", 1)],
    "int namespace(int a) {\n  if (a) {\n    return 1;\n  }\n  return 0;\n}\n":
        [("namespace( int a)", 2)],
}


@pytest.mark.parametrize("source", NOT_A_NAMESPACE, ids=["alias", "using-directive",
                                                         "C function named namespace"])
def test_namespace_that_opens_no_namespace_reads_as_lizard_reads_it(source):
    """An alias and a using-directive end at their `;`, and in C `namespace` is
    an identifier like any other."""
    path = "p.c" if source.startswith("int namespace") else "p.cpp"

    rows = analyze_source(path, source, note=False)

    assert [(r.long_name, r.ccn_std) for r in rows] == NOT_A_NAMESPACE[source]
