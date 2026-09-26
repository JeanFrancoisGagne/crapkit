"""Each language charges its own control structures, and nothing else.

Sonar Cognitive Complexity v1.7 (B1, B2) charges a loop, a switch and a catch
+1 and the nesting they sit in. The pass read one keyword set for every
language, so a word that is a keyword somewhere was a structure everywhere:
`cmd.do(1)` in Python and `do(n)` in Go read as do-while loops, `goto(url)` in
JavaScript as a jump. And it missed structures that one language spells its own
way: Swift's `repeat` and `guard`, Rust's `loop`, Go's `select`, PowerShell's
`trap` and Python's `match`, while Swift's `do`, which only opens a scope for
`catch`, read as a loop.

Two spellings the old set could not tell apart are separated too. A `while`
right after a `}` was taken for the tail of a do-while whatever the brace
closed, so the loop after an `if` block or a Python dict cost nothing; the
tail is now only the `while` after the block that a `do` (Swift: `repeat`)
opened. And a word after a member access (`p.catch(h)`, `Symbol.for('x')`)
names a member, never a structure.
"""
import pytest

from crapkit.analyze import analyze_source


def _row(path: str, source: str):
    rows = analyze_source(path, source, note=False)
    assert len(rows) == 1, [r.long_name for r in rows]
    return rows[0]


def _cognitive(path: str, source: str) -> int:
    return _row(path, source).cognitive


NAMES = [  # a word that is a keyword in another language, used as a name here
    ("do as a Python method", "a.py", "def f(cmd):\n    cmd.do(1)\n"),
    ("other languages' keywords as Python calls", "a.py",
     "def f(x):\n    goto(x)\n    switch(x)\n    catch(x)\n    foreach(x)\n"),
    ("do as a Go function", "a.go", "package p\n\nfunc DoCall(n int) int {\n\tdo(n)\n\treturn n\n}\n"),
    ("while as a Go function", "a.go", "package p\n\nfunc W(n int) int {\n\twhile(n)\n\treturn n\n}\n"),
    ("switch as a Rust function", "a.rs", "fn f(n: i32) -> i32 {\n    switch(n);\n    n\n}\n"),
    ("goto as a JavaScript function", "a.js", "function f(u) {\n  goto(u);\n}\n"),
    ("except as a TypeScript function", "a.ts", "function f(u: number) {\n  except(u);\n}\n"),
    ("catch as a promise method", "a.js", "function f(p) {\n  return p.then(g).catch(h);\n}\n"),
    ("for as a member", "a.js", "function f() {\n  return Symbol.for('x');\n}\n"),
    ("catch as a Combine operator", "a.swift",
     "func f(p: P) -> Q {\n    return p.catch { _ in Just(0) }\n}\n"),
    ("match as a Python variable", "a.py",
     "def f(s):\n    match = re.match('x', s)\n    match.group(0)\n    return match\n"),
]


@pytest.mark.parametrize("label,path,source", NAMES, ids=[c[0] for c in NAMES])
def test_a_name_spelled_like_a_keyword_is_no_structure(label, path, source):
    """A Python row's nesting comes from this pass too; a brace language's
    nesting column is lizard's own counter and is not pinned here."""
    row = _row(path, source)
    assert row.cognitive == 0
    if path.endswith(".py"):
        assert row.nesting == 0


STRUCTURES = [  # (label, path, source, Sonar value)
    ("Go select is a switch", "a.go",
     "package p\n\nfunc Pick(c chan int) int {\n\tselect {\n\tcase x := <-c:\n\t\treturn x\n"
     "\tdefault:\n\t\treturn 0\n\t}\n}\n", 1),
    ("Swift repeat-while is one loop", "a.swift",
     "func rep(n: Int) -> Int {\n    var i = n\n    repeat {\n        i -= 1\n    } while i > 0\n"
     "    return i\n}\n", 1),
    ("Swift do opens no loop, its catch costs one", "a.swift",
     "func d(r: R) -> Int {\n    do {\n        try r.run()\n    } catch {\n        return 2\n    }\n"
     "    return 0\n}\n", 1),
    ("Swift guard is an if", "a.swift",
     "func g(a: Bool, b: Bool) {\n    if a {\n        guard b else {\n            return\n        }\n"
     "        show(1)\n    }\n}\n", 3),
    ("Swift guard's else body sits one level in", "a.swift",
     "func g(a: Bool, b: Bool) {\n    guard a else {\n        if b {\n            return\n        }\n"
     "        return\n    }\n}\n", 3),
    ("Swift guard's condition is one sequence", "a.swift",
     "func g(a: Bool, b: Bool, c: Bool) {\n    guard (a && b) && c else {\n        return\n    }\n}\n", 2),
    ("Rust loop is a loop", "a.rs",
     "fn f(mut n: i32) -> i32 {\n    loop {\n        n -= 1;\n        if n < 0 {\n            break;\n"
     "        }\n    }\n    n\n}\n", 3),
    ("PowerShell trap is a catch", "a.ps1",
     "function Invoke-T($x) {\n    trap { continue }\n    return $x\n}\n", 1),
    ("PowerShell do-until is one loop", "a.ps1",
     "function Invoke-D($x) {\n    do {\n        $x--\n    } until ($x -lt 0)\n    return $x\n}\n", 1),
    ("PowerShell do-while is one loop", "a.ps1",
     "function Invoke-D($x) {\n    do {\n        $x--\n    } while ($x -gt 0)\n    return $x\n}\n", 1),
    ("C do-while is one loop", "a.c",
     "int f(int a) {\n    do {\n        a--;\n    } while (a);\n    return a;\n}\n", 1),
    ("Java do-while is one loop", "A.java",
     "class A {\n  int f(int a) {\n    do {\n      a--;\n    } while (a > 0);\n    return a;\n  }\n}\n", 1),
    ("goto still costs one in C", "a.c", "int f(int a) {\n    goto done;\ndone:\n    return a;\n}\n", 1),
    ("a keyword after a line that ends in a `.` argument", "a.ps1",
     "function Invoke-B($x) {\n    Push-Location .\n    if ($x) {\n        return 1\n    }\n}\n", 1),
    ("Python match is a switch", "a.py",
     "def f(n):\n    match n:\n        case 1:\n            return 1\n        case _:\n            return 2\n", 1),
    ("Python match nests its cases", "a.py",
     "def f(n):\n    match (n):  # the subject in brackets\n        case 1:\n            if n:\n"
     "                return 1\n    return 0\n", 3),
    ("Python match over lines", "a.py",
     "def f(n, m):\n    match (n,\n           m):\n        case (1, 2):\n            return 1\n", 1),
]


@pytest.mark.parametrize("label,path,source,want", STRUCTURES, ids=[c[0] for c in STRUCTURES])
def test_each_language_charges_its_own_structures(label, path, source, want):
    assert _cognitive(path, source) == want


WHILE_AFTER_BRACE = [  # a loop right after a block that is not a do's
    ("a.c", "int f(int a) {\n    if (a) {\n        a++;\n    }\n    while (a) {\n        a--;\n    }\n"
            "    return a;\n}\n", 2),
    ("A.java", "class A {\n  int f(int a) {\n    if (a > 0) {\n      a++;\n    }\n"
               "    while (a > 0) {\n      a--;\n    }\n    return a;\n  }\n}\n", 2),
    ("a.ts", "function f(a: number) {\n  {\n    a++;\n  }\n  while (a) {\n    a--;\n  }\n  return a;\n}\n", 1),
    ("a.rs", "fn f(mut i: i32) -> i32 {\n    while i < 9 {\n        i += 1;\n    }\n"
             "    while i > 0 {\n        i -= 1;\n    }\n    i\n}\n", 2),
    ("a.zig", "fn f(n: i32) i32 {\n    var i: i32 = 0;\n    while (i < n) {\n        i += 1;\n    }\n"
              "    while (i > 0) {\n        i -= 1;\n    }\n    return i;\n}\n", 2),
    ("a.swift", "func f(n: Int) -> Int {\n    var i = n\n    if i > 0 {\n        i = 1\n    }\n"
                "    while i > 0 {\n        i -= 1\n    }\n    return i\n}\n", 2),
    ("a.py", "def f(e):\n    table = {1: 2}\n    while e:\n        e = table.get(e)\n    return e\n", 1),
]


@pytest.mark.parametrize("path,source,want", WHILE_AFTER_BRACE, ids=[c[0] for c in WHILE_AFTER_BRACE])
def test_a_while_after_any_other_block_is_a_loop(path, source, want):
    assert _cognitive(path, source) == want


def test_a_python_match_level_holds_its_cases():
    """The match is a nesting level for a Python row, whose nesting column this
    pass measures."""
    row = _row("a.py", "def f(n):\n    match n:\n        case 1:\n            return 1\n")
    assert (row.cognitive, row.nesting) == (1, 1)


def test_a_python_line_that_starts_with_match_but_is_a_call_charges_nothing():
    assert _cognitive("a.py", "def f(s):\n    match(s)\n    match (s)\n    return 0\n") == 0
