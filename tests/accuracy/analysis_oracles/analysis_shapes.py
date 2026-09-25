"""Named Python source shapes the reader must list the way Python's ast does.

Each shape is one small module, named after what it holds. Several reproduce
a past reader bug (the retro id in the comment); the rest are layouts the
Python reference allows that a token-based reader can misread. A shape the
running Python cannot parse (a 3.14 construct on 3.12) is left out of the
comparison and counted, as analysis_corpora does. No crapkit import.
"""
from __future__ import annotations

import ast

PY_SHAPES = {
    # R37 (#72): a signature that runs past its first `)`.
    "issue72": ("class Popen:\n"
                "    def __init__(self, args, bufsize=-1, pass_fds=(), *,\n"
                "                 user=None, group=None, umask=-1):\n"
                "        if user:\n"
                "            self.user = user\n"
                "        return None\n\n"
                "    def poll(self):\n"
                "        return self.user\n"),
    "annotated_return": ("def annotated(a: int) -> dict[str,\n"
                         "                              int]:\n"
                         "    if a:\n        return {}\n    return {'a': a}\n\n\n"
                         "def after_annotated(b):\n    return b\n"),
    # R38: PEP 695 type parameters before the parameter list.
    "pep695": ("def first[T](a: T) -> T:\n    if a:\n        return a\n    return a\n\n\n"
               "def second[T: (int, str), U](a: T, b: U) -> T:\n    return a\n\n\n"
               "class Box[T]:\n    def get[S](self, s: S) -> T:\n        return self.t\n"),
    # R40: a def nested three deep names each enclosing def once.
    "three_deep": ("def outer(a):\n    def middle(b):\n        def inner(c):\n"
                   "            def innermost(d):\n                return d\n"
                   "            return innermost\n        return inner\n    return middle\n"),
    # R41, R44: a body on the colon line.
    "one_line_defs": ("def a(x): return x\ndef b(y): return y if y else 0\n\n\n"
                      "def c(z):\n    return z\n"),
    # R43: a one-line def whose body leaves a bracket open to the lexer.
    "fstring_bracket": ('def fill(x): return f"{x:(>10}"\n\n\n'
                        "def after(y):\n    if y:\n        return 1\n    return 0\n"),
    # R20: module code after the last def.
    "module_code_after": ("def last(a):\n    if a:\n        return 1\n    return 0\n\n\n"
                          "if __name__ == '__main__':\n    for i in range(3):\n        last(i)\n"),
    "async_and_decorated": ("@decorator\nasync def fetch(x):\n    async with x:\n"
                            "        return await x\n\n\n@a\n@b(1, key=lambda v: v)\n"
                            "def deco(y):\n    return y\n"),
    "methods": ("class Holder:\n    def method(self, x):\n        if x:\n            return self\n"
                "        return None\n\n    @staticmethod\n    def helper(x):\n        return x\n\n"
                "    @property\n    def value(self):\n        return 1\n\n"
                "    class Inner:\n        def deep(self):\n            return 2\n"),
    "wrapped_signature": ("def wrapped(\n    first,\n    second,\n):\n    if first:\n"
                          "        return second\n    return None\n"),
    "continued_signature": ("def continued(a) \\\n        -> int:\n    if a:\n        return 1\n"
                            "    return 0\n"),
    "class_in_def": ("def factory():\n    class Made:\n        def method(self):\n"
                     "            return 1\n    return Made\n"),
    "def_in_strings": ('def real():\n    s = "def fake(x):"\n    # def other(y):\n'
                       '    """\n    def third(z):\n        pass\n    """\n    return s\n'),
    "semicolon_body": "def semi(x): y = x; return y\n\n\ndef after_semi(z):\n    return z\n",
    "default_lambda": "def keyed(key=lambda v: v, b=1):\n    return key(b)\n",
    "lambdas_only": "square = lambda x: x * x\npairs = sorted([], key=lambda p: p[1])\n",
    "decorated_class_method": ("@dataclass\nclass Point:\n    x: int = 0\n\n"
                               "    def moved(self, dx):\n        return Point(self.x + dx)\n"),
    "nested_after_body": ("def parent(a):\n    def helper(b): return b\n    if a:\n"
                          "        return helper(a)\n    return 0\n"),
    # PEP 758 (3.14): except without parentheses; PEP 750 (3.14): a t-string.
    "pep758_except": ("def guarded(f):\n    try:\n        return f()\n"
                      "    except ValueError, TypeError:\n        return None\n"),
    "pep750_tstring": 'def templated(name):\n    return t"hello {name}"\n',
}


def parses(source: str) -> bool:
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


def py_shape_files() -> dict[str, str]:
    """{shapes/<name>.py: source} for every shape the running Python parses."""
    return {f"shapes/{name}.py": source for name, source in PY_SHAPES.items() if parses(source)}


def rejected_shapes() -> list[str]:
    return sorted(name for name, source in PY_SHAPES.items() if not parses(source))
