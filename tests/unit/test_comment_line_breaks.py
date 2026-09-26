"""A comment is as many lines as it holds LFs.

lizard's comment_counter counted a comment's lines with str.splitlines, which
also ends a line at \\x0b, \\x0c, \\x1c, \\x1d, \\x1e, \\x85, U+2028 and U+2029. A
comment holding one of them pushed every function below it down a line, so
brief, worklist and the gate named lines the function does not sit on, and a
span pushed past its function joined the coverage of the next one: a function
called once scored its uncalled neighbour's 0%. Python, C, git and the editor
all read that comment as one line.
"""
import pytest

from crapkit.analyze import analyze_source

BREAKS = ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"]
IDS = ["vt", "ff", "fs", "gs", "rs", "nel", "ls", "ps"]

PY = "def f(a):\n    if a:\n        return 1\n    return 0\n\n\ndef g(a):\n    return a\n"
C = ("int f(int a) {\n    if (a)\n        return 1;\n    return 0;\n}\n\n"
     "int g(int a) {\n    return a;\n}\n")


def spans(path: str, code: str) -> list[tuple[str, int, int]]:
    return [(r.long_name, r.start, r.end) for r in analyze_source(path, code, note=False)]


@pytest.mark.parametrize("odd", BREAKS, ids=IDS)
@pytest.mark.parametrize("path, comment, body", [
    ("e.py", "# a{}b\n", PY),
    ("e.c", "// a{}b\n", C),
    ("e.c", "/* a{}b\n   c */\n", C),
    ("e.js", "// a{}b\n", C.replace("int ", "function ").replace("function a", "a")),
], ids=["python", "c-line", "c-block", "js"])
def test_a_comment_holding_a_break_str_splitlines_knows_is_one_line(path, comment, body, odd):
    plain = spans(path, "x = 1;\n" + comment.format("") + body)

    assert spans(path, "x = 1;\n" + comment.format(odd) + body) == plain
    assert plain[0][1] == 3 + comment.count("\n") - 1


def test_a_forgive_comment_holding_a_form_feed_still_forgives():
    code = "def f(a):  #lizard forgive\x0c\n    if a:\n        return 1\n    return 0\n"

    assert spans("e.py", code) == spans("e.py", code.replace("\x0c", ""))
