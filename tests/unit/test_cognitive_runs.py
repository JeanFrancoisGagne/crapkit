"""A sequence of like logical operators costs +1 once, however it is laid out.

Sonar Cognitive Complexity v1.7 (Sequences of logical operators) charges one
point per sequence and one each time the operator changes; the paper scores
`if (a && !(b && c))` 3, because the negated group is a sequence of its own.
The pass kept one run per function and reset it at every comma and every line
break. So a sequence continued on the next line counted twice, a comma inside
a call's arguments split the sequence around the call, and a negated group
joined the sequence it sat in.

The run is now kept per bracket: a call's arguments, an index and a negated
group each start their own and hand the outer one back at the closing bracket,
a plain group continues the outer one, and a line break ends a run only
outside brackets and when neither side of it is an operator.

Three spellings that are not sequences at all: `??` and `??=` coalesce a null,
which the paper ignores (Ignore shorthand); `and` and `or` are identifiers
outside Python, Zig and the C family; and a word operator followed by `:` is an
Objective-C selector part (`- (int)join:(int)a and:(int)b`). The GNU `a ?: b`
is a conditional operator with its middle operand omitted, and costs what a
ternary costs.
"""
import pytest

from crapkit.analyze import analyze_source


def _cognitive(path: str, source: str) -> int:
    rows = analyze_source(path, source, note=False)
    assert len(rows) == 1, [r.long_name for r in rows]
    return rows[0].cognitive


CASES = [  # (label, path, source, Sonar value)
    ("negated group, Python", "a.py",
     "def f(a, b, c):\n    if a and not (b and c):\n        return 1\n    return 0\n", 3),
    ("negated group, TypeScript", "a.ts",
     "function f(a: boolean, b: boolean, c: boolean) {\n  if (a && !(b && c)) {\n"
     "    return 1;\n  }\n  return 0;\n}\n", 3),
    ("negated group, PowerShell", "a.ps1",
     "function Test-NotRun($a, $b, $c) {\n    if (-not ($a -and $b) -and $c) {\n"
     "        return 1\n    }\n    return 0\n}\n", 3),
    ("a negated operand stays in the run", "a.py",
     "def f(a, b, c):\n    return a and not b and c\n", 1),
    ("a group continues the run", "a.ts",
     "function f(a: boolean, b: boolean, c: boolean) {\n  return a && (b && c);\n}\n", 1),
    ("a group opens a run the next operator continues", "a.ts",
     "function f(a: boolean, b: boolean, c: boolean) {\n  return (a && b) && c;\n}\n", 1),
    ("a group of the other operator", "a.ts",
     "function f(a: boolean, b: boolean, c: boolean, d: boolean) {\n"
     "  return a && (b || c) && d;\n}\n", 2),
    ("a call's arguments hold their own run", "a.ts",
     "function f(a: boolean, b: boolean, c: boolean) {\n  return g(a && b) && c;\n}\n", 2),
    ("a comma inside a call keeps the run around it", "a.py",
     "def f(a, b, x, y):\n    if a or pick(x, y) or b:\n        return 1\n    return 0\n", 2),
    ("a comma between arguments ends a run", "a.py",
     "def f(a, b, c, d):\n    return pick(a or b, c or d)\n", 2),
    ("case label with calls, Go", "a.go",
     "package p\n\nfunc CaseRun(args []string, s string) int {\n\tfor len(args) > 0 {\n\t\tswitch {\n"
     "\t\tcase strings.HasPrefix(s, \"-\") && !strings.Contains(s, \"=\") && len(s) == 2"
     " && !short(s[1:], flags):\n\t\t\tif len(args) <= 1 {\n\t\t\t\treturn 1\n\t\t\t}\n"
     "\t\t}\n\t}\n\treturn 0\n}\n", 7),
    ("over lines inside brackets, Python", "a.py",
     "def f(a, b, c):\n    return (a or b\n            or c)\n", 1),
    ("over lines after the operator, C", "a.c",
     "int f(int a, int b, int c) {\n    if (a &&\n        b && c) {\n        return 1;\n    }\n"
     "    return 0;\n}\n", 2),
    ("over lines after the operator, Go", "a.go",
     "package p\n\nfunc LongRun(a, b, c bool) int {\n\tif a &&\n\t\tb && c {\n\t\treturn 1\n"
     "\t}\n\treturn 0\n}\n", 2),
    ("over lines before the operator, JavaScript", "a.js",
     "function f(a, b, c) {\n  const ok = a\n    && b\n    && c;\n  return ok;\n}\n", 1),
    ("over lines, PowerShell", "a.ps1",
     "function Test-SplitRun($a, $b, $c) {\n    if ($a -and\n        $b -and $c) {\n"
     "        return 1\n    }\n    return 0\n}\n", 2),
    ("two statements hold two runs", "a.go",
     "package p\n\nfunc Two(a, b, c, d bool) bool {\n\tx := a && b\n\ty := c && d\n"
     "\treturn x || y\n}\n", 3),
    ("two Python statements hold two runs", "a.py",
     "def f(a, b, c, d):\n    x = a and b\n    y = c and d\n    return x, y\n", 2),
]


@pytest.mark.parametrize("label,path,source,want", CASES, ids=[c[0] for c in CASES])
def test_a_sequence_costs_one_whatever_its_layout(label, path, source, want):
    assert _cognitive(path, source) == want


COALESCE = [
    ("a.ts", "function f(a?: number): number {\n  return a ?? 0;\n}\n"),
    ("a.ts", "function f(a?: number): number {\n  a ??= 1;\n  return a;\n}\n"),
    ("a.swift", "func coalesce(a: Int?) -> Int {\n    return a ?? 0\n}\n"),
    ("a.ps1", "function Test-Coalesce($a) {\n    return $a ?? 0\n}\n"),
]


@pytest.mark.parametrize("path,source", COALESCE)
def test_null_coalescing_costs_nothing(path, source):
    assert _cognitive(path, source) == 0


def test_a_ternary_beside_a_coalesce_still_costs_one():
    source = "function f(a?: number, b?: number): number {\n  return a ? b ?? 1 : 2;\n}\n"
    assert _cognitive("a.ts", source) == 1


IDENTIFIERS = [  # `and` and `or` are names outside Python, Zig and the C family
    ("a.go", "package p\n\nfunc F(and, or bool) bool {\n\treturn and == or\n}\n"),
    ("a.js", "function f(and, or) {\n  return and(or);\n}\n"),
    ("s.m", "@implementation S\n\n- (int)join:(int)a and:(int)b {\n    return a + b;\n}\n\n@end\n"),
    ("s.m", "int f(S *s) {\n    return [s join:1 and:2 or:3];\n}\n"),
]


@pytest.mark.parametrize("path,source", IDENTIFIERS)
def test_and_or_as_names_cost_nothing(path, source):
    assert _cognitive(path, source) == 0


def test_word_operators_still_count_where_they_are_operators():
    assert _cognitive("a.zig", "fn f(a: bool, b: bool, c: bool) bool {\n"
                               "    return a and b or c;\n}\n") == 2
    assert _cognitive("a.cpp", "bool f(bool a, bool b) {\n    return a and b;\n}\n") == 1


ELVIS = [  # GCC sec. 6.8: `a ?: b` is `a ? a : b` with `a` evaluated once
    ("a.c", "int f(int a, int b) {\n    return a ?: b;\n}\n", 1),
    ("a.cpp", "int f(int a, int b) {\n    return a ?: b;\n}\n", 1),
    ("a.m", "int f(int a, int b) {\n    return a ?: b;\n}\n", 1),
    ("a.mm", "int f(int a, int b) {\n    return a ?: b;\n}\n", 1),
    ("nested.c", "int f(int a, int b) {\n    if (a) {\n        return a ?: b;\n    }\n"
                 "    return 0;\n}\n", 3),
    ("method.m", "@implementation K\n- (id)pick:(id)x {\n    return x ?: self;\n}\n@end\n", 1),
    ("literal.m", "int f(id x) {\n    id d = @{@\"a\": x ?: @0};\n    id a = @[x ? x : @1];\n"
                  "    return d && a;\n}\n", 3),
]


@pytest.mark.parametrize("path,source,want", ELVIS)
def test_the_gnu_conditional_with_an_omitted_operand_costs_a_ternary(path, source, want):
    assert _cognitive(path, source) == want


def test_an_optional_mark_in_typescript_is_no_conditional():
    source = ("function f(a?: { b?: number }): number | undefined {\n"
              "  return a?.b;\n}\n")
    assert _cognitive("a.ts", source) == 0
