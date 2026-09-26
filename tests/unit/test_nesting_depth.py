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
    # ND 2: the case label opened a level.
    "pick.c": ("int pick(int k) {\n    switch (k) {\n    case 1:\n        return 10;\n"
               "    case 2:\n        return 20;\n    default:\n        return 0;\n    }\n}\n", 1),
    # ND 2 for both: ND's token set holds Python's `def`.
    "def.go": ("package p\n\nfunc Default(def int) int {\n\treturn def\n}\n", 0),
    "def.js": ("export function wire(inst, def) {\n  init(inst, def);\n}\n", 0),
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
