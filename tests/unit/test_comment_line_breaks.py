"""A comment is as many lines as it holds LFs.

lizard's comment_counter counted a comment's lines with str.splitlines, which
also ends a line at \\x0b, \\x0c, \\x1c, \\x1d, \\x1e, \\x85, U+2028 and U+2029. A
comment holding one of them pushed every function below it down a line, so
brief, worklist and the gate named lines the function does not sit on, and a
span pushed past its function joined the coverage of the next one: a function
called once scored its uncalled neighbour's 0%. Python's compiler, coverage.py,
C compilers, git, c8, @vitest/coverage-v8 and the editor all read that comment as
one line. JavaScript's own rule, which Babel, TypeScript source maps and V8's
stack traces number by, also ends a line at U+2028 and U+2029; istanbul_lines
moves those numbers onto these lines (tests/e2e/test_comment_line_ends_e2e.py).

brief's source and duplication's shingles cut the file at the span, so they
split it the same way: with sourcelines.source_lines, not str.splitlines.
"""
import pytest

from crapkit.analyze import analyze_source
from crapkit.dup import find_duplicates
from crapkit.packet import function_source
from crapkit.snapshot import InventoryRow

BREAKS = ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"]
IDS = ["vt", "ff", "fs", "gs", "rs", "nel", "ls", "ps"]

PY = "def f(a):\n    if a:\n        return 1\n    return 0\n\n\ndef g(a):\n    return a\n"
C = ("int f(int a) {\n    if (a)\n        return 1;\n    return 0;\n}\n\n"
     "int g(int a) {\n    return a;\n}\n")
SOURCES = pytest.mark.parametrize("path, comment, body", [
    ("e.py", "# a{}b\n", PY),
    ("e.c", "// a{}b\n", C),
    ("e.c", "/* a{}b\n   c */\n", C),
    ("e.js", "// a{}b\n", C.replace("int ", "function ").replace("function a", "a")),
], ids=["python", "c-line", "c-block", "js"])


def spans(path: str, code: str) -> list[tuple[str, int, int]]:
    return [(r.long_name, r.start, r.end) for r in analyze_source(path, code, note=False)]


@pytest.mark.parametrize("odd", BREAKS, ids=IDS)
@SOURCES
def test_a_comment_holding_a_break_str_splitlines_knows_is_one_line(path, comment, body, odd):
    plain = spans(path, "x = 1;\n" + comment.format("") + body)

    assert spans(path, "x = 1;\n" + comment.format(odd) + body) == plain
    assert plain[0][1] == 3 + comment.count("\n") - 1


@pytest.mark.parametrize("odd", BREAKS, ids=IDS)
@SOURCES
def test_brief_shows_the_function_below_such_a_comment(path, comment, body, odd):
    code = "x = 1;\n" + comment.format(odd) + body
    (_, start, end), _ = spans(path, code)

    assert function_source(code, start, end) == body[:body.index("\n\n")]


def test_a_forgive_comment_holding_a_form_feed_still_forgives():
    code = "def f(a):  #lizard forgive\x0c\n    if a:\n        return 1\n    return 0\n"

    assert spans("e.py", code) == spans("e.py", code.replace("\x0c", ""))


def _rows(path: str, code: str) -> list[InventoryRow]:
    return [InventoryRow(scope="src", path=path, long_name=r.long_name, start=r.start, end=r.end,
                         ccn_std=r.ccn, ccn_mod=r.ccn, ccn=r.ccn, nloc=r.nloc, params=1, nesting=0)
            for r in analyze_source(path, code, note=False)]


@pytest.mark.parametrize("odd", BREAKS, ids=IDS)
def test_duplication_pairs_twins_below_such_a_comment(odd):
    """Twelve of the characters in one comment between two twins: the second twin's
    span sat twelve lines below its text, and duplication compared the comment and
    blank lines with the first twin."""
    body = "".join(f"    total = total + {i} * n\n" for i in range(8))
    twin = "def {}(n):\n    total = 0\n" + body + "    return total\n\n\n"
    code = twin.format("alpha") + "# " + odd.join("abcdefghijklm") + "\n" + twin.format("beta")

    (pair,) = find_duplicates(_rows("m.py", code), lambda: {"m.py": code})

    assert [f["long_name"] for f in pair["functions"]] == ["alpha( n )", "beta( n )"]
