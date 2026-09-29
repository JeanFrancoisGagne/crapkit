"""`nesting` on a Python row is how deep the function goes, not how many blocks it holds.

lizard's ND extension keys on `;` and `{` and, for Python, on the reader's
`loops` set; what it counts for Python is nesting structures, so a flat
seven-`if` function read 7 and a three-deep one read 3, the same number for
opposite shapes (measured on 0.4.15, and on `lizard -Ens`: flat 3 ifs -> 3,
nested 3 -> 6). crapkit's cognitive pass already keeps a per-function stack of
open blocks for the Sonar nesting increment; the deepest that stack gets is the
depth a reader means by "nesting". Spec item 15, decision 13: Python rows read
that depth.

Brace languages kept lizard's column until its bookkeeping proved wrong on
ordinary code: every `}` closes a level though only a keyword opens one, a `;`
closes a level a braceless structure opened, and `&&`, `||`, `case`, `try` and
`def` each open one. Three nested loops read 2, a Go condition with three
operators read 4, and a parameter named `def` read 2. Shell rows had read the
stack already, since ND never closes a block that ends in `fi`, `done` or
`esac`. Every language now reads the cognitive pass's stack.
"""
from pathlib import Path

import pytest

from crapkit.analyze import analyze_source

ROOT = Path(__file__).resolve().parent.parent.parent

FLAT = "def flat(n):\n" + "".join(
    f"    if n == {i}:\n        return {i}\n" for i in range(7)) + "    return -1\n"

DEEP = """def deep(a, b, c):
    if a:
        if b:
            if c:
                return 1
    return 0
"""

CHAIN = """def chain(n):
    if n > 2:
        return "a"
    elif n > 1:
        return "b"
    else:
        return "c"
"""

EXCEPT_IN_IF = """def guarded(path, strict):
    if strict:
        try:
            return open(path).read()
        except OSError:
            return ""
    return None
"""

WITH_IN_IF = """def guarded(path, strict):
    if strict:
        with open(path) as fh:
            if fh.readable():
                return fh.read()
    return None
"""

# lizard closes a nested `def` on the first token of the line that dedents
# past it, so the outer function's own `if` on that line has to be read as the
# outer's: before the fix it was stepped under the inner function, and the
# outer resumed one level short with its next `if` read as an inline one.
NESTED_DEF = """def outer(a):
    def inner(b):
        return b
    if a:
        if a > 1:
            return inner(a)
    return 0
"""

HELPER_THEN_IF = """def outer(a):
    def inner(b):
        return b
    if a:
        return inner(a)
    return 0
"""


# The same seam at module level: the first token of the line that dedents out
# of the last function is the module's, not the function's. Measured on the
# 0.5.0 branch before the fix over crapkit's own tree: six functions ending a
# module read one `cognitive` point too many, and one read a level it did not
# have, from the `if __name__` or the module-level call that followed them.
MAIN_GUARD = """def main(argv=None):
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""

CALLED_AT_IMPORT = """def register():
    if _READERS:
        return
    _READERS.append(1)


register()
"""

# lizard's ND reads 3: `for`, `if`, and the first `&&` of a condition each add
# a level. The cognitive stack reads 2 for the same shape (it charges `&&` flat),
# so a brace row that reads 3 is one that still reads lizard's column.
TS = """export function f(a: number, b: number) {
  for (const x of [a, b]) {
    if (x > 0 && x < 9) { return x; }
  }
  return 0;
}
"""

_LOOP = ("for (int i = 0; i < 3; i++) {\n        if (x) {\n            go();\n        }\n"
         "    }\n")

# file: (source, depth worked by hand from Sonar Cognitive Complexity v1.7 App. B2:
# if, else if, else, a conditional operator, switch, each loop and catch open a
# level; a logical operator, a case label, try, a bare block, @synchronized and
# @autoreleasepool open none). Each comment names what lizard's ND column read.
BRACE_DEPTHS = {
    # ND 2: its hidden-bracket counter took each C-style for for a braceless one,
    # and the first `;` of the header closed the level before the body opened.
    "loops.c": ("int nested_loops(int n) {\n    int t = 0;\n    for (int i = 0; i < n; i++) {\n"
                "        for (int j = 0; j < n; j++) {\n            for (int k = 0; k < n; k++) {\n"
                "                t++;\n            }\n        }\n    }\n    return t;\n}\n", 3),
    # ND 2 for the next five: a `{` before the for, any `{`, sent it down the same path.
    "if.c": ("void f(int x) {\n    if (x) {\n    " + _LOOP + "    }\n}\n", 3),
    "bare.c": ("void f(int x) {\n    {\n    " + _LOOP + "    }\n}\n", 2),
    "pool.m": ("void f(int x) {\n    @autoreleasepool {\n    " + _LOOP + "    }\n}\n", 2),
    "init.c": ("int f(int x) {\n    int xs[2] = {1, 2};\n    " + _LOOP + "    return 0;\n}\n", 2),
    "sync.java": ("class K {\n    void f(int x) {\n        synchronized (this) {\n"
                  "            for (int i = 0; i < 3; i++) {\n                if (x > 0) {\n"
                  "                    go();\n                }\n            }\n        }\n"
                  "    }\n}\n", 2),
    "if.go": ("package p\n\nfunc f(x int) {\n\tif x > 0 {\n\t\tfor i := 0; i < 3; i++ {\n"
              "\t\t\tif x > 1 {\n\t\t\t\tgo1()\n\t\t\t}\n\t\t}\n\t}\n}\n", 3),
    # ND 4: each operator of an unparenthesized condition opened a level.
    "logic.go": ("package p\n\nfunc Logic(a, b, c, d bool) int {\n\tif a && b && c || d {\n"
                 "\t\treturn 1\n\t}\n\treturn 0\n}\n", 1),
    # ND 2: the first operator of a parenthesized condition opened a level.
    "logic.c": ("int logic(int a, int b, int c, int d) {\n    if (a && b && c || d) {\n"
                "        return 1;\n    }\n    return 0;\n}\n", 1),
    # ND 3: each case label opened a level the next one did not close.
    "pick.go": ("package p\n\nfunc Pick(k int) int {\n\tswitch k {\n\tcase 1:\n\t\treturn 10\n"
                "\tcase 2:\n\t\treturn 20\n\tdefault:\n\t\treturn 0\n\t}\n}\n", 1),
    # ND 1, and right, where each case ends in `return`.
    "pick.c": ("int pick(int k) {\n    switch (k) {\n    case 1:\n        return 10;\n"
               "    case 2:\n        return 20;\n    default:\n        return 0;\n    }\n}\n", 1),
    # ND 2 for the next two, where each of three cases ends in `break;`.
    "cases.c": ("int pick(int k) {\n    int r = 0;\n    switch (k) {\n    case 1:\n        r = 1;\n"
                "        break;\n    case 2:\n        r = 2;\n        break;\n    case 3:\n"
                "        r = 3;\n        break;\n    }\n    return r;\n}\n", 1),
    "cases.ts": ("function pick(k: number) {\n  let r = 0;\n  switch (k) {\n    case 1:\n      r = 1;\n"
                 "      break;\n    case 2:\n      r = 2;\n      break;\n    case 3:\n      r = 3;\n"
                 "      break;\n  }\n  return r;\n}\n", 1),
    # ND 2 for both: ND's token set holds Python's `def`.
    "def.go": ("package p\n\nfunc Default(def int) int {\n\treturn def\n}\n", 0),
    "def.js": ("export function wire(inst, def) {\n  init(inst, def);\n}\n", 0),
    # ND 2 for both: each statement's `&&` opened a level its `;` left open.
    "and-statements.js": ("function f(p, q) {\n  get(p) && write('found');\n"
                          "  get(q) && write('found');\n}\n", 0),
    "and-then-if.js": ("function f(p) {\n  get(p) && write('found');\n  if (p) {\n"
                       "    go();\n  }\n}\n", 1),
    # ND 1: the `;` after the initializer closed the if's level.
    "init.go": ("package p\n\nfunc InitAfterIf(a []int) int {\n\tif len(a) == 0 {\n"
                "\t\treturn 0\n\t}\n\tif n := len(a); n > 1 {\n\t\tfor range a {\n"
                "\t\t\tn--\n\t\t}\n\t}\n\treturn 1\n}\n", 2),
    # ND 1: `g();` closed the second if's level.
    "sibling.c": ("int sibling_ifs(int a, int b, int c) {\n    if (a > 0) {\n        return a;\n"
                  "    }\n    if (b) {\n        g();\n        if (c) {\n            return 1;\n"
                  "        }\n    }\n    return 0;\n}\n", 2),
    # ND 3: the braceless if left its level open over the loop.
    "braceless.c": ("int braceless_then_loop(int a, int n) {\n    if (a) return 0;\n"
                    "    for (int i = 0; i < n; i++) {\n        if (i) return 1;\n    }\n"
                    "    return 2;\n}\n", 2),
    "braceless.zig": ("fn f(a: bool, n: u32) u32 {\n    if (a) return 0;\n    var i: u32 = 0;\n"
                      "    while (i < n) : (i += 1) {\n        if (i == 3) return 1;\n    }\n"
                      "    return 2;\n}\n", 2),
    # ND 2: the first conditional operator left its level open.
    "ternaries.c": ("int two_ternaries(int x, int y) {\n    int a = x ? 1 : 2;\n"
                    "    int b = y ? 3 : 4;\n    return a + b;\n}\n", 1),
    "arm.js": ("function f(c) {\n  return c ? { a: 1 } : { b: 2 };\n}\n", 1),
    "ternary.ps1": ("function F($a) {\n  $b = $a ? 1 : 2\n  return $b\n}\n", 1),
    # A guard with no `;` still has a body one level down (ND 1, and right).
    "guard.js": ("function g(x) {\n  if (!x) return\n  return x.y\n}\n", 1),
    # ND 1: an else opened no level.
    "else.c": ("int f(int a, int b) {\n    if (a) {\n        return 1;\n    } else {\n"
               "        if (b) {\n            return 2;\n        }\n    }\n    return 0;\n}\n", 2),
    "else-braceless.c": ("int f(int a) {\n    if (a) return 1;\n    else return 2;\n}\n", 1),
    # ND 0: ND's token set holds no guard.
    "guard.swift": ("func f(x: Int?) -> Int {\n    guard let v = x else {\n        return 0\n"
                    "    }\n    return v\n}\n", 1),
    # ND 1: `?` opened a level, though Rust and Zig have no conditional operator
    # and TypeScript's `??` is not one.
    "try.rs": ("fn f() -> Result<u8, E> {\n    let v = g()?;\n    Ok(v)\n}\n", 0),
    "optional.zig": ("fn g(x: ?u32) u32 {\n    const v = x orelse 0;\n    return v;\n}\n", 0),
    "coalesce.ts": ("function f(a?: number) {\n  const x = a ?? 0\n  const y: number = 1\n"
                    "  return x + y\n}\n", 0),
    # ND 1: a declarator `&&` opened a level.
    "take.cpp": ("void take(Widget&& w) {\n    use(w);\n}\n", 0),
    # A body without braces is a level for what it holds: a conditional operator
    # inside it sits two down, and so does the braced if that is a for's body.
    "ternary-in-body.c": ("int pick(int a, int b) {\n    if (a) b = b > 0 ? 1 : 2;\n"
                          "    return b;\n}\n", 2),
    "braceless-for.c": ("void walk(int n) {\n    for (int i = 0; i < n; ++i)\n"
                        "        if (i) {\n            show(i);\n        }\n}\n", 2),
    # A `{` on the line after its header is still the body's.
    "allman.c": ("int f(int a, int b)\n{\n    if (a)\n    {\n        if (b)\n        {\n"
                 "            return 1;\n        }\n    }\n    return 0;\n}\n", 2),
    # A conditional operator in another's arm sits a level below it; one after
    # another's statement, or beside it in an argument list, does not.
    "chained.java": ("class T {\n    static int pick(int a, int b) {\n        return a > 0\n"
                     "            ? 1\n            : b > 0\n                ? 2\n"
                     "                : 3;\n    }\n}\n", 2),
    "args.c": ("int g(int a) {\n    return f(a ? 1 : 2, a ? 3 : 4);\n}\n", 1),
    # The `,` of a template argument list sits in the first arm, not after it.
    "template-arm.cpp": ("auto f() -> int {\n  return N <= M ? enc<C, T>()\n"
                         "                : bit | N;\n}\n", 1),
    # With no `;`, a line break ends the statement: the second operator and the
    # second if are siblings of the first.
    "no-semicolons.ts": ("function f(x: boolean, y: boolean) {\n  const a = x ? 1 : 2\n"
                         "  const b = y ? 3 : 4\n  return a + b\n}\n", 1),
    "guard-then-if.js": ("function h(x) {\n  if (!x) return\n  if (x.y) {\n    go()\n  }\n}\n", 1),
    # The `{` of an argument is not a body, and ends with the call.
    "call-body.js": ("function send(err, res) {\n  if (err) return res.json({ error: err });\n"
                     "  return res.end();\n}\n", 1),
    # GNU C's `a ?: b` is `a ? a : b`; outside C, `size?: T` marks an optional
    # property and starts no conditional operator.
    "elvis.m": ("int elvis(int a, int b) {\n    return a ?: b;\n}\n", 1),
    "optional.tsx": ("function Badge({ size }: { size?: \"sm\" | \"lg\" }) {\n"
                     "  return <span>{size}</span>\n}\n", 0),
    # A guard's body ends with its `;`: the block after it is no body of the
    # guard's, and adds no level (ND read 2 for these, and so did the pass).
    "guard-sync.java": ("class K {\n  void f(Object o, boolean a, boolean b) {\n    if (a) return;\n"
                        "    synchronized (o) {\n      if (b) {\n        go();\n      }\n    }\n"
                        "  }\n}\n", 1),
    "guard-pool.m": ("void f(int a, int b) {\n    if (a) return;\n    @autoreleasepool {\n"
                     "        if (b) {\n            go();\n        }\n    }\n}\n", 1),
    # Zig's `else |err| if` links an else-if chain, one level deep.
    "payload-else.zig": ("fn f(x: anyerror!u8, e: anyerror) !void {\n    if (x) |v| {\n"
                         "        use(v);\n    } else |err| if (err != e) return err;\n}\n", 1),
}

# A `{` in a structure's header (a composite literal, an initializer list, an
# array initializer, a lambda, a func literal) is not the structure's body. The
# pass took the header's first `{` for the body, closed the level at its `}`, and
# left the real body at the depth outside the structure: each function below,
# an outer structure holding one more, read nesting 1 and cognitive 2 where it
# is 2 and 3 (Sonar v1.7 App. B: +1, then +1 and 1 for nesting).
_INNER_GO = "\t\tif x > t {\n\t\t\tgo1()\n\t\t}\n\t}\n}\n"
_INNER_C = "        if (x > t) {\n            go();\n        }\n    }\n}\n"
_FOR_C = "        for (;;) {\n            go();\n        }\n    }\n}\n"
HEADER_BRACES = {
    "range.go": "package p\n\nfunc F(t int) {\n\tfor _, x := range []int{1, 2} {\n" + _INNER_GO,
    "map.go": ("package p\n\nfunc F(t int) {\n\tfor _, x := range map[string]int{\"a\": 1} {\n"
               + _INNER_GO),
    # A table-driven test: a struct type's braces, then its literal's, then the body's.
    "table.go": ("package p\n\nfunc F(t int) {\n\tfor _, x := range []struct {\n\t\tn int\n"
                 "\t}{\n\t\t{1},\n\t\t{2},\n\t} {\n\t\tif x.n > t {\n\t\t\tgo1()\n\t\t}\n\t}\n}\n"),
    "index.go": ("package p\n\nfunc F(m map[K]int) {\n\tif v, ok := m[K{1}]; ok {\n"
                 "\t\tfor v > 0 {\n\t\t\tv--\n\t\t}\n\t}\n}\n"),
    "init-list.cpp": "void f(int t) {\n    for (auto x : {1, 2, 3}) {\n" + _INNER_C,
    "brace-init.cpp": ("void f(std::map<K, int> m) {\n    if (m.count(K{1}) > 0) {\n" + _FOR_C),
    "compound.c": "void f(int t) {\n    if (g((struct S){1, 2})) {\n" + _FOR_C,
    "array.java": ("class K {\n  void f(int t) {\n    for (int x : new int[]{1, 2}) {\n"
                   "      if (x > t) {\n        go();\n      }\n    }\n  }\n}\n"),
    "lambda.java": ("class K {\n  void f(java.util.List<Integer> xs) {\n"
                    "    if (xs.stream().anyMatch(x -> { return x > 0; })) {\n      for (;;) {\n"
                    "        go();\n      }\n    }\n  }\n}\n"),
    "object.ts": ("function f(t: number) {\n  for (const [k, v] of Object.entries({a: 1})) {\n"
                  "    if (v > t) {\n      go(k);\n    }\n  }\n}\n"),
    "object.js": "function f() {\n  if (match({a: 1})) {\n    for (;;) {\n      go();\n    }\n  }\n}\n",
    "block.m": ("void f(NSArray *a) {\n    if ([a indexOfObjectPassingTest:^BOOL(id o, NSUInteger i, "
                "BOOL *s) { return o != nil; }] != NSNotFound) {\n" + _FOR_C),
    "closure.swift": ("func f(_ xs: [Int]) {\n    if xs.contains(where: { $0 > 0 }) {\n"
                      "        for x in xs {\n            go(x)\n        }\n    }\n}\n"),
    "closure.rs": ("fn f(xs: Vec<i32>) {\n    if xs.iter().any(|x| { *x > 0 }) {\n"
                   "        for x in xs {\n            go(x);\n        }\n    }\n}\n"),
    "unsafe.rs": ("fn f(it: &mut I) {\n    while let Some(x) = unsafe { it.next() } {\n"
                  "        if x > 0 {\n            go();\n        }\n    }\n}\n"),
    "scriptblock.ps1": ("function F($xs) {\n    if ($xs | Where-Object { $_ -gt 0 }) {\n"
                        "        foreach ($x in $xs) {\n            go $x\n        }\n    }\n}\n"),
    "literal.zig": ("fn f(t: u8) void {\n    for ([_]u8{ 1, 2 }) |x| {\n        if (x > t) {\n"
                    "            go();\n        }\n    }\n}\n"),
}

# A `do` that names something is no loop: Go and Zig have no do-while, and a
# word after a `.` names a member (`obs.do(fn)`). Read as a do-while, each paid
# +1 and reached a level, and in Go it waited for a `{` and took the method's
# body or a literal after the call. ND read nesting 0 for the first five.
# file: (source, (nesting, cognitive))
DO_WORDS = {
    "method.go": ("package p\n\nfunc (kv *KV) do(ctx int) error {\n\terr := kv.get(ctx)\n"
                  "\treturn err\n}\n", (0, 0)),
    "call.go": ("package p\n\nfunc F(n int) {\n\tdo(n)\n\tx := T{a: 1}\n\tuse(x)\n}\n", (0, 0)),
    # ND 1, and right; the `if` paid +2 inside the level the method's body took.
    "method-if.go": ("package p\n\nfunc (kv *KV) do(ctx int) error {\n\tif ctx > 0 {\n"
                     "\t\treturn nil\n\t}\n\treturn nil\n}\n", (1, 1)),
    "fn.zig": ("fn do(x: u8) u8 {\n    const y = x + 1;\n    return y;\n}\n", (0, 0)),
    "call.zig": ("fn f(x: u8) void {\n    do(x);\n    const s = S{ .a = 1 };\n    use(s);\n}\n",
                 (0, 0)),
    "member.js": ("function f(x) {\n  x.do(1);\n  const o = {a: 1};\n  use(o);\n}\n", (0, 0)),
    # ND 1, and right: the `if` is the only level.
    "member-then-if.ts": ("function f(x: X) {\n  x?.do(1)\n  if (x.y) {\n    go()\n  }\n}\n",
                          (1, 1)),
    # A do-while stays one, its `{` on the next line or not.
    "loop.ps1": ("function F($a) {\n    do\n    {\n        go\n    } while ($a)\n}\n", (1, 1)),
    "loop.js": ("function f(a) {\n  do {\n    a--\n  } while (a)\n  return {a: 1}\n}\n", (1, 1)),
}

# No structure keyword is followed by a `:`; an object's key, a type's member and
# a Swift argument label spelled like one are. Each such word paid +1 and opened
# a body that the `,` after it never closed, so a keyword table stacked a level
# per key. Each comment says what 0.8.0 read. file: (source, (nesting, cognitive))
KEYWORD_KEYS = {
    # (4, 6)
    "table.js": ("function f() {\n  const kw = {if: 1, for: 2, while: 3, do: 4, switch: 5, catch: 6};\n"
                 "  return kw;\n}\n", (0, 0)),
    # (2, 4)
    "table.ts": ("function f() {\n  return {\n    if: 1,\n    for: 2,\n    do: 4,\n    switch: 5,\n"
                 "  };\n}\n", (0, 0)),
    # (2, 6)
    "type.ts": ("function f(x: number) {\n  const o: { if: number; for: string } = { if: x, for: \"a\" };\n"
                "  return o;\n}\n", (0, 0)),
    # (2, 4): the arms are one level, and cost the conditional operator's +1.
    "ternary.ts": ("function f(a: boolean) {\n  const o = a ? {if: 1} : {for: 2};\n  return o;\n}\n",
                   (1, 1)),
    # (0, 1)
    "label.swift": ("func f(x: Int) {\n    g(for: x, in: 2)\n    let y = S(do: x)\n    use(y)\n}\n",
                    (0, 0)),
    # A `:` before the keyword is a label's or a case's, and the keyword counts.
    "label-loop.js": ("function f(xs) {\n  outer: for (const x of xs) {\n    if (x) continue outer;\n"
                      "  }\n}\n", (2, 4)),
    "case-if.c": ("int f(int k, int a) {\n    switch (k) {\n    case 1: if (a) { go(); }\n    }\n"
                  "    return 0;\n}\n", (2, 3)),
}

# A `while` right after a `}` is a do-while's tail only where that `}` closed a
# `do`'s block. Read after any `}`, the loop after an if block or a literal paid
# nothing and opened no level, so the if inside it read one level short: nesting
# 1 and cognitive 2 in each brace row below, where the loop is +1 and the if in
# it +2 (ND read nesting 2, and right). file: (source, (nesting, cognitive))
_THEN_WHILE = "while (b) {\n        if (a) {\n            h();\n        }\n    }\n"
LOOP_AFTER_BLOCK = {
    "while.c": ("void f(int a, int b) {\n    if (a) {\n        g();\n    }\n    " + _THEN_WHILE + "}\n",
                (2, 4)),
    "while.java": ("class K {\n  void f(boolean a, boolean b) {\n    if (a) {\n      g();\n    }\n"
                   "    while (b) {\n      if (a) {\n        h();\n      }\n    }\n  }\n}\n", (2, 4)),
    "while.ts": ("function f(a: boolean, b: boolean) {\n  if (a) {\n    g()\n  }\n  while (b) {\n"
                 "    if (a) {\n      h()\n    }\n  }\n}\n", (2, 4)),
    "while.ps1": ("function F($a, $b) {\n    if ($a) {\n        g\n    }\n    while ($b) {\n"
                  "        if ($a) {\n            h\n        }\n    }\n}\n", (2, 4)),
    "while.rs": ("fn f(a: bool, b: bool) {\n    if a {\n        g();\n    }\n    while b {\n"
                 "        if a {\n            h();\n        }\n    }\n}\n", (2, 4)),
    "while.swift": ("func f(a: Bool, b: Bool) {\n    if a {\n        g()\n    }\n    while b {\n"
                    "        if a {\n            h()\n        }\n    }\n}\n", (2, 4)),
    "while.zig": ("fn f(a: bool, b: bool) void {\n    if (a) {\n        g();\n    }\n    " + _THEN_WHILE
                  + "}\n", (2, 4)),
    "literal.js": ("function f(b) {\n  const o = {a: 1}\n  while (b) {\n    if (o.a) {\n      h()\n"
                   "    }\n  }\n}\n", (2, 3)),
    # The pass read (0, 0) in Python, where a dict literal ends in `}` too.
    "table.py": ("def dict_then_while(e):\n    table = {1: 2}\n    while e:\n        e = table.get(e)\n"
                 "    return e\n", (1, 1)),
    # A do-while's `while` stays its tail, on the `}`'s line or the next, and so
    # does an inner one's; a loop after a whole do-while is a loop.
    "do-next-line.c": ("int f(int a) {\n    do {\n        a--;\n    }\n    while (a);\n    return a;\n}\n",
                       (1, 1)),
    "do-in-do.c": ("int f(int a, int b) {\n    do {\n        do {\n            b--;\n        } while (b);\n"
                   "    } while (a--);\n    return a;\n}\n", (2, 3)),
    "do-then-while.c": ("int f(int a, int b) {\n    do {\n        a--;\n    } while (a);\n"
                        "    while (b) {\n        b--;\n    }\n    return a;\n}\n", (1, 2)),
    # Go has no `while`, so the word is a name wherever it stands. 0.8.0 read
    # both rows (3, 5): each use of the word paid as a loop.
    "after-brace.go": ("package p\n\nfunc F(what string) string {\n\tif what == \"\" {\n\t\treturn \"\"\n"
                       "\t}\n\twhile := \" while \" + what\n\tif what != \"x\" {\n\t\twhile = \"y\"\n\t}\n"
                       "\treturn while\n}\n", (1, 2)),
    "first.go": ("package p\n\nfunc F(what string) string {\n\twhile := what\n\tif what != \"x\" {\n"
                 "\t\twhile = \"y\"\n\t}\n\treturn while\n}\n", (1, 1)),
}

# Zig's `else =>` is a switch's default prong, which opens no level, as a `case`
# label opens none; a payload else, `else |err| return err;`, has a body with no
# braces that ends at its `;`. The pass read `else =>` as an else waiting for its
# block (nesting 2, 3 and 3 for the first three), and it read the payload's first
# `|` as the token after the else, so a payload else with no braces waited on and
# the next block in the function took its level (nesting 2 for the next two).
_PAYLOAD = ("fn f(x: anyerror!u8, y: bool) !u8 {\n    if (x) |w| {\n        use(w);\n"
            "    } else |err| return err;\n")
_LABELED_IF = "blk: {\n        if (y) {\n            break :blk 1;\n        }\n        break :blk 2;\n    }"
ZIG_ELSE = {
    "prong.zig": ("fn f(x: u8) void {\n    switch (x) {\n        1 => a(),\n        else => {\n"
                  "            b();\n        },\n    }\n}\n", 1),
    "prong-switch.zig": ("fn f(x: u8, y: u8) void {\n    switch (x) {\n        1 => a(),\n"
                         "        else => switch (y) {\n            1 => b(),\n            else => {\n"
                         "                c();\n            },\n        },\n    }\n}\n", 2),
    "prong-if.zig": ("fn f(x: u8, y: bool) void {\n    switch (x) {\n        1 => a(),\n        else => {\n"
                     "            if (y) {\n                c();\n            }\n        },\n    }\n}\n", 2),
    "payload-labeled.zig": (_PAYLOAD + "    const n = " + _LABELED_IF + ";\n    return n;\n}\n", 1),
    "payload-literal.zig": (_PAYLOAD + "    const s = S{ .a = " + _LABELED_IF + " };\n    return s.a;\n}\n",
                            1),
    # A payload else with braces holds its block one level down.
    "payload-block.zig": ("fn f(x: anyerror!u8, y: bool) !void {\n    if (x) |v| {\n        use(v);\n"
                          "    } else |err| {\n        if (y) {\n            return err;\n        }\n    }\n}\n",
                          2),
}

# Shell and PowerShell rows read ND through their readers' own keyword lists,
# which held the logical operators. Each comment says what ND read.
SCRIPT_DEPTHS = {
    # ND 2: the inner `if` and the loop shared a level.
    "nested.sh": ('f() {\n  if [ "$a" ]; then\n    if [ "$b" ]; then\n      while true; do\n'
                  '        echo x\n      done\n    fi\n  fi\n}\n', 3),
    # ND 2: `&&` and `||` each opened a level.
    "logic.sh": ('f() {\n  [ "$a" ] && [ "$b" ] || echo no\n  echo done\n}\n', 0),
    # ND 2: three sibling one-line `if`s stacked.
    "flat.sh": ('f() {\n  if [ "$a" ]; then echo a; fi\n  if [ "$b" ]; then echo b; fi\n'
                '  if [ "$c" ]; then echo c; fi\n}\n', 1),
    # ND 0: `case ... esac` opened no level.
    "case.sh": ('f() {\n  case "$1" in\n    a) echo a ;;\n    b) echo b ;;\n  esac\n}\n', 1),
    # ND 3: `-and` and `-or` each opened a level.
    "logic.ps1": ('function F($a, $b, $c) {\n    if ($a -and $b -or $c) {\n        go\n'
                  '    }\n}\n', 1),
    # ND 0: the switch opened no level.
    "switch.ps1": ('function F($k) {\n    switch ($k) {\n        1 { go }\n        2 { go }\n'
                   '        default { go }\n    }\n}\n', 1),
    # ND 2: `&&` and `||` between two pipelines each opened a level.
    "chain.ps1": ("function F($p) {\n    Get-Item $p && Write-Output 'found'\n"
                  "    Get-Item $p || Write-Output 'missing'\n}\n", 0),
}

# Zig and Kotlin write `if (a) g() else { ... }` on one line, with no `;` and no
# line break to end the arm, so the `else` ends it. The last two rows put an `if`
# in a Zig return type, whose arms end at the function's `{`: its else took that
# `{` for its block, and they read (2, 4) and (2, 5), as 0.8.0 did, the body's
# `if` paying +2. field.zig puts one in a field's type, which ends at the `=`: the
# value's `if` sat in the type's else and read (2, 5). file: (source, (nesting,
# cognitive))
_IF_B = "        if (b) {\n            h();\n        }\n    }\n}\n"
ELSE_ARMS = {
    "if.zig": ("fn f(a: bool, b: bool) void {\n    if (a) g() else {\n" + _IF_B, (2, 4)),
    "while.zig": ("fn f(a: bool, b: bool) void {\n    while (a) g() else {\n" + _IF_B, (2, 4)),
    "for.zig": ("fn f(xs: []u8, b: bool) void {\n    for (xs) |x| g(x) else {\n" + _IF_B, (2, 4)),
    "payload.zig": ("fn f(a: anytype, b: bool) void {\n    if (a) |v| g(v) else {\n" + _IF_B, (2, 4)),
    "chain.zig": ("fn f(a: bool, b: bool, c: bool) void {\n    if (a) g() else if (b) h() else {\n"
                  "        if (c) {\n            k();\n        }\n    }\n}\n", (2, 5)),
    "labeled.zig": ("fn f(a: bool, b: bool) u8 {\n    return if (a) 1 else blk: {\n        if (b) {\n"
                    "            break :blk 2;\n        }\n        break :blk 3;\n    };\n}\n", (2, 6)),
    "field.zig": ("pub fn F(comptime size: usize) type {\n    return struct {\n        buffer: [size]u8,\n"
                  "        called: if (safety) bool else void =\n            if (safety) false else {},\n"
                  "    };\n}\n", (1, 4)),
    "if.kt": ("fun f(a: Boolean, b: Boolean) {\n    if (a) g() else {\n        if (b) {\n            h()\n"
              "        }\n    }\n}\n", (2, 4)),
    "value.kt": ("fun f(a: Boolean, b: Boolean): Int {\n    val v = if (a) 1 else {\n        if (b) {\n"
                 "            h()\n        }\n        2\n    }\n    return v\n}\n", (2, 4)),
    "return-type.zig": ("fn f(x: anytype) if (A) u8 else u16 {\n    if (x) {\n        return 1;\n    }\n"
                        "    return 0;\n}\n", (1, 3)),
    "return-type-chain.zig": ("fn f(x: anytype) if (A) u8 else if (B) u16 else u32 {\n    if (x) {\n"
                              "        return 1;\n    }\n    return 0;\n}\n", (1, 4)),
}

# The same arm before an else that holds a switch: its prongs sit one level below
# the if. The last row puts an `if` in the function's return type, and read 3.
# file: (source, nesting)
ELSE_SWITCH_DEPTHS = {
    "statement.zig": ("fn f(a: bool, e: E) void {\n    if (a) g() else switch (e) {\n        .x => h(),\n"
                      "        .y => k(),\n    }\n}\n", 2),
    "value.zig": ("fn f(a: bool, e: E) u8 {\n    return if (a) 1 else switch (e) {\n        .x => 0,\n"
                  "        .y => 2,\n    };\n}\n", 2),
    "payload.zig": ("pub inline fn initComptime(comptime s: []const u8) View {\n"
                    "    return comptime if (init(s)) |r| r else |err| switch (err) {\n"
                    "        error.Invalid => {\n            @compileError(\"invalid\");\n        },\n    };\n}\n", 2),
    "return-type.zig": ("pub fn toRadians(ang: anytype) if (@TypeOf(ang) == comptime_int) comptime_float "
                        "else @TypeOf(ang) {\n    switch (@typeInfo(@TypeOf(ang))) {\n"
                        "        .float => return ang * per_deg,\n"
                        "        .vector => |V| if (@typeInfo(V.child) == .float) return ang * per_deg,\n"
                        "        .int => {},\n    }\n    @compileError(\"x\");\n}\n", 2),
}

# In Zig, Kotlin and Scala an `if` is a value that sits in a list, and the `,`
# after it ends its body: a switch prong, an argument, a struct field. Kotlin and
# Scala had no rules for a body without braces, and their rows read nesting 0. C
# and JavaScript keep a body open past a `,`, their comma operator (see
# COMMA_OPERATORS), where the pass ended it and read nesting 1.
# file: (source, (nesting, cognitive))
_PRONGS = "fn f(x: E, a: bool, b: bool) u8 {\n    return switch (x) {\n"
LIST_ARMS = {
    "prongs.zig": (_PRONGS + "        .a => if (a) 1 else 2,\n        .b => if (b) 3 else 4,\n"
                   "        .c => if (a) 5 else 6,\n    };\n}\n", (2, 10)),
    "prongs-bare.zig": ("fn f(x: E, a: bool, b: bool) void {\n    switch (x) {\n        .a => if (a) g(),\n"
                        "        .b => if (b) h(),\n        .c => if (a) k(),\n    }\n}\n", (2, 7)),
    "args.zig": ("fn f(a: bool, b: bool) void {\n    g(.{ if (a) \"x\" else \"\", if (b) \"y\" else \"\" });\n}\n",
                 (1, 4)),
    "fields.zig": ("fn f(a: bool, b: bool) S {\n    return .{ .x = if (a) 1 else 2, .y = if (b) 3 else 4,"
                   " .z = if (a) 5 else 6 };\n}\n", (1, 6)),
    "args.kt": ("fun f(a: Boolean, b: Boolean) {\n    g(if (a) 1 else 2, if (b) 3 else 4, if (a) 5 else 6)\n}\n",
                (1, 6)),
    "args.scala": ("object O {\n  def f(a: Boolean, b: Boolean): Unit = {\n"
                   "    g(if (a) 1 else 2, if (b) 3 else 4, if (a) 5 else 6)\n  }\n}\n", (1, 6)),
}
COMMA_OPERATORS = {
    "comma.c": "void f(int a, int b) {\n    if (a) x++, y = b ? 1 : 2;\n}\n",
    "comma.js": "function f(a, b) {\n  if (a) x++, y = b ? 1 : 2;\n}\n",
}

# A Zig payload after a loop's header comes before the body: its `,` ends
# nothing, and a line break after it does not end the body either.
# file: (source, nesting)
PAYLOAD_DEPTHS = {
    "block.zig": ("fn f(xs: []u8, b: bool) void {\n    for (xs, 0..) |x, i| {\n        if (b) {\n"
                  "            g(x, i);\n        }\n    }\n}\n", 2),
    "bare.zig": ("fn f(xs: []u8, b: bool) void {\n    for (xs, 0..) |x, i| if (b) g(x, i);\n}\n", 2),
    "next-line.zig": ("fn f(xs: []u8, b: bool) void {\n    for (xs) |x|\n        if (b) g(x);\n}\n", 2),
}


# An `if` right after a `:` in Zig is a type, and only there does the `=` after
# it end its arms. In a statement's arm the `=` is an assignment inside the arm,
# a parameter's type ends at its `)` before the body's `=` is read, and C's `:`
# before an `if` is a case label. file: (source, (nesting, cognitive))
TYPE_IFS = {
    "variable.zig": ("fn f(b: bool) void {\n    const x: if (safe) u8 else u16 = if (b) 1 else 2;\n"
                     "    _ = x;\n}\n", (1, 4)),
    "statement.zig": ("fn f(a: bool, b: bool) u8 {\n    var x: u8 = 0;\n"
                      "    if (a) x = 1 else x = if (b) 2 else 3;\n    return x;\n}\n", (2, 5)),
    "parameter.zig": ("fn f(x: if (A) u8 else u16, a: bool, b: bool) void {\n    var y: u8 = 0;\n"
                      "    if (a) y = if (b) 1 else 2;\n    _ = x;\n}\n", (2, 6)),
    "case.c": ("int f(int k, int a, int b) {\n    int x = 0;\n    switch (k) {\n"
               "    case 1: if (a) x = b ? 1 : 2;\n    }\n    return x;\n}\n", (3, 6)),
}


def _nesting(name: str, code: str) -> int:
    (record,) = analyze_source(name, code)
    return record.nesting


def _rows(name: str, code: str) -> dict:
    """Records keyed by lizard's function name (`outer.inner` for a nested def)."""
    return {r.long_name.split("(")[0].strip(): r for r in analyze_source(name, code)}


def test_seven_flat_ifs_are_one_level_deep():
    assert _nesting("flat.py", FLAT) == 1


def test_three_nested_ifs_are_three_levels_deep():
    assert _nesting("deep.py", DEEP) == 3


def test_an_if_elif_else_chain_is_one_level_deep():
    """Each link replaces the last on the stack; none sits inside another."""
    assert _nesting("chain.py", CHAIN) == 1


def test_an_except_inside_an_if_is_two_deep_and_the_try_adds_nothing():
    """Sonar's rule, which the cognitive pass already applies: `try` is free,
    the `except` handler is a block of its own."""
    assert _nesting("guarded.py", EXCEPT_IN_IF) == 2


def test_a_with_block_adds_no_level():
    """`with` is free under Sonar's rules, so an `if` inside a `with` inside an
    `if` is two deep, not three; the agent JSON page says which keywords count."""
    assert _nesting("with.py", WITH_IN_IF) == 2


def test_the_blocks_after_a_nested_def_belong_to_the_outer_function():
    rows = _rows("nested.py", NESTED_DEF)
    assert rows["outer"].nesting == 2
    assert rows["outer.inner"].nesting == 0


def test_a_helper_defined_first_leaves_the_outer_functions_if_at_depth_one():
    rows = _rows("helper.py", HELPER_THEN_IF)
    assert rows["outer"].nesting == 1
    assert rows["outer.inner"].nesting == 0


def test_cognitive_reads_the_same_owner_after_a_nested_def():
    """The depth and the cognitive score come off one stack, so the outer's
    two `if`s pay 1 and 2 to the outer, and the inner pays nothing."""
    rows = _rows("nested.py", NESTED_DEF)
    assert rows["outer"].cognitive == 3
    assert rows["outer.inner"].cognitive == 0


def test_a_module_level_if_after_the_last_function_is_not_the_functions():
    """`if __name__ == "__main__":` opens no level in `main` and costs it nothing."""
    (row,) = analyze_source("main.py", MAIN_GUARD)
    assert (row.nesting, row.cognitive) == (0, 0)


def test_a_module_level_call_of_the_function_just_defined_is_not_recursion():
    (row,) = analyze_source("register.py", CALLED_AT_IMPORT)
    assert (row.nesting, row.cognitive) == (1, 1)


def test_a_logical_operator_opens_no_level_in_a_brace_language():
    assert _nesting("f.ts", TS) == 2


@pytest.mark.parametrize("name", sorted(BRACE_DEPTHS))
def test_a_brace_language_reads_the_depth_of_its_blocks(name):
    source, depth = BRACE_DEPTHS[name]

    assert _nesting(name, source) == depth


# --- shell: blocks close on words, so the depth comes off the cognitive pass ----
#
# lizard's ND closes a level on a `}` or pops a hidden one at a `;`. Shell closes
# `if` with `fi`, a loop with `done` and `case` with `esac`, none of which ND
# reads, so every block leaked a level: seven ifs side by side read 6, four nested
# read 3, and a `case` read 0 because ND's shell set left it out.

SH_FLAT = "flat() {\n" + "".join(
    f'  if [ "$1" = {i} ]; then\n    n={i}\n  fi\n' for i in range(7)) + "}\n"

# `then` on its own line puts no `;` between the blocks for ND to pop at.
SH_FLAT_NO_SEMICOLON = "flat() {\n  if a\n  then\n    n=1\n  fi\n  if b\n  then\n    n=2\n  fi\n}\n"

SH_DEEP = (
    "deep() {\n"
    '  if [ -n "$1" ]; then\n'
    '    if [ -n "$2" ]; then\n'
    '      if [ -n "$3" ]; then\n'
    '        if [ -n "$4" ]; then\n'
    "          return\n"
    "        fi\n      fi\n    fi\n  fi\n}\n")

SH_CASE = 'pick() {\n  case "$1" in\n    a) echo 10 ;;\n    b) echo 20 ;;\n    *) echo 0 ;;\n  esac\n}\n'

SH_IF_IN_CASE = ('pick() {\n  case "$1" in\n    a)\n      if [ -n "$2" ]; then\n        echo 10\n'
                 '      fi\n      ;;\n  esac\n}\n')

SH_LOOPS = ("scan() {\n  while read -r line; do\n    for w in $line; do\n      echo \"$w\"\n"
            "    done\n  done\n  until ok; do\n    sleep 1\n  done\n}\n")

SH_LISTS = "lists() {\n  a && b\n  c || d\n}\n"


def test_seven_shell_ifs_side_by_side_are_one_level_deep():
    assert _nesting("flat.sh", SH_FLAT) == 1


def test_shell_ifs_with_then_on_its_own_line_are_one_level_deep():
    assert _nesting("flat.sh", SH_FLAT_NO_SEMICOLON) == 1


def test_four_nested_shell_ifs_are_four_levels_deep():
    assert _nesting("deep.sh", SH_DEEP) == 4


def test_a_shell_case_opens_one_level_and_its_arms_sit_side_by_side():
    """Sonar's switch: the `case` opens one level, the arms add none."""
    assert _nesting("pick.sh", SH_CASE) == 1


def test_an_if_inside_a_case_arm_is_two_deep():
    assert _nesting("pick.sh", SH_IF_IN_CASE) == 2


def test_a_loop_in_a_loop_is_two_deep_and_a_later_loop_starts_over():
    assert _nesting("scan.sh", SH_LOOPS) == 2


def test_a_shell_and_or_list_opens_no_level():
    """`a && b` is a decision, not a block: ccn counts it, the depth does not."""
    assert _nesting("lists.sh", SH_LISTS) == 0


def test_bash_files_read_the_same_depth_as_sh_files():
    assert _nesting("flat.bash", SH_FLAT) == 1


@pytest.mark.parametrize("name", sorted(HEADER_BRACES))
def test_a_brace_in_a_structures_header_is_not_its_body(name):
    (record,) = analyze_source(name, HEADER_BRACES[name])

    assert (record.nesting, record.cognitive) == (2, 3)


@pytest.mark.parametrize("name", sorted(DO_WORDS))
def test_a_do_that_names_something_is_no_loop(name):
    source, (nesting, cognitive) = DO_WORDS[name]
    (record,) = analyze_source(name, source)

    assert (record.nesting, record.cognitive) == (nesting, cognitive)


@pytest.mark.parametrize("name", sorted(KEYWORD_KEYS))
def test_a_keyword_before_a_colon_is_a_key_or_a_label(name):
    source, (nesting, cognitive) = KEYWORD_KEYS[name]
    (record,) = analyze_source(name, source)

    assert (record.nesting, record.cognitive) == (nesting, cognitive)


@pytest.mark.parametrize("name", sorted(LOOP_AFTER_BLOCK))
def test_a_while_after_a_block_is_a_do_while_tail_only_after_a_do(name):
    source, (nesting, cognitive) = LOOP_AFTER_BLOCK[name]
    (record,) = analyze_source(name, source)

    assert (record.nesting, record.cognitive) == (nesting, cognitive)


@pytest.mark.parametrize("name", sorted(ZIG_ELSE))
def test_a_zig_default_prong_opens_no_level_and_a_payload_else_ends_at_its_statement(name):
    source, depth = ZIG_ELSE[name]

    assert _nesting(name, source) == depth


def test_the_block_a_leaked_zig_else_took_charges_no_nesting_to_the_if_in_it():
    """The same leak cost cognitive too: the `if` inside the block that took the
    payload else's level paid +2, one more than beside the same else in braces."""
    source = ZIG_ELSE["payload-labeled.zig"][0]
    braced = source.replace("else |err| return err;", "else |err| {\n        return err;\n    }")
    (bare,) = analyze_source("payload.zig", source)
    (block,) = analyze_source("braced.zig", braced)

    assert bare.cognitive == block.cognitive


def test_a_zig_payload_else_before_an_if_is_one_else_if_link():
    """`else |err| if (...)` links an else-if chain: the else pays +1 and the
    if opens its body without a +1 of its own (Sonar v1.7 App. B1), so the
    function reads cognitive 2 like `} else if (...)`. The if after the payload
    used to pay +1 as well."""
    source = ("pub fn payloadElse(x: anyerror!u8) u8 {\n    if (x) |v| {\n        return v;\n"
              "    } else |err| if (err != error.Boom) return 1;\n    return 0;\n}\n")
    (record,) = analyze_source("payload-if.zig", source)

    assert (record.nesting, record.cognitive) == (1, 2)


@pytest.mark.parametrize("name", sorted(ELSE_ARMS))
def test_an_else_ends_the_arm_with_no_braces_before_it(name):
    source, (nesting, cognitive) = ELSE_ARMS[name]
    (record,) = analyze_source(name, source)

    assert (record.nesting, record.cognitive) == (nesting, cognitive)


@pytest.mark.parametrize("name", sorted(ELSE_SWITCH_DEPTHS))
def test_a_switch_after_an_else_sits_one_level_below_its_if(name):
    source, depth = ELSE_SWITCH_DEPTHS[name]

    assert _nesting(name, source) == depth


@pytest.mark.parametrize("name", sorted(TYPE_IFS))
def test_only_the_equals_after_a_zig_type_ends_the_if_in_it(name):
    source, (nesting, cognitive) = TYPE_IFS[name]
    (record,) = analyze_source(name, source)

    assert (record.nesting, record.cognitive) == (nesting, cognitive)


@pytest.mark.parametrize("name", sorted(LIST_ARMS))
def test_a_comma_ends_a_body_where_the_language_has_no_comma_operator(name):
    source, (nesting, cognitive) = LIST_ARMS[name]
    (record,) = analyze_source(name, source)

    assert (record.nesting, record.cognitive) == (nesting, cognitive)


@pytest.mark.parametrize("name", sorted(COMMA_OPERATORS))
def test_a_comma_operator_keeps_the_body_open(name):
    """C's and JavaScript's `,` is their comma operator, so the `if` body goes on
    past it and the conditional operator after it sits in that body, two deep."""
    assert _nesting(name, COMMA_OPERATORS[name]) == 2


@pytest.mark.parametrize("name", sorted(PAYLOAD_DEPTHS))
def test_a_zig_payload_comes_before_the_body_and_ends_nothing(name):
    source, depth = PAYLOAD_DEPTHS[name]

    assert _nesting(name, source) == depth


@pytest.mark.parametrize("name", sorted(SCRIPT_DEPTHS))
def test_a_shell_or_powershell_function_reads_the_depth_of_its_blocks(name):
    source, depth = SCRIPT_DEPTHS[name]

    assert _nesting(name, source) == depth


def test_a_powershell_switch_parameter_type_waits_for_no_body():
    """`[switch]$Force` is a parameter type, but the pass reads the word `switch`.
    Its wait for a `{` used to outlast the parameter list, so the function's body
    read as the switch's block and the `if` in it paid +2 (cognitive 3 against 1
    beside a `[bool]` parameter). The wait now ends at the `]` around it: the `if`
    pays +1 and sits one level deep. The word's own +1 is a separate reader
    defect, so the test allows it and no more."""
    body = "    if ($Force) { go }\n}\n"
    (typed,) = analyze_source("switch.ps1", "function F([string]$a, [switch]$Force) {\n" + body)
    (plain,) = analyze_source("bool.ps1", "function F([string]$a, [bool]$Force) {\n" + body)

    assert (typed.nesting, plain.nesting, plain.cognitive) == (1, 1, 1)
    assert typed.cognitive - plain.cognitive <= 1


def test_a_structure_in_another_structures_header_leaves_its_body_waiting():
    """A Rust `match` in an `if` condition takes the first `{` for its own body;
    the `if` still waits for the next one. +1 for the if, +1 for the match in
    its condition, +2 for the for in its body."""
    source = ("fn f(x: u8) {\n    if match x { 1 => true, _ => false } {\n"
              "        for _ in 0..3 {\n            go();\n        }\n    }\n}\n")
    (record,) = analyze_source("match-in-if.rs", source)

    assert (record.nesting, record.cognitive) == (2, 4)


def test_a_match_guard_has_no_body_to_take_the_next_arms_block():
    """A Rust guard's `if` waited for a `{` and took the block of the next arm
    that had one, which read one level deep and charged the `if` inside it 3.
    +1 for the match, +2 for each guard inside it, +2 for the if in the arm."""
    source = ("fn f(x: u8, y: bool) {\n    match x {\n        1 if y => a(),\n"
              "        2 if y => a(),\n        _ => {\n            if y {\n                go();\n"
              "            }\n        }\n    }\n}\n")
    (record,) = analyze_source("guard.rs", source)

    assert (record.nesting, record.cognitive) == (2, 7)


def test_the_agent_json_page_names_where_each_languages_nesting_comes_from():
    """The row a reader of `next-item --json` lands on has to say where the
    number comes from, or 7 and 1 for the same flat function across an upgrade
    reads as a regression; and it has to say what opens a level, or a reader
    who counts blocks calls the number wrong (`with` opens none in Python, a
    `case` label and `&&` none anywhere)."""
    page = (ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8")
    rows = [ln for ln in page.splitlines() if ln.startswith("| `nesting` |")]
    assert len(rows) == 1, f"expected one `nesting` row, found {len(rows)}"
    missing = [word for word in ("cognitive", "every language", "Python", "shell", "`with`",
                                 "`except`", "`esac`", "`case`", "`&&`", "`?`")
               if word not in rows[0]]
    assert missing == [], rows[0]


# (file, source): one `if` whose condition holds `&&` and `||`, or PowerShell's
# `-and` and `-or`.
LOGICAL_EXAMPLES = [
    ("f.ts", "function f(a, b, c) {\n  if (a && b || c) { g(); }\n}\n"),
    ("f.c", "void f(int a, int b, int c) {\n  if (a && b || c) { g(); }\n}\n"),
    ("F.java", "class F { void f(boolean a, boolean b, boolean c) {\n"
     "  if (a && b || c) { g(); }\n} }\n"),
    ("f.ps1", "function f($a, $b, $c) {\n  if ($a -and $b -or $c) { g }\n}\n"),
]


def test_the_nesting_row_says_a_logical_operator_opens_no_level_in_any_language():
    """The same condition reads 1 in TypeScript, C, Java and PowerShell: the `if`
    opens a level and its `&&`, `||`, `-and` and `-or` open none, as the row
    says. lizard's ND column put a `&&` one level below its `if` and gave each
    PowerShell operator a level of its own, so one condition read 2 in
    TypeScript and 3 in PowerShell."""
    page = (ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8")
    (row,) = [ln for ln in page.splitlines() if ln.startswith("| `nesting` |")]
    assert "A logical operator such as `&&`" in row and "open none" in row, row
    for name, code in LOGICAL_EXAMPLES:
        assert _nesting(name, code) == 1, name
