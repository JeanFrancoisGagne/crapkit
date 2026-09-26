"""A source's lines end at LF, CRLF and a lone CR, the way Python's compiler,
coverage.py and analyze.decode_source read it.

`str.splitlines` also ends a line at \\x0b, \\x0c, \\x1c-\\x1e, \\x85, \\u2028 and
\\u2029. The readers that numbered a file with it put each function one line
lower for every form feed above it: mutate grew no mutant on the changed line,
brief showed the blank lines above the function, and duplication missed a twin.
apply_mutant also dropped any line end but LF, so in a CR-only file the mutated
line ran into the next one and the mutant died on a syntax error.
"""
import pytest

from crapkit.dup import find_duplicates
from crapkit.mutate import Mutant, apply_mutant, file_mutants
from crapkit.packet import function_source
from crapkit.snapshot import InventoryRow
from crapkit.sourcelines import source_lines

# Three form-feed lines, one line each to the reader: `def target` is line 5.
FORM_FEED = ("x = 1\n\x0c\n\x0c\n\x0c\n"
             "def target(b):\n    if b > 0:\n        return 1\n    return 2\n")
TARGET = "def target(b):\n    if b > 0:\n        return 1\n    return 2"


def test_a_line_ends_only_at_lf_crlf_and_a_lone_cr():
    kept = "a\x0bb\x0cc\x1cd\x1de\x1ef\x85g h i"

    assert source_lines(kept + "\r\nj\rk\nl", keepends=True) == [kept + "\r\n", "j\r", "k\n", "l"]


def test_mutate_grows_its_mutants_on_the_changed_line_below_a_form_feed():
    mutants = file_mutants(FORM_FEED, {6}, "python")

    assert {(m.line, m.original) for m in mutants} == {(6, "    if b > 0:")}


@pytest.mark.parametrize("end", ["\r", "\r\n"])
def test_a_mutant_keeps_the_line_end_of_the_line_it_replaced(end):
    text = end.join(["def f(b):", "    if b > 0:", "        x = 1", "        return x", ""])
    mutant = Mutant("m.py", 2, "    if b > 0:", "    if b >= 0:", "> -> >=")

    assert apply_mutant(text, mutant) == text.replace("b > 0", "b >= 0")


def test_brief_shows_the_function_below_a_form_feed():
    assert function_source(FORM_FEED, 5, 8) == TARGET


def row(name: str, start: int, end: int) -> InventoryRow:
    return InventoryRow(scope="src", path="m.py", long_name=name, start=start, end=end,
                        ccn_std=1, ccn_mod=1, ccn=1, nloc=end - start + 1, params=1, nesting=0)


def test_duplication_pairs_twins_a_form_feed_separates():
    body = "".join(f"    total = total + {i} * n\n" for i in range(8))
    twin = "def {}(n):\n    total = 0\n" + body + "    return total\n\n\n"
    # alpha is lines 1-11, six form-feed lines follow its two blank lines, beta is 20-30.
    text = twin.format("alpha") + "\x0c\n" * 6 + twin.format("beta")
    rows = [row("alpha( n )", 1, 11), row("beta( n )", 20, 30)]

    (pair,) = find_duplicates(rows, lambda: {"m.py": text})

    assert [f["long_name"] for f in pair["functions"]] == ["alpha( n )", "beta( n )"]
