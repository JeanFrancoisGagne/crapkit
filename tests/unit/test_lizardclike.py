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
