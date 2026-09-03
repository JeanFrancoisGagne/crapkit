"""`nesting` on a Python row is how deep the function goes, not how many blocks it holds.

lizard's ND extension keys on `;` and `{` and, for Python, on the reader's
`loops` set; what it counts for Python is nesting structures, so a flat
seven-`if` function read 7 and a three-deep one read 3, the same number for
opposite shapes (measured on 0.4.15, and on `lizard -Ens`: flat 3 ifs -> 3,
nested 3 -> 6). crapkit's cognitive pass already keeps a per-function stack of
open blocks for the Sonar nesting increment; the deepest that stack gets is the
depth a reader means by "nesting". Spec item 15, decision 13: Python rows read
that depth, brace languages keep lizard's column.
"""
from pathlib import Path

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
# so a brace row that reads 3 is one that kept lizard's column.
TS = """export function f(a: number, b: number) {
  for (const x of [a, b]) {
    if (x > 0 && x < 9) { return x; }
  }
  return 0;
}
"""


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


def test_a_brace_language_keeps_lizards_depth():
    assert _nesting("f.ts", TS) == 3


def test_the_agent_json_page_names_where_each_languages_nesting_comes_from():
    """The row a reader of `next-item --json` lands on has to say which column a
    Python number is, or 7 and 1 for the same flat function across an upgrade
    reads as a regression; and it has to say what opens a level, or a reader
    who counts blocks calls the number wrong (`with` opens none)."""
    page = (ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8")
    rows = [ln for ln in page.splitlines() if ln.startswith("| `nesting` |")]
    assert len(rows) == 1, f"expected one `nesting` row, found {len(rows)}"
    row = rows[0]
    assert "cognitive" in row and "lizard" in row, row
    assert "Python" in row, row
    assert "`with`" in row and "`except`" in row, row
