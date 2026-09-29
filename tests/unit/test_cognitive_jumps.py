"""A break or continue costs +1 only when it jumps to a label, spelled the way
its language spells one.

Sonar Cognitive Complexity v1.7 (Jumps to labels) charges `break` and
`continue` only with a label. The pass took any token after the keyword but
`;`, `}` and `)` for a label, so it charged a Rust match arm's `0 => continue,`
and a Zig prong's for the comma, Rust's `break n;` for the value a loop
returns, and in Go and Swift, which end a statement at the line break, a bare
`break` for the `case` on the next line. A label is now what the language
writes: `'outer` in Rust, `:blk` in Zig, a count in shell, and a name on the
same line everywhere else.
"""
import pytest

from crapkit.analyze import analyze_source

CASES = [  # (label, path, source, Sonar value)
    ("a Rust arm's continue", "a.rs",
     "pub fn f(v: Vec<i32>) -> i32 {\n    for r in v {\n        let d = match r {\n"
     "            0 => continue,\n            x => x,\n        };\n        if d > 1 {\n"
     "            return d;\n        }\n    }\n    0\n}\n", 5),
    ("a Rust break that returns a value", "a.rs",
     "fn f(mut n: i32) -> i32 {\n    let x = loop {\n        n -= 1;\n        if n < 0 {\n"
     "            break n;\n        }\n    };\n    x\n}\n", 3),
    ("a Rust labeled break", "a.rs",
     "fn f(v: Vec<i32>) -> i32 {\n    'outer: for r in &v {\n        for s in &v {\n"
     "            if r == s {\n                break 'outer;\n            }\n        }\n    }\n    0\n}\n", 7),
    ("a Zig prong's continue", "a.zig",
     "pub fn f(items: []const u8) usize {\n    var n: usize = 0;\n    for (items) |c| {\n"
     "        switch (c) {\n            0 => continue,\n            1...255 => n += 1,\n        }\n"
     "    }\n    return n;\n}\n", 3),
    ("a Zig labeled break", "a.zig",
     "fn f(a: bool) i32 {\n    const x = blk: {\n        if (a) break :blk 1;\n        break :blk 2;\n"
     "    };\n    return x;\n}\n", 3),
    ("a Go break before the next case", "a.go",
     "package p\n\nfunc F(n int) int {\n\tswitch n {\n\tcase 1:\n\t\tbreak\n\tcase 2:\n"
     "\t\treturn 2\n\t}\n\treturn 0\n}\n", 1),
    ("a Go labeled break", "a.go",
     "package p\n\nfunc F(n []int) int {\nOuter:\n\tfor _, a := range n {\n\t\tfor _, b := range n {\n"
     "\t\t\tif a == b {\n\t\t\t\tbreak Outer\n\t\t\t}\n\t\t}\n\t}\n\treturn 0\n}\n", 7),
    ("a Swift break before the next case", "a.swift",
     "func f(n: Int) -> Int {\n    switch n {\n    case 1:\n        break\n    default:\n"
     "        return 2\n    }\n    return 0\n}\n", 1),
    ("a JavaScript labeled continue", "a.js",
     "function f(a) {\n  outer: for (const x of a) {\n    for (const y of a) {\n"
     "      if (x === y) continue outer;\n    }\n  }\n}\n", 7),
    ("a PowerShell labeled break", "a.ps1",
     "function Get-F($a) {\n    :outer foreach ($x in $a) {\n        foreach ($y in $a) {\n"
     "            if ($x -eq $y) { break outer }\n        }\n    }\n}\n", 7),
    ("a PowerShell break in a switch", "a.ps1",
     "function Get-F($a) {\n    switch ($a) {\n        1 { break }\n        default { 'x' }\n    }\n}\n", 1),
    ("a C break", "a.c",
     "int f(int a) {\n    for (;;) {\n        if (a) {\n            break;\n        }\n    }\n    return a;\n}\n", 3),
    ("a shell break out of two loops", "a.sh",
     "f() {\n  for a in 1 2; do\n    for b in 1 2; do\n      break 2\n    done\n  done\n}\n", 4),
]


@pytest.mark.parametrize("label,path,source,want", CASES, ids=[c[0] for c in CASES])
def test_only_a_jump_to_a_label_costs_one(label, path, source, want):
    (row,) = analyze_source(path, source, note=False)
    assert row.cognitive == want
