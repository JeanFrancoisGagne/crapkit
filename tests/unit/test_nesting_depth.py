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

from crapkit.analyze import ANALYSIS_VERSION, analyze_source

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


def test_a_brace_language_keeps_lizards_depth():
    assert _nesting("f.ts", TS) == 3


def test_analysis_version_invalidates_python_records_cached_with_the_count():
    """A cache keys on content plus the fingerprint, and every cached .py record
    written at version 8 carries the structure count under the depth's name."""
    assert ANALYSIS_VERSION > 8


def test_the_agent_json_page_names_where_each_languages_nesting_comes_from():
    """The row a reader of `next-item --json` lands on has to say which column a
    Python number is, or 7 and 1 for the same flat function across an upgrade
    reads as a regression."""
    page = (ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8")
    rows = [ln for ln in page.splitlines() if ln.startswith("| `nesting` |")]
    assert len(rows) == 1, f"expected one `nesting` row, found {len(rows)}"
    row = rows[0]
    assert "cognitive" in row and "lizard" in row, row
    assert "Python" in row, row
