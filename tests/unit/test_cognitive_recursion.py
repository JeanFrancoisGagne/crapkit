"""Direct recursion costs +1 cognitive when the body calls the function itself.

The Sonar paper (Cognitive Complexity v1.7, B1) charges one point for each
method in a recursion cycle. The pass used to read recursion as any body token
spelled like the name lizard held when the function's first token arrived.
That missed every call in Go (the name was still empty), Java, C++ in a
namespace or class and PowerShell (the name was `K::int`, `detail::pow10` or
empty), shell (the body sits at brace depth 0) and a nested Python def (its
name is `outer.inner`). It invented a call from a local variable, another
object's method (`self.inner.close()`), a constructor (`File(fd)` inside
`File::open`) and an Objective-C message to another selector or to super.

A call is now what counts: the bare name followed by `(`, reached through no
receiver or through `self`, `this`, `Self`, `cls` or the function's own
qualifier; a command word in shell and PowerShell; and in Objective-C a message
to `self` whose whole selector is the method's.
"""
import pytest

from crapkit.analyze import analyze_source


def _cognitive(path: str, source: str, name: str) -> int:
    rows = [r for r in analyze_source(path, source, note=False)
            if r.long_name.startswith(name)]
    assert len(rows) == 1, [r.long_name for r in analyze_source(path, source, note=False)]
    return rows[0].cognitive


FACT = {  # if +1, the call to itself +1
    "K.java": ("class K {\n    static int fact(int n) {\n        if (n <= 1) {\n            return 1;\n"
               "        }\n        return n * fact(n - 1);\n    }\n}\n", "K::fact"),
    "a.go": ("package p\n\nfunc Fact(n int) int {\n\tif n <= 1 {\n\t\treturn 1\n\t}\n"
             "\treturn n * Fact(n-1)\n}\n", "Fact"),
    "a.rs": ("fn fact(n: u64) -> u64 {\n    if n <= 1 {\n        return 1;\n    }\n"
             "    n * fact(n - 1)\n}\n", "fact"),
    "a.swift": ("func fact(n: Int) -> Int {\n    if n <= 1 {\n        return 1\n    }\n"
                "    return n * fact(n: n - 1)\n}\n", "fact"),
    "a.zig": ("fn fact(n: u32) u32 {\n    if (n <= 1) {\n        return 1;\n    }\n"
              "    return n * fact(n - 1);\n}\n", "fact"),
    "a.sh": ("fact() {\n  if [ \"$1\" -le 1 ]; then\n    echo 1\n    return\n  fi\n"
             "  echo $(( $1 * $(fact $(( $1 - 1 ))) ))\n}\n", "fact"),
    "a.ps1": ("function Get-Factorial($n) {\n    if ($n -le 1) {\n        return 1\n    }\n"
              "    return $n * (Get-Factorial ($n - 1))\n}\n", "Get-Factorial"),
    "a.py": ("def fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\n", "fact"),
    "a.c": ("int fact(int n) {\n    if (n <= 1) {\n        return 1;\n    }\n"
            "    return n * fact(n - 1);\n}\n", "fact"),
    "a.ts": ("function fact(n: number): number {\n  if (n <= 1) {\n    return 1;\n  }\n"
             "  return n * fact(n - 1);\n}\n", "fact"),
}


@pytest.mark.parametrize("path", sorted(FACT))
def test_a_call_to_itself_costs_one_in_every_language(path):
    source, name = FACT[path]
    assert _cognitive(path, source, name) == 2


CPP_SCOPED = [  # (source, long-name prefix, Sonar value)
    ("namespace detail {\nlong long pow10(unsigned n) {\n"
     "    return n == 0 ? 1 : 10 * pow10(n - 1);\n}\n}\n", "detail::pow10", 2),
    ("struct Calc {\n    int fact(int n) {\n        if (n <= 1) {\n            return 1;\n"
     "        }\n        return n * fact(n - 1);\n    }\n};\n", "Calc::fact", 2),
    ("int Calc::fact(int n) {\n    if (n <= 1) {\n        return 1;\n    }\n"
     "    return n * Calc::fact(n - 1);\n}\n", "Calc::fact", 2),
    ("int Calc::fact(int n) {\n    return n * this->fact(n - 1);\n}\n", "Calc::fact", 1),
    # a constructor call and a cast name the class, never the method
    ("File File::open(int fd) {\n    if (fd == -1) {\n        fail();\n    }\n"
     "    return File(fd);\n}\n", "File::open", 1),
    ("void Stream::grow(int n) {\n    if (n) {\n"
     "        static_cast<Stream&>(*this).flush();\n    }\n}\n", "Stream::grow", 1),
]


@pytest.mark.parametrize("source,name,want", CPP_SCOPED)
def test_a_qualified_cpp_function_calls_itself_by_its_last_name(source, name, want):
    assert _cognitive("a.cpp", source, name) == want


NOT_CALLS = [  # the name spelled in a body that calls nothing of its own
    ("a.py", "def close(self):\n    self.inner.close()\n", "close"),
    ("a.py", "def close(self, other):\n    other.close()\n    return close\n", "close"),
    ("a.c", "int count(int n) {\n    int count = n;\n    return count;\n}\n", "count"),
    ("a.js", "app.route = function route(path) {\n  return this.router.route(path);\n};\n", "route"),
    ("a.ts", "function walk(t: Tree) {\n  return t.walk();\n}\n", "walk"),
    ("a.go", "package p\n\nfunc Walk(t T) int {\n\tWalk := t.n\n\treturn Walk\n}\n", "Walk"),
    ("a.sh", "greet() {\n  echo greet\n}\n", "greet"),
    ("a.ps1", "function Get-Name {\n    Write-Output 'Get-Name'\n}\n", "Get-Name"),
]


@pytest.mark.parametrize("path,source,name", NOT_CALLS)
def test_the_name_without_a_call_costs_nothing(path, source, name):
    assert _cognitive(path, source, name) == 0


def test_a_method_through_its_own_receiver_is_recursion(tmp_path):
    py = "class A:\n    def close(self):\n        self.close()\n"
    js = "class A {\n  walk(n) {\n    return this.walk(n - 1);\n  }\n}\n"
    rs = "impl A {\n    fn walk(&self) -> i32 {\n        self.walk()\n    }\n    fn f() -> i32 {\n        Self::f()\n    }\n}\n"
    assert _cognitive("a.py", py, "close") == 1
    assert _cognitive("a.js", js, "walk") == 1
    assert [r.cognitive for r in analyze_source("a.rs", rs, note=False)] == [1, 1]


def test_a_nested_python_def_that_calls_itself_is_recursion():
    source = ("def with_recursive_helper(n):\n    def countdown(k):\n        if k <= 0:\n"
              "            return 0\n        return countdown(k - 1)\n    return countdown(n)\n")
    rows = {r.long_name.split("(")[0].strip(): r.cognitive
            for r in analyze_source("a.py", source, note=False)}
    assert rows == {"with_recursive_helper.countdown": 2, "with_recursive_helper": 0}


OBJC = ("@implementation S\n\n"
        "- (int)narrower:(int)a to:(int)b {\n    return [self narrower:a];\n}\n\n"
        "- (void)viewDidLoad {\n    [super viewDidLoad];\n}\n\n"
        "- (int)walk:(int)a to:(int)b {\n    return [self walk:[self other:a] to:b];\n}\n\n"
        "- (void)reload {\n    [self reload];\n}\n\n"
        "- (void)relay {\n    [self.next relay];\n}\n\n"
        "@end\n")


def test_an_objective_c_message_is_recursion_only_to_self_with_the_whole_selector():
    rows = {r.long_name.split("(")[0].strip(): r.cognitive
            for r in analyze_source("s.m", OBJC, note=False)}
    assert rows == {"narrower:": 0, "viewDidLoad": 0, "walk:": 1, "reload": 1, "relay": 0}


def test_recursion_costs_one_however_many_calls():
    source = ("def fib(n):\n    if n < 2:\n        return n\n"
              "    return fib(n - 1) + fib(n - 2)\n")
    assert _cognitive("a.py", source, "fib") == 2
