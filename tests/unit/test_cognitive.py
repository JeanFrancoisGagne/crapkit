"""Sonar-spec cognitive complexity through the REAL lizard pipeline.

Expected values derive from the Cognitive Complexity whitepaper rules:
+1 (+nesting) for if/ternary/switch/loops/catch-except, +1 flat for
else/elif (an else-if chain counts once per link), +1 per run of a boolean
operator and +1 per alternation, +1 for a labeled break/continue, +1 once
for direct recursion. Nesting rises inside block structures. try/finally
and case labels are free.
"""
from pathlib import Path

from crapkit.analyze import analyze_one


def _cognitive(tmp_path: Path, name: str, source: str) -> dict[str, int]:
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    _, records = analyze_one((str(p), name))
    return {r.long_name.split("(")[0].strip(): r.cognitive for r in records}


def test_simple_if_is_one(tmp_path):
    src = "export function f(a: number) {\n  if (a) { return 1; }\n  return 0;\n}\n"
    assert _cognitive(tmp_path, "a.ts", src)["f"] == 1


def test_sum_of_primes_is_seven(tmp_path):
    src = (
        "function sumOfPrimes(max: number): number {\n"
        "  let total = 0;\n"
        "  OUT: for (let i = 1; i <= max; ++i) {\n"      # +1
        "    for (let j = 2; j < i; ++j) {\n"            # +2
        "      if (i % j === 0) {\n"                     # +3
        "        continue OUT;\n"                        # +1
        "      }\n"
        "    }\n"
        "    total += i;\n"
        "  }\n"
        "  return total;\n"
        "}\n")
    assert _cognitive(tmp_path, "b.ts", src)["sumOfPrimes"] == 7


def test_switch_counts_once_whatever_the_case_count(tmp_path):
    src = (
        "function words(n: number): string {\n"
        "  switch (n) {\n"                               # +1
        "    case 1: return 'one';\n"
        "    case 2: return 'a couple';\n"
        "    default: return 'lots';\n"
        "  }\n"
        "}\n")
    assert _cognitive(tmp_path, "c.ts", src)["words"] == 1


def test_else_if_chain_counts_once_per_link(tmp_path):
    src = (
        "function grade(x: number): string {\n"
        "  if (x > 90) { return 'A'; }\n"                # +1
        "  else if (x > 80) { return 'B'; }\n"           # +1
        "  else { return 'C'; }\n"                       # +1
        "}\n")
    assert _cognitive(tmp_path, "d.ts", src)["grade"] == 3


def test_boolean_runs_and_alternations(tmp_path):
    src = (
        "function ok(a: boolean, b: boolean, c: boolean, d: boolean) {\n"
        "  if (a && b && c || d) { return 1; }\n"        # if +1, && run +1, || alternation +1
        "  return 0;\n"
        "}\n")
    assert _cognitive(tmp_path, "e.ts", src)["ok"] == 3


def test_ternary_and_recursion_in_typescript(tmp_path):
    src = (
        "export function demo(a: number, b: number): number {\n"
        "  if (a > 0 && b > 0 || a < -1) {\n"            # +1, +1, +1
        "    for (let i = 0; i < a; i++) {\n"            # +2
        "      b = a ? b + 1 : b - 1;\n"                 # +3 (ternary at nesting 2)
        "    }\n"
        "  } else if (a === 0) {\n"                      # +1
        "    b = 2;\n"
        "  }\n"
        "  return demo(b, a);\n"                         # +1 direct recursion, once
        "}\n")
    assert _cognitive(tmp_path, "f.ts", src)["demo"] == 10


def test_python_full_house(tmp_path):
    src = (
        "def clamp(a, b):\n"
        "    if a > 0 and b > 0 or a < -1:\n"            # +1, and +1, or +1
        "        for i in range(a):\n"                   # +2
        "            b = b + 1 if a else b - 1\n"        # +3 ternary expression
        "    elif a == 0:\n"                             # +1
        "        while b:\n"                             # +2
        "            b -= 1\n"
        "    return clamp(b, a)\n")                      # +1 recursion
    assert _cognitive(tmp_path, "g.py", src)["clamp"] == 12


def test_try_finally_free_catch_pays(tmp_path):
    src = (
        "def risky(x):\n"
        "    try:\n"                                     # 0
        "        if x:\n"                                # +2 (nested in try? no: try adds no nesting -> +1)
        "            return 1\n"
        "    except ValueError:\n"                       # +1
        "        return 2\n"
        "    finally:\n"                                 # 0
        "        print(x)\n")
    assert _cognitive(tmp_path, "h.py", src)["risky"] == 2


def test_flat_function_is_zero(tmp_path):
    src = "def add(a, b):\n    return a + b\n"
    assert _cognitive(tmp_path, "i.py", src)["add"] == 0


def test_comments_never_read_as_code(tmp_path):
    src = (
        "def quiet(x):\n"
        "    # if this or that and whatever else\n"
        "    if x:  # while unlikely\n"                  # +1 only
        "        return 1\n"
        "    return 0\n")
    assert _cognitive(tmp_path, "j.py", src)["quiet"] == 1


def test_ts_block_comment_with_keywords_is_free(tmp_path):
    src = (
        "function calm(x: number) {\n"
        "  /* if (x) { for (;;) {} } */\n"
        "  if (x) { return 1; }\n"                       # +1 only
        "  return 0;\n"
        "}\n")
    assert _cognitive(tmp_path, "k.ts", src)["calm"] == 1
