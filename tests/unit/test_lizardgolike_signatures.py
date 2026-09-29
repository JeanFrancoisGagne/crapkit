"""Go and Zig signatures read to where the language ends them, every row hand-counted.

The Go spec ("Function declarations", "Function literals", "Type switches") and
the Zig language reference ("Functions", "Function Pointers") give each case: a
parameter is a name the list itself declares, never a word inside a type's
brackets; a literal's result type may be a struct with braces of its own; a
type declaration and a type switch's guard declare no function; and a Zig
`fn` followed by `(` is a function type, never a function.

Each row is (long name, start line, end line, ccn, parameter count). The long
name is the ratchet key a mark was recorded under, so it is lizard's spelling.
"""
import lizard
import pytest

from crapkit import lizardgolike
from crapkit.analyze import analyze_source

CASES = {
    # three parameters, whatever brackets their types hold
    "params.go": ("package p\n\nfunc f(m map[string]int, xs []int, fn func(int, string) error) int {\n"
                  "\tif m == nil {\n\t\treturn 0\n\t}\n\treturn 1\n}\n",
                  [("f m map [ string ] int , xs [ ] int , fn func int , string error", 3, 8, 2, 3)]),
    # a package-level literal's parameters are counted from its group
    "literal.go": ("package p\n\nvar f = func(a, b int, m map[string]int) {\n\tif a > b {\n"
                   "\t\treturn\n\t}\n}\n", [("(a,b int,m map[string]int)", 3, 7, 2, 3)]),
    # a literal whose result type is a struct: the body is the brace after it
    "structret.go": ("package p\n\nvar build = func(n int) struct{ a int } {\n\tif n > 0 {\n"
                     "\t\treturn struct{ a int }{n}\n\t}\n\treturn struct{ a int }{0}\n}\n",
                     [("(n int)", 3, 8, 2, 1)]),
    # a type declaration before a function declares no function
    "typefirst.go": ("package p\n\ntype P struct{ x int }\n\nfunc f(a int) int {\n\tif a > 0 {\n"
                     "\t\treturn 1\n\t}\n\treturn 0\n}\n", [("f a int", 5, 10, 2, 1)]),
    # a type switch's guard declares nothing: the function keeps its `if` after it
    "switch.go": ("package p\n\nfunc f(v interface{}) int {\n\tswitch v.(type) {\n\tcase int:\n"
                  "\t\treturn 1\n\t}\n\tif v == nil {\n\t\treturn 2\n\t}\n\treturn 0\n}\n",
                  [("f v interface { }", 3, 12, 3, 1)]),
    # a Zig function type inside a function: the function keeps its `if`
    "fntype.zig": ("fn outer(x: u8) u8 {\n    const cb: *const fn (u8) void = &inner;\n"
                   "    _ = cb;\n    if (x > 0) {\n        return 1;\n    }\n    return 0;\n}\n\n"
                   "fn inner(y: u8) void {\n    _ = y;\n}\n",
                   [("outer x : u8", 1, 8, 2, 1), ("inner y : u8", 10, 12, 1, 1)]),
    # an array of function pointers: the `{` after the type opens the array literal
    "array.zig": ("fn outer(x: u8) u8 {\n    const hs = [_]*const fn (u8) void{ &inner, &inner };\n"
                  "    _ = hs;\n    if (x > 0) {\n        return 1;\n    }\n    return 0;\n}\n\n"
                  "fn inner(y: u8) void {\n    _ = y;\n}\n",
                  [("outer x : u8", 1, 8, 2, 1), ("inner y : u8", 10, 12, 1, 1)]),
    # and at the top of the file
    "toptype.zig": ("var hook: ?*const fn (u8) void = null;\n\nfn f(x: u8) u8 {\n    if (x > 0) {\n"
                    "        return 1;\n    }\n    return 0;\n}\n", [("f x : u8", 3, 8, 2, 1)]),
    # a block comment on one line is no line break: the signature goes on to its body
    "comment1.go": ("package p\n\nfunc f(a int) /* one line */ int {\n\tif a > 0 {\n\t\treturn 1\n\t}\n"
                    "\treturn 0\n}\n", [("f a int", 3, 8, 2, 1)]),
    # one spanning lines is: Go puts a semicolon after the function type's `error`
    "comment2.go": ("package p\n\nfunc outer(x int) int {\n\tvar cb func(int) error /* note\n"
                    "\t*/ if x > 0 {\n\t\treturn 1\n\t}\n\t_ = cb\n\treturn 0\n}\n",
                    [("outer x int", 3, 10, 2, 1)]),
    # `union(enum) { ... }` is the result type, its `(enum)` group included
    "union.zig": ("fn f(x: u8) union(enum) { a: u8, b: void } {\n    if (x > 0) {\n"
                  "        return .{ .a = x };\n    }\n    return .{ .b = {} };\n}\n",
                  [("f x : u8", 1, 6, 2, 1)]),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_signature_ends_where_its_language_ends_it(name):
    code, expected = CASES[name]

    found = analyze_source(f"src/{name}", code)

    assert [(r.long_name, r.start, r.end, r.ccn_std, r.params) for r in found] == expected


@pytest.mark.parametrize("resolved, named", [(None, "no reader"), (lizard.CLikeReader, "CLikeReader")])
def test_a_registration_that_did_not_take_names_what_lizard_resolved(monkeypatch, resolved, named):
    monkeypatch.setattr(lizard, "get_reader_for", lambda name: resolved)

    with pytest.raises(RuntimeError) as refused:
        lizardgolike.register()

    assert str(refused.value) == (
        f"crapkit.lizardgolike.register() did not take: lizard resolves 'go' to {named}, not "
        f"CorrectedGoReader. lizard {lizard.version} picks readers some other way than "
        "lizard_languages.languages(); rewrite register() against the new mechanism.")
