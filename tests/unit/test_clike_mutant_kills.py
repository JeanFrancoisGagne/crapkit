"""The C family readers' edges the weekly readers run left unchecked, each worked by
hand from the reader's docstrings and ISO/IEC 14882:2020, several taken from
headers of fmt, cJSON, CPython and AFNetworking.

Every source below compiles as its language, or is a declaration shape the
docstrings name as read on purpose (a stray `;` or `}` inside a declarator's
group). Each row is (long name, start, end, ccn, cognitive, nesting, params); an
`if` body `if (a) { return 1; } return 0;` costs ccn 2, cognitive 1, nesting 1.
"""
import pytest

import lizard
from crapkit import lizardclike
from crapkit.analyze import analyze_source

IF_BODY = "  if (a) {\n    return 1;\n  }\n  return 0;\n"
G = "int g(int a) {\n" + IF_BODY + "}\n"


def _rows(path: str, source: str) -> list[tuple]:
    records = analyze_source(path, source, note=False)
    assert not getattr(records, "reason", ""), records.reason
    return [(r.long_name, r.start, r.end, r.ccn_std, r.cognitive, r.nesting, r.params)
            for r in records]


def _g(start: int) -> tuple:
    return ("g( int a)", start, start + 5, 2, 1, 1, 1)


# --- declarations the attribute reading keeps whole -----------------------------------

CASES = {  # label: (path, source, rows)
    "a declaration macro with a type argument (CPython)": (
        "p.h", "PyAPI_DATA(PyTypeObject) PyEnum_Type;\nPyAPI_DATA(PyTypeObject) PyReversed_Type;\n" + G,
        [_g(3)]),
    "a word after override": (
        "p.cpp", "struct S {\n  int f(int a) override WORD {\n" + IF_BODY + "  }\n};\n" + G,
        [("S::f( int a)", 2, 7, 2, 1, 1, 1), _g(9)]),
    "an export macro with arguments before the return type, after a function (cJSON)": (
        "p.c", G + "CJSON_PUBLIC(void) cJSONUtils_SortObject(cJSON * const object)\n{\n" + IF_BODY
        + "}\n", [_g(1), ("cJSONUtils_SortObject( cJSON * const object)", 7, 13, 2, 1, 1, 1)]),
    "a macro before a function with a type argument, after a template head": (
        "p.cpp", "template <class T>\nEXPORT(int) f(T a) {\n" + IF_BODY + "}\n",
        [("f( T a)", 2, 7, 2, 1, 1, 1)]),
    "a lock annotation after a default reaching into a member": (
        "p.cpp", "int run(int a = cfg.level) LOCKS_EXCLUDED(mu) {\n" + IF_BODY + "}\n" + G,
        [("run( int a = cfg . level)", 1, 6, 2, 1, 1, 1), _g(7)]),
    "a lock annotation after a variadic-only list": (
        "p.c", "int f(...) LOCKS_EXCLUDED(mu) {\n" + IF_BODY + "}\n" + G,
        [("f( ...)", 1, 6, 2, 1, 1, 0), _g(7)]),
    "an export macro before a variadic function": (
        "p.c", "static int EXPORT(x) f(const char *fmt, ...) {\n" + IF_BODY + "}\n" + G,
        [("f( const char * fmt , ...)", 1, 6, 2, 1, 1, 1), _g(7)]),
    "an export macro before a variadic-only function": (
        "p.c", "static int EXPORT(x) f(...) {\n" + IF_BODY + "}\n",
        [("f( ...)", 1, 6, 2, 1, 1, 0)]),
    "an export macro before a function of a qualified type": (
        "p.cpp", "static int EXPORT(x) f(std::string s) {\n" + IF_BODY + "}\n" + G,
        [("f( std :: string s)", 1, 6, 2, 1, 1, 1), _g(7)]),
    "a destructor with a macro whose argument is a literal": (
        "p.cpp", "X::~X() NOEXCEPT(1) {\n" + IF_BODY + "}\n", [("X::~X()", 1, 6, 2, 1, 1, 0)]),
    "a constructor with a member initializer that calls a constructor (fmt)": (
        "p.h", "FMT_ALWAYS_INLINE basic_fstring(const S&) : str_(S()) {\n};\n",
        [("basic_fstring( const S &)", 1, 2, 1, 0, 0, 1)]),
    "a name in parentheses under its return type": (
        "p.c", "static int\n(max)(int a, int b)\n{\n" + IF_BODY + "}\n" + G,
        [("max( int a , int b)", 1, 8, 2, 1, 1, 2), _g(9)]),
    "a trailing return type holding a function pointer": (
        "p.cpp", "auto f(int a) -> int (*)(int) {\n" + IF_BODY + "}\n" + G,
        [("f( int a)", 1, 6, 2, 1, 1, 1), _g(7)]),
    "a default argument written 1.": (
        "p.c", "int f(double a = 1.) {\n" + IF_BODY + "}\n" + G,
        [("f( double a = 1.)", 1, 6, 2, 1, 1, 1), _g(7)]),
    "a constrained out-of-class constructor": (
        "p.cpp", "template <class T> requires (N > 1)\nS<T>::S(T a) {\n" + IF_BODY + "}\n" + G,
        [("S<T>::S( T a)", 2, 7, 2, 1, 1, 1), _g(8)]),
    "a C function named interface in Objective-C": (
        "p.m", "int interface(int a) {\n" + IF_BODY + "}\n" + G,
        [("interface( int a)", 1, 6, 2, 1, 1, 1), _g(7)]),
}


@pytest.mark.parametrize("path, source, rows", CASES.values(), ids=CASES.keys())
def test_each_function_keeps_its_row(path, source, rows):
    assert _rows(path, source) == rows


# --- class heads, templates and local classes ------------------------------------------

HEADS = {
    "an enum class inside a function, its enumerator's ?: charged to it": (
        "p.cpp", "int f(int a) {\n  enum class E { A = N ? 1 : 2 };\n" + IF_BODY + "}\n" + G,
        [("f( int a)", 1, 7, 3, 2, 1, 1), _g(8)]),
    "a local class with a qualified base": (
        "p.cpp", "int outer(int a) {\n  struct L : ns::B {\n    int m(int a) {\n" + IF_BODY
        + "    }\n  };\n  return 0;\n}\n" + G,
        [("outer.L::m( int a)", 3, 8, 2, 1, 1, 1), ("outer( int a)", 1, 11, 1, 0, 0, 1), _g(12)]),
    "a template head whose default argument is a template": (
        "p.cpp", "template <class T, class U = std::vector<int>>\nclass S {\n  int f(int a) {\n"
        + IF_BODY + "  }\n};\n" + G, [("S::f( int a)", 3, 8, 2, 1, 1, 1), _g(10)]),
    "explicit instantiations with trailing return types (fmt)": (
        "p.cc", "template FMT_API auto thousands_sep_impl(locale_ref) -> thousands_sep_result<char>;\n"
        "template FMT_API auto decimal_point_impl(locale_ref) -> wchar_t;\n" + G, [_g(3)]),
    "an alias template holding a comparison (fmt)": (
        "p.h", "template <typename Rep>\nusing unsigned_rep = conditional_t<std::is_integral<Rep>::value "
        "&& sizeof(Rep) < sizeof(int),\n                                  unsigned, typename "
        "make_unsigned_or_unchanged<Rep>::type>;\nusing milliseconds = std::chrono::duration<rep, "
        "std::milli>;\n" + G, [_g(5)]),
    "a stray ; inside a declarator's group": ("p.c", "int x = a * (b;\n" + G, [_g(2)]),
    "a stray } inside a declarator's group": ("p.c", "int x = a * (b }\n" + G, [_g(2)]),
}


@pytest.mark.parametrize("path, source, rows", HEADS.values(), ids=HEADS.keys())
def test_heads_and_templates_leave_each_function_its_row(path, source, rows):
    assert _rows(path, source) == rows


# --- the && of a reference and the function-try-block ----------------------------------

PASSES = {
    "a typedef's && is no and; a return's is": (
        "p.cpp", "typedef int&& R;\nint f(int a) {\n  return a && b;\n}\n" + G,
        [("f( int a)", 2, 4, 2, 1, 0, 1), _g(5)]),
    "a reference declared across a line continuation": (
        "p.cpp", "int f(int a) {\n  Widget&& \\\n    r = make();\n  return a;\n}\n" + G,
        [("f( int a)", 1, 5, 1, 0, 0, 1), _g(6)]),
    "a reference declared across a directive (lizard counts the #if)": (
        "p.cpp", "int f(int a) {\n  Widget&&\n#if X\n    r = make();\n#endif\n  return a;\n}\n" + G,
        [("f( int a)", 1, 7, 2, 0, 0, 1), _g(8)]),
    "an and in a ternary after a range-for": (
        "p.cpp", "int f(int a) {\n  for (int i : v) x += i;\n  return c ? a && b : d;\n}\n" + G,
        [("f( int a)", 1, 4, 4, 3, 1, 1), _g(5)]),
    "a function-try-block with comments before its body and its handler": (
        "p.cpp", "int f(int a) try // note\n{\n" + IF_BODY + "} /* x */ catch (...) {\n}\n" + G,
        [("f( int a)", 1, 8, 3, 2, 1, 1), _g(9)]),
    "a function-try-block whose handler ends the file": (
        "p.cpp", G + "int f(int a) try {\n" + IF_BODY + "} catch (...) {\n}",
        [_g(1), ("f( int a)", 7, 13, 3, 2, 1, 1)]),
}


@pytest.mark.parametrize("path, source, rows", PASSES.values(), ids=PASSES.keys())
def test_the_token_passes_leave_each_function_its_row(path, source, rows):
    assert _rows(path, source) == rows


# --- Objective-C ----------------------------------------------------------------------

F = "int f(int a) {\n" + IF_BODY + "}\n"
OBJC = {
    "a superclass and protocols before instance variables": (
        "@interface A : NSObject <NSCopying> {\n    int _x;\n}\n@end\n" + F,
        [("f( int a)", 5, 10, 2, 1, 1, 1)]),
    "a class extension adopting a protocol": (
        "@interface Adopting () <NSCopying> {\n    int _phase;\n}\n@end\n" + F,
        [("f( int a)", 5, 10, 2, 1, 1, 1)]),
    "a category": ("@interface A (Cat)\n- (int)m:(int)a;\n@end\n" + F, [("f( int a)", 4, 9, 2, 1, 1, 1)]),
    "attributes after a method's selector": (
        "@implementation A\n- (void)m:(int)a NS_REQUIRES_SUPER API_AVAILABLE(ios(10)) {\n" + IF_BODY
        + "}\n@end\n", [("m:( int )", 2, 7, 2, 1, 1, 1)]),
    "an attribute ending a method declaration": (
        "@interface A\n- (void)m:(int)a NS_REQUIRES_SUPER;\n@end\n" + F, [("f( int a)", 4, 9, 2, 1, 1, 1)]),
    "a class method returning a pointer (AFNetworking)": (
        "+ (NSSet *)keyPathsForValuesAffectingValueForKey:(NSString *)key {\n}\n",
        [("keyPathsForValuesAffectingValueForKey:( NSString * )", 1, 2, 1, 0, 0, 1)]),
}


@pytest.mark.parametrize("source, rows", OBJC.values(), ids=OBJC.keys())
def test_objective_c_heads_and_methods_keep_each_row(source, rows):
    assert _rows("p.m", source) == rows


# --- the helpers' documented answers -------------------------------------------------

@pytest.mark.parametrize("tokens, size", [
    (["get", "("], 1), (["S", "::", "get", "("], 3), (["S", "::", "("], 0), (["...", ")"], 0)])
def test_a_qualified_name_ends_on_a_word(tokens, size):
    assert lizardclike._qualified_name_length(tokens) == size


def test_a_group_followed_by_anything_but_lists_and_bounds_declares_nothing():
    """`( * pick ( int k ) ) ( double )` declares pick; with a word after the group,
    the declarator is no function's."""
    group = "* ( * pick ( int k ) )".split()

    assert lizardclike.nested_declarator(group + ["(", "double", ")"]) == (
        ["pick"], ["int", "k"], [])
    assert lizardclike.nested_declarator(group + ["x"]) is None


@pytest.mark.parametrize("name, special", [
    ("S::S", True), ("S<T>::~S", True), ("a::S::S", True), ("a::S::T", False), ("S", False)])
def test_a_special_member_is_named_after_the_class_that_qualifies_it(name, special):
    assert lizardclike.names_a_special_member(name) is special


class _Stranger:
    """A reader class lizard could resolve a file to if its mechanism changed."""


@pytest.mark.parametrize("resolved, named", [
    (None, "no reader"),
    (_Stranger, f"{__name__}._Stranger"),
])
def test_a_rebind_that_does_not_take_names_what_lizard_resolves(monkeypatch, resolved, named):
    monkeypatch.setattr(lizard, "CLikeReader", lizard.CLikeReader)
    monkeypatch.setattr(lizard, "get_reader_for", lambda filename: resolved)

    with pytest.raises(RuntimeError) as refused:
        lizardclike.register()

    assert str(refused.value) == (
        f"crapkit.lizardclike.register() did not take: lizard resolves '.c' to {named}, not "
        "crapkit.lizardclike.CLikeReader. lizard "
        f"{lizard.version} picks readers some other way than lizard_languages.languages(); "
        "rewrite register() against the new mechanism.")


def test_register_makes_lizard_s_fallback_reader_crapkit_s(monkeypatch):
    """A suffix no reader declares falls back to lizard.CLikeReader."""
    monkeypatch.setattr(lizard, "CLikeReader", lizard.CLikeReader)

    lizardclike.register()

    assert lizard.CLikeReader is lizardclike.CLikeReader
