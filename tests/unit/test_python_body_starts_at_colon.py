"""A Python def's body starts at the first token after its signature's colon.

The cognitive pass started a Python body at the def's first newline. A def
whose body sits on its colon line reaches no newline before that body ends, so
it read cognitive 0 and nesting 0 whatever the body held, and a signature
written over several lines had its continuation lines counted as body. The body
now starts after the `:` at bracket depth 0 that ends the signature, so a def
reads the same with its body on the colon line as with its body on the next
line, and a signature reads the same over several lines as on one.

Every expected (cognitive, nesting) pair is counted by hand with the Sonar rules
test_cognitive.py spells out: +1 plus the nesting for a loop or an `if`, the
ternary form included, +1 per run of a boolean operator, +1 once for recursion.
"""
import pytest

from crapkit.analyze import analyze_source


def _read(source: str) -> dict[str, tuple[int, int]]:
    return {r.long_name.split("(")[0].strip(): (r.cognitive, r.nesting)
            for r in analyze_source("body.py", source)}


# Each case twice: the shape under test, then the same def written the way the
# cognitive pass already read right. Both must give the hand count.
SAME_DEF = [
    ("a comprehension on the colon line",  # for +1 opens a level, its if +2, or +1
     "def f(x): return [a for a in x if a or x]\n",
     "def f(x):\n    return [a for a in x if a or x]\n",
     (4, 1)),
    ("a boolean ternary on the colon line",  # if +1, and +1, the else arm is free
     "def f(x, y): return 1 if x and y else 2\n",
     "def f(x, y):\n    return 1 if x and y else 2\n",
     (2, 0)),
    ("recursion on the colon line",  # if +1, the call to itself +1
     "def fact(n): return 1 if n < 2 else n * fact(n - 1)\n",
     "def fact(n):\n    return 1 if n < 2 else n * fact(n - 1)\n",
     (2, 0)),
    ("a method on the colon line",  # and +1
     "class K:\n    def m(self): return self.a and self.b\n",
     "class K:\n    def m(self):\n        return self.a and self.b\n",
     (1, 0)),
    ("a colon inside the signature's brackets",  # the body's or +1, the lambda's or is signature
     "def f(key=lambda v: v or 0) -> dict[str, int]: return key(1) or 2\n",
     "def f(key=lambda v: v or 0) -> dict[str, int]:\n    return key(1) or 2\n",
     (1, 0)),
    ("a colon inside a type parameter list",  # or +1
     "def f[T: int](x: T) -> T: return x or x\n",
     "def f[T: int](x: T) -> T:\n    return x or x\n",
     (1, 0)),
    ("a ternary default on a signature continuation line",
     "def f(a,\n      b=1 if X else 2):\n    return a\n",
     "def f(a, b=1 if X else 2):\n    return a\n",
     (0, 0)),
    ("a multi-line signature with its body on the colon line",  # the body's or +1
     "def f(a,\n      b=1 if X else 2): return a or b\n",
     "def f(a, b=1 if X else 2):\n    return a or b\n",
     (1, 0)),
    ("a signature continuation line that opens with if",
     "def f(a=(1\n         if X else 2)):\n    return a\n",
     "def f(a=(1 if X else 2)):\n    return a\n",
     (0, 0)),
]


@pytest.mark.parametrize("shape, source, reference, expected", SAME_DEF,
                         ids=[case[0] for case in SAME_DEF])
def test_a_def_reads_its_body_from_the_signature_colon(shape, source, reference, expected):
    (name,) = _read(reference)
    assert _read(reference)[name] == expected, "the reference shape is the hand count"
    assert _read(source) == {name: expected}


def test_a_match_line_opening_the_body_still_counts_its_subject():
    """lizard's soft-keyword lookahead reads a `match` line ahead before the
    rest of the chain sees its first token, so the body start has to be the
    cognitive pass's own count, not a mark a later extension sets."""
    source = ("def f(a, b):\n"
              "    match (a or b):\n"  # or +1; a Python match and its arms are free
              "        case 1:\n"
              "            return 1\n")
    assert _read(source) == {"f": (1, 0)}


def test_a_one_line_def_nested_in_another_keeps_its_own_body():
    source = ("def outer(xs):\n"
              "    def pick(x): return x if x else None\n"  # if +1
              "    return [pick(x) for x in xs]\n")         # for +1 opens a level
    assert _read(source) == {"outer": (1, 1), "outer.pick": (1, 0)}
