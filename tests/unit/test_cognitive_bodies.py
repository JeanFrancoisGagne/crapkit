"""Where a structure's body starts and what it nests, with or without braces.

Sonar Cognitive Complexity v1.7 (App. B1-B3) charges an else-if link a flat +1
and raises the nesting level for everything inside an if, an else or a loop.
Zig puts a payload between an `else` and the `if` it continues, `else |err|
if (...)`, and the pass decided at the payload's first `|` that the `else` was
a plain one: the `if` after it paid +1 of its own and the chain cost one more
than the same chain with no payload.

A body written without braces (`if (a) return b ? 1 : 2;`) is a level too,
and the pass held none for it: only a `{` pushed one, so a structure in such a
body paid no nesting, and the structure kept waiting for a `{` past the end of
its statement and took the next block of any kind for its own. In the
languages that allow such a body (C, C++, Objective-C, Java, JavaScript,
TypeScript and Zig) the body now holds a level from its header's `)` to the
end of its statement, and a `{` is a structure's block only where the
structure is still waiting for it at its own bracket depth.
"""
import pytest

from crapkit.analyze import analyze_source

ZIG_ELSE_CHAINS = [  # (label, source, Sonar value)
    ("a payload else before an if with no braces",
     "pub fn f(x: anyerror!u8) u8 {\n    if (x) |v| {\n        return v;\n"
     "    } else |err| if (err != error.Boom) return 1;\n    return 0;\n}\n", 2),
    ("a payload else-if chain that ends in an else",
     "pub fn f(x: anyerror!u8) u8 {\n    if (x) |v| {\n        return v;\n"
     "    } else |err| if (err == error.A) {\n        return 1;\n    } else {\n"
     "        return 2;\n    }\n}\n", 3),
    ("a pointer payload before an if",
     "pub fn f(x: anyerror!u8) u8 {\n    if (x) |*v| {\n        return v.*;\n"
     "    } else |*err| if (err.* == error.A) {\n        return 1;\n    }\n    return 0;\n}\n", 2),
    ("a payload else whose block holds an if (control)",
     "pub fn f(x: anyerror!u8) u8 {\n    if (x) |v| {\n        return v;\n    } else |err| {\n"
     "        if (err == error.Boom) {\n            return 1;\n        }\n    }\n    return 0;\n}\n", 4),
    ("an else-if with no payload (control)",
     "pub fn f(a: bool, b: bool) u8 {\n    if (a) {\n        return 1;\n    } else if (b) {\n"
     "        return 2;\n    }\n    return 0;\n}\n", 2),
]


@pytest.mark.parametrize("label,source,want", ZIG_ELSE_CHAINS, ids=[c[0] for c in ZIG_ELSE_CHAINS])
def test_a_zig_payload_else_continues_its_chain(label, source, want):
    rows = analyze_source("a.zig", source, note=False)
    assert [r.cognitive for r in rows] == [want], label


IF_TERNARY = "int f(int a, int b) {\n    if (a) b = b > 0 ? 1 : 2;\n    return b;\n}\n"
BRACELESS = [  # (label, path, source, Sonar value)
    ("a C if with braces around a ternary (control)", "a.c",
     "int f(int a, int b) {\n    if (a) {\n        b = b > 0 ? 1 : 2;\n    }\n    return b;\n}\n", 3),
    ("a C if around a ternary", "a.c", IF_TERNARY, 3),
    ("a C++ if around a ternary", "a.cpp", IF_TERNARY, 3),
    ("an Objective-C if around a ternary", "a.m", IF_TERNARY, 3),
    ("a C for around an if", "a.c",
     "void f(int n) {\n    for (int i = 0; i < n; ++i)\n        if (i) {\n            g();\n"
     "        }\n}\n", 3),
    ("a C if around a for", "a.c",
     "void f(int a, int n) {\n    if (a)\n        for (int i = 0; i < n; ++i) {\n            g();\n"
     "        }\n}\n", 3),
    ("a C if around an if", "a.c",
     "void f(int a, int b) {\n    if (a)\n        if (b) {\n            g();\n        }\n}\n", 3),
    ("a C while around a ternary", "a.c",
     "int f(int b) {\n    while (b)\n        b = b > 1 ? b - 1 : 0;\n    return b;\n}\n", 3),
    ("a C else around a ternary", "a.c",
     "int f(int a, int b) {\n    if (a) {\n        b = 1;\n    } else\n"
     "        b = b > 0 ? 1 : 2;\n    return b;\n}\n", 4),
    ("a C if and else, neither with braces", "a.c",
     "int f(int a, int b) {\n    if (a) b = 1;\n    else b = b > 0 ? 1 : 2;\n    return b;\n}\n", 4),
    ("a C else after a body in a loop body", "a.c",
     "int f(int n, int a, int b) {\n    for (int i = 0; i < n; ++i)\n        if (a) b = 1;\n"
     "        else b = b > 0 ? 1 : 2;\n    return b;\n}\n", 7),
    ("a C else that belongs to the if around a loop", "a.c",
     "int f(int n, int a, int b) {\n    if (a)\n        for (int i = 0; i < n; ++i) g();\n"
     "    else b = b > 0 ? 1 : 2;\n    return b;\n}\n", 6),
    ("a C body ends at its semicolon", "a.c",
     "void f(int a, int b) {\n    if (a) g();\n    if (b) {\n        g();\n    }\n}\n", 2),
    ("a C block after a body with no braces", "a.c",
     "int f(int a, int b) {\n    if (a) return 0;\n    {\n        if (b) {\n            g();\n"
     "        }\n    }\n    return 1;\n}\n", 2),
    ("a C++ init list in a range-for header", "a.cpp",
     "void f() {\n    for (int x : {1, 2}) {\n        if (x) {\n            g();\n        }\n"
     "    }\n}\n", 3),
    ("a Java if around a ternary", "Case.java",
     "class Case {\n    int f(int a, int b) {\n        if (a > 0) b = b > 0 ? 1 : 2;\n        return b;\n"
     "    }\n}\n", 3),
    ("a Java for around an if", "Case.java",
     "class Case {\n    void f(int n) {\n        for (int i = 0; i < n; ++i)\n"
     "            if (i > 0) {\n                g();\n            }\n    }\n}\n", 3),
    ("a Java lambda block in an if header", "Case.java",
     "class Case {\n    void f(java.util.List<Integer> xs) {\n"
     "        if (xs.stream().anyMatch(x -> { return x > 0; })) {\n"
     "            if (xs.isEmpty()) {\n                g();\n            }\n        }\n    }\n}\n", 3),
    ("a JavaScript for around an if", "a.js",
     "export function visitAll(xs) {\n  for (const x of xs) if (x) visit(x);\n}\n", 3),
    ("a JavaScript body a line break ends", "a.js",
     "function f(a, b) {\n  if (a) g()\n  if (b) {\n    g()\n  }\n}\n", 2),
    ("a JavaScript body that goes on after an operator", "a.js",
     "function f(a, b, c) {\n  if (a) x = b &&\n    (c ? 1 : 2)\n  return x\n}\n", 4),
    ("a JavaScript body that goes on at a question mark", "a.js",
     "function f(a, b) {\n  if (a) x = b\n    ? 1\n    : 2\n  return x\n}\n", 3),
    ("a JavaScript body on the line after its header", "a.js",
     "function f(xs) {\n  for (const x of xs)\n    if (x)\n      visit(x)\n  if (xs) {\n    g()\n  }\n}\n", 4),
    ("a JavaScript else after a braced if in a loop body", "a.js",
     "function f(xs, c) {\n  for (const x of xs) if (x) { a() } else b = c ? 1 : 2\n  return b\n}\n", 7),
    ("a JavaScript try in a body", "a.js",
     "function f(a) {\n  if (a) try { g() } catch (e) { h() }\n  if (a) {\n    g()\n  }\n}\n", 4),
    ("a TypeScript arrow in a body, which lizard hands the call's closing bracket", "a.ts",
     "function f(xs: number[], a: boolean) {\n  if (a) xs = xs.map((v) => (v ? 1 : 2));\n"
     "  if (xs) {\n    g();\n  }\n}\n", 2),
    ("a TypeScript object literal in a body", "a.ts",
     "function f(a: boolean, b: boolean) {\n  if (a) return { k: 1 };\n  if (b) {\n    g();\n  }\n}\n", 2),
    ("a Zig for with a payload around an if", "a.zig",
     "pub fn f(xs: []const u8) usize {\n    var n: usize = 0;\n    for (xs) |x| if (x > 0) {\n"
     "        n += 1;\n    };\n    return n;\n}\n", 3),
    ("a Zig while with a continue expression around an if", "a.zig",
     "pub fn f(n: usize) usize {\n    var i: usize = 0;\n    var k: usize = 0;\n"
     "    while (i < n) : (i += 1) if (i > 2) {\n        k += 1;\n    };\n    return k;\n}\n", 3),
    ("a Zig catch payload around a switch", "a.zig",
     "pub fn f(x: anyerror!u8) u8 {\n    return x catch |e| switch (e) {\n"
     "        error.A => 0,\n        error.B => 1,\n    };\n}\n", 3),
    ("a Zig if expression ends at its else", "a.zig",
     "pub fn f(a: bool, b: bool) u8 {\n    const y: u8 = if (a) 1 else 2;\n    if (b) {\n"
     "        return y;\n    }\n    return 0;\n}\n", 3),
    ("Zig prongs holding if expressions", "a.zig",
     "pub fn f(k: u8, a: bool, b: bool) u8 {\n    return switch (k) {\n"
     "        0 => if (a) 1 else 2,\n        1 => if (b) 3 else 4,\n        2 => 5,\n"
     "        3...255 => 6,\n    };\n}\n", 7),
    ("a Go if, whose body always has braces (control)", "a.go",
     "package p\n\nfunc F(a bool, b int) int {\n\tif a {\n\t\tif b > 0 {\n\t\t\treturn 1\n"
     "\t\t}\n\t}\n\treturn 0\n}\n", 3),
]


@pytest.mark.parametrize("label,path,source,want", BRACELESS, ids=[c[0] for c in BRACELESS])
def test_a_body_without_braces_is_a_level(label, path, source, want):
    rows = analyze_source(path, source, note=False)
    named = [r.cognitive for r in rows if not r.long_name.startswith("(anonymous)")]
    assert named == [want], label
