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


# JavaScript and TypeScript shapes, compared with the TypeScript compiler's
# function list (test_ts_compiler). Each file ends with a plain function, so a
# reader that loses its place in a shape shows it on the function after.
_TAIL = "\nexport function tailFn(q) {\n  return q;\n}\n"
TS_SHAPES = {
    # R21: sibling arrows on one line are two functions.
    "sibling_arrows.js": "export const pair = [(a) => a || 0, (b) => b && 1];\n",
    "arrows_in_calls.js": ("export function outer(items) {\n"
                           "  return items.map((x) => x * 2).filter((x) => x > 1 && x < 9);\n"
                           "}\n"),
    # R45: a template literal nested in another, and one holding an arrow.
    "nested_template.ts": ("export function templated(name: string) {\n"
                           "  return `a ${`b ${name}`} c`;\n}\n"),
    "template_arrow.js": "export function late(xs) {\n  return `${xs.map((x) => x + 1)}`;\n}\n",
    "class_members.ts": ("export class Box {\n  private n = 0;\n  constructor(n) {\n    this.n = n;\n"
                         "  }\n  get size() {\n    return this.n;\n  }\n  set size(v) {\n"
                         "    this.n = v;\n  }\n  bump(by) {\n    if (by > 0) {\n      this.n += by;\n"
                         "    }\n    return this.n;\n  }\n  static make() {\n    return new Box(0);\n"
                         "  }\n}\n"),
    "object_methods.js": ("export const api = {\n  get(k) {\n    return k;\n  },\n"
                          "  put: function (k, v) {\n    return v;\n  },\n"
                          "  del: (k) => {\n    return k;\n  },\n};\n"),
    "async_and_generators.js": ("export async function load(url) {\n  try {\n"
                                "    return await fetch(url);\n  } catch (e) {\n"
                                "    return null;\n  } finally {\n    done();\n  }\n}\n\n"
                                "export function* count(n) {\n  for (let i = 0; i < n; i++) {\n"
                                "    yield i;\n  }\n}\n\nexport const later = async (x) => {\n"
                                "  return x;\n};\n"),
    "default_export.js": "export default function (a, b = 2) {\n  return a + b;\n}\n",
    "iife.js": "(function () {\n  return 1;\n})();\n",
    "overloads.ts": ("export function pick(a: string): string;\nexport function pick(a: number): number;\n"
                     "export function pick(a) {\n  return a;\n}\n"),
    "arrow_object.js": "export const make = (a) => ({ a });\n",
    "loops_and_switch.js": ("export function walk(xs) {\n  let n = 0;\n  do {\n    n++;\n"
                            "  } while (n < 3);\n  for (const k in xs) {\n    n += k.length;\n  }\n"
                            "  for (const x of xs) {\n    switch (x) {\n      case 1:\n"
                            "        n++;\n        break;\n      default:\n        n--;\n    }\n"
                            "  }\n  return n;\n}\n"),
    "jsx_handlers.jsx": ("export function Button({ onClick }) {\n"
                         "  return <button onClick={() => onClick(1)}>go</button>;\n}\n"),
    "tsx_generic_arrow.tsx": ("export const first = <T,>(xs: T[]): T => xs[0];\n"),
    "optional_chain.ts": "export function read(a) {\n  return a?.b?.c;\n}\n",
}


def ts_shape_files() -> dict[str, str]:
    """{js/<name>: source} for every JavaScript and TypeScript shape, each
    followed by the plain tail function."""
    return {f"js/{name}": source + _TAIL for name, source in TS_SHAPES.items()}
