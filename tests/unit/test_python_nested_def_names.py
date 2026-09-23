"""A Python def nested in other defs names each enclosing def once.

lizard 1.24.0 qualifies a new def with the full name of every def on its
nesting stack, and each of those names already carries its own parents. Two
deep that reads right, `a.b`; three deep it read `a.a.b.c`, and four deep
`a.a.b.a.a.b.c.d`. That name is the ratchet key and what every report prints.
A class adds nothing to the name, under lizard and here.

Every expected row is (long_name, start, end, ccn), counted by hand from the
source.
"""
import pytest

from crapkit.analyze import analyze_source


def _read(source: str):
    return [(r.long_name, r.start, r.end, r.ccn) for r in analyze_source("nested.py", source)]


THREE_DEEP = ("def a(x):\n    def b(y):\n        def c(z):\n            if z:\n                return 1\n"
              "            return 2\n        return c\n    return b\n")

# The decorator factory the three-deep case came from. Both stubs keep their
# body on the colon line and end there; neither lends its name to the factory.
STUB_THEN_FACTORY = '''def require_admin(
    _func: None = None,
    *,
    permission: str | None = None,
) -> Callable[
    [int],
    int,
]: ...


@overload
def require_admin(
    _func: int,
) -> int: ...


def require_admin(a):
    def decorator(func):
        def with_admin(self):
            if a:
                return 1
            return 2
        return with_admin
    return decorator
'''

SHAPES = [
    ("two deep", "def a(x):\n    def b(y):\n        return y\n    return b\n",
     [("a.b( y )", 2, 3, 1), ("a( x )", 1, 4, 1)]),
    ("three deep", THREE_DEEP,
     [("a.b.c( z )", 3, 6, 2), ("a.b( y )", 2, 7, 1), ("a( x )", 1, 8, 1)]),
    ("four deep",
     "def a(x):\n    def b(y):\n        def c(z):\n            def d(w):\n                return w\n"
     "            return d\n        return c\n    return b\n",
     [("a.b.c.d( w )", 4, 5, 1), ("a.b.c( z )", 3, 6, 1), ("a.b( y )", 2, 7, 1), ("a( x )", 1, 8, 1)]),
    ("a class between two defs and a method",
     "def a():\n    def b():\n        class C:\n            def m(self):\n                return 1\n"
     "        return C\n    return b\n",
     [("a.b.m( self )", 4, 5, 1), ("a.b( )", 2, 6, 1), ("a( )", 1, 7, 1)]),
    ("a method with two defs nested in it",
     "class K:\n    def a(self):\n        def b():\n            def c():\n                return 1\n"
     "            return c\n        return b\n",
     [("a.b.c( )", 4, 5, 1), ("a.b( )", 3, 6, 1), ("a( self )", 2, 7, 1)]),
    ("a generic def three deep",
     "def a(x):\n    def b(y):\n        def c[T](z: T) -> T:\n            return z\n        return c\n"
     "    return b\n",
     [("a.b.c( z : T )", 3, 4, 1), ("a.b( y )", 2, 5, 1), ("a( x )", 1, 6, 1)]),
    ("a decorator factory after an overload stub", STUB_THEN_FACTORY,
     [("require_admin( _func : None = None , * , permission : str | None = None , )", 1, 8, 1),
      ("require_admin( _func : int , )", 12, 14, 1),
      ("require_admin.decorator.with_admin( self )", 19, 22, 2),
      ("require_admin.decorator( func )", 18, 23, 1),
      ("require_admin( a )", 17, 24, 1)]),
]


@pytest.mark.parametrize("name, source, expected", SHAPES, ids=[s[0] for s in SHAPES])
def test_a_nested_def_names_each_enclosing_def_once(name, source, expected):
    assert _read(source) == expected

