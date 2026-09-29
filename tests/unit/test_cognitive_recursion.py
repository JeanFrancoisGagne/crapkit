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


OVERLOADS = [  # (label, path, source, name, Sonar value)
    ("Java delegates to a longer overload", "K.java",
     "class K {\n    static String format(Date date) {\n        return format(date, false);\n    }\n}\n",
     "K::format", 0),
    ("Java calls itself", "K.java",
     "class K {\n    static int depth(Node n) {\n        return n == null ? 0 : 1 + depth(n.parent);\n"
     "    }\n}\n", "K::depth", 2),
    ("Java varargs take any count", "K.java",
     "class K {\n    static int sum(int... xs) {\n        return xs.length == 0 ? 0 : sum(1, 2, 3);\n"
     "    }\n}\n", "K::sum", 2),
    ("Swift delegates to a longer overload", "a.swift",
     "func validate() -> Int {\n    return validate(statusCode: 200, strict: true)\n}\n", "validate", 0),
    ("Swift leaves a default out", "a.swift",
     "func fetch(_ url: String, retries: Int = 3) {\n    fetch(url)\n}\n", "fetch", 1),
    ("C++ leaves a default out", "a.cpp",
     "int f(int a, bool upper = false) {\n    return f(a);\n}\n", "f", 1),
    ("C++ delegates to a longer overload", "a.cpp",
     "void write(int a) {\n    write(a, 2);\n}\n", "write", 0),
    ("C++ passes a call as its one argument", "a.cpp",
     "int f(int a) {\n    return f(g(a, 1));\n}\n", "f", 1),
    ("C++ takes no arguments", "a.cpp", "void spin() {\n    spin();\n}\n", "spin", 1),
    ("C varargs", "a.c", "int g(int a, ...) {\n    return g(1, 2, 3);\n}\n", "g", 1),
]


@pytest.mark.parametrize("label,path,source,name,want", OVERLOADS, ids=[c[0] for c in OVERLOADS])
def test_an_overload_with_another_arity_is_another_function(label, path, source, name, want):
    assert _cognitive(path, source, name) == want


def _swift_d(parameters: str, arguments: str) -> str:
    return (f"class A {{\n    func d({parameters}) -> Int {{\n"
            f"        if x > 0 {{ return d({arguments}) }}\n        return 0\n    }}\n}}\n")


# Swift names a function by its argument labels as well: `d(of:)` and `d(for:)`
# are two functions, so a call reaches this one only when each argument's label,
# or its lack of one, is the next parameter's, a parameter with a default left
# out. if +1, and +1 when the call is recursion.
SWIFT_LABELS = [  # (label, parameters, arguments, Sonar value)
    ("same label", "of x: Int", "of: x - 1", 2),
    ("another label", "of x: Int", "for: x", 1),
    ("a label where the parameter has none", "_ x: Int", "of: x", 1),
    ("no label where the parameter has none", "_ x: Int", "x - 1", 2),
    ("no label where the parameter has one", "of x: Int", "x - 1", 1),
    ("the parameter's name is its label", "x: Int", "x: x - 1", 2),
    ("two labels", "a x: Int, b y: Int", "a: x - 1, b: y", 2),
    ("the second label differs", "a x: Int, b y: Int", "a: x - 1, c: y", 1),
    ("a default left out", "a x: Int, b y: Int = 0", "a: x - 1", 2),
    ("a default left out before a label", "a x: Int, b y: Int = 0, c z: Int", "a: x - 1, c: z", 2),
    ("a conditional's colon is no label", "_ x: Int", "x > 1 ? x - 1 : 0", 4),
]


@pytest.mark.parametrize("label,parameters,arguments,want", SWIFT_LABELS,
                         ids=[c[0] for c in SWIFT_LABELS])
def test_a_swift_call_reaches_the_function_only_with_its_labels(label, parameters, arguments, want):
    assert _cognitive("a.swift", _swift_d(parameters, arguments), "d") == want


def test_a_swift_type_name_call_with_another_label_is_another_function():
    """`DebugDescription.description(for: request.headers)` inside
    `description(of request:)`: the type's name reaches its functions, and the
    label picks another one."""
    source = ("private enum DebugDescription {\n    static func description(of request: Req) -> String {\n"
              "        return DebugDescription.description(for: request.headers)\n    }\n}\n")
    assert _cognitive("a.swift", source, "description") == 0


def _cognitive_at(path: str, source: str, line: int) -> int:
    rows = [r for r in analyze_source(path, source, note=False) if r.start == line]
    assert len(rows) == 1, [(r.long_name, r.start) for r in analyze_source(path, source, note=False)]
    return rows[0].cognitive


# A call that another function of the same name in the file takes too is that
# one's as much as this one's, and the pass sees no types to tell them apart,
# so only a call no other one takes is recursion. Each read 1 where the body
# forwards to the other overload.
OVERLOAD_SETS = [  # (label, path, source, start line of the row read, Sonar value)
    ("C++ forwards with as many arguments", "a.cpp",
     "struct V {\n  bool starts_with(V sv) const { return true; }\n"
     "  bool starts_with(const char* s) const { return starts_with(V(s)); }\n};\n", 3, 0),
    ("Java forwards to an overload defined after it", "A.java",
     "class A {\n  Object fromJson(String json) { return fromJson(new StringReader(json)); }\n"
     "  Object fromJson(Reader in) { return null; }\n}\n", 2, 0),
    ("Java recursion no other overload takes", "A.java",
     "class A {\n  boolean isAssignableFrom(Type from) { return isAssignableFrom(from, null, null); }\n"
     "  boolean isAssignableFrom(Type from, P to, Map m) { return isAssignableFrom(to, to, m); }\n}\n", 3, 1),
    ("Java forwards to a longer overload", "A.java",
     "class A {\n  boolean isAssignableFrom(Type from) { return isAssignableFrom(from, null, null); }\n"
     "  boolean isAssignableFrom(Type from, P to, Map m) { return isAssignableFrom(to, to, m); }\n}\n", 2, 0),
    ("Swift forwards through a default", "a.swift",
     "class S {\n  func request(_ r: Req) -> Int { return 1 }\n"
     "  func request(_ url: String, method: Int = 0) -> Int { return request(r) }\n}\n", 3, 0),
    ("Swift overloads in two extensions of one type", "a.swift",
     "extension S {\n  func load(_ r: Req) -> Int { return 1 }\n}\n"
     "extension S {\n  func load(_ url: String) -> Int { return load(Req(url)) }\n}\n", 5, 0),
    ("Swift: another type's function of the name is no overload", "a.swift",
     "struct A {\n  func walk(_ n: Int) { walk(n - 1) }\n}\n"
     "struct B {\n  func walk(_ s: String) {}\n}\n", 2, 1),
    ("C: one function written twice is no overload", "a.c",
     "#ifdef WIN\nint walk(int n) { return walk(n - 1); }\n#else\n"
     "int walk(int n) { return walk(n - 1); }\n#endif\n", 2, 1),
    # The limit: the same count of arguments of other types reads as the other
    # overload's too, and a real call to itself is missed.
    ("Java: same count, other types", "A.java",
     "class A {\n  int walk(int n) { return walk(n - 1); }\n  int walk(String s) { return 0; }\n}\n", 2, 0),
]


@pytest.mark.parametrize("label,path,source,line,want", OVERLOAD_SETS, ids=[c[0] for c in OVERLOAD_SETS])
def test_a_call_another_overload_takes_too_is_no_recursion(label, path, source, line, want):
    assert _cognitive_at(path, source, line) == want


def _swift_cancel(call: str) -> str:
    return ("class D {\n  func cancel(producingResumeData flag: Bool) {\n"
            f"    {call}\n  }}\n}}\n")


# A closure passed in the parentheses used to cut the call short, and a call
# cut short counted whatever its labels; a closure after the `)` was not
# counted as an argument at all.
SWIFT_CLOSURES = [  # (label, source, start line, Sonar value)
    ("a closure with another label", _swift_cancel("cancel(optionallyProducingResumeData: { _ in })"), 2, 0),
    ("an empty closure with another label", _swift_cancel("cancel(optionallyProducingResumeData: { })"), 2, 0),
    ("a shorthand closure with another label", _swift_cancel("cancel(optionallyProducingResumeData: { $0 })"),
     2, 0),
    ("a closure with the function's label", _swift_cancel("cancel(producingResumeData: { $0 }())"), 2, 1),
    ("a trailing closure", "func each(_ n: Int, _ body: (Int) -> Void) {\n  if n > 0 {\n"
     "    each(n - 1) { body($0) }\n  }\n}\n", 1, 2),
    ("a trailing closure and no parentheses", "func run(_ body: () -> Void) {\n  run { }\n}\n", 1, 1),
    ("a trailing closure past a default", "func load(_ url: String, retries: Int = 0, done: () -> Void) {\n"
     "  load(url) { }\n}\n", 1, 1),
    ("a trailing closure where no parameter is left", "func each(_ n: Int) {\n  each(n - 1) { }\n}\n", 1, 0),
    ("a structure's block is no closure", "func valid(_ n: Int, _ strict: Bool) -> Bool {\n"
     "  if valid(n - 1) {\n    return true\n  }\n  return false\n}\n", 1, 1),
    ("a closure type's own parameters", "class R {\n"
     "  func on(_ q: Int = 0, perform handler: (_ r: Int, _ done: (Int) -> Void) -> Void) {}\n"
     "  func on(_ q: Int = 0, perform handler: (Int) -> Void) {\n    on(q) { r, done in done(r) }\n  }\n}\n",
     3, 0),
    ("a generic's commas", "class R {\n  func add(on queue: Q, stream: Handler<S, F>) {\n"
     "    if more { self.add(on: queue, stream: stream) }\n  }\n}\n", 2, 2),
]


@pytest.mark.parametrize("label,source,line,want", SWIFT_CLOSURES, ids=[c[0] for c in SWIFT_CLOSURES])
def test_a_swift_closure_is_one_argument_wherever_it_is_passed(label, source, line, want):
    """The last two cases: lizard splits a parameter list at every comma, in a
    closure type's parameters and a generic's arguments too, so the function
    read more parameters than it takes."""
    assert _cognitive_at("a.swift", source, line) == want


def test_a_go_method_calls_itself_through_its_receiver():
    source = ("package p\n\nfunc (c *Command) Traverse(args []string) int {\n\tif len(args) == 0 {\n"
              "\t\treturn 0\n\t}\n\treturn c.Traverse(args[1:])\n}\n\n"
              "func (c *Command) Name() string {\n\treturn c.parent.Name()\n}\n")
    rows = [r.cognitive for r in analyze_source("a.go", source, note=False)]
    assert rows == [2, 0]


# A method is reached through its object or its class. In Python, JavaScript and
# TypeScript a bare name in a method's body is looked up outside the class, and in
# Go outside the receiver's methods, so a bare call there reaches another
# function: the builtin, the module function or the imported helper the method
# wraps. The class's own name reaches it, as `self`, `this` and `cls` do.
METHODS = [  # (label, path, source, name, Sonar value)
    ("Python method calls the builtin it shadows", "a.py",
     "class A:\n    def open(self):\n        return open(self.path)\n", "open", 0),
    ("Python method calls the module function it wraps", "a.py",
     "class Repo:\n    def staged_diff(self):\n        return staged_diff(self.root)\n", "staged_diff", 0),
    ("Python method under a decorator", "a.py",
     "class A:\n    @property\n    def size(self):\n        return size(self.items)\n", "size", 0),
    ("Python static method through its class", "a.py",
     "class T:\n    @staticmethod\n    def walk(n):\n        if n:\n            T.walk(n - 1)\n", "walk", 2),
    ("Python class method through cls", "a.py",
     "class T:\n    @classmethod\n    def walk(cls, n):\n        return cls.walk(n - 1)\n", "walk", 1),
    ("Python async method", "a.py",
     "class A:\n    async def fetch(self, url):\n        return await fetch(url)\n", "fetch", 0),
    ("Python function after a class", "a.py",
     "class A:\n    def m(self):\n        return 1\n\n\ndef walk(n):\n    return walk(n - 1)\n", "walk", 1),
    ("Python function nested in a method", "a.py",
     "class A:\n    def m(self):\n        def helper(k):\n            return helper(k - 1)\n"
     "        return helper(3)\n", "m.helper", 1),
    ("JavaScript method calls the function it wraps", "a.js",
     "class A {\n  map(f) {\n    return map(this.xs, f);\n  }\n}\n", "map", 0),
    ("TypeScript method calls the function it wraps", "a.ts",
     "class A {\n  map(f: F): X[] {\n    return map(this.xs, f);\n  }\n}\n", "map", 0),
    ("JavaScript static method through its class", "a.js",
     "class A {\n  static walk(n) {\n    return n ? A.walk(n - 1) : 0;\n  }\n}\n", "walk", 2),
    ("JavaScript field holding an arrow", "a.js",
     "class A {\n  walk = (n) => {\n    return walk(n - 1);\n  };\n}\n", "walk", 0),
    ("JavaScript object method", "a.js",
     "const helpers = {\n  format(d) {\n    return format(d);\n  },\n};\n", "format", 0),
    ("JavaScript named function expression in an object", "a.js",
     "const o = {\n  walk: function walk(n) {\n    return walk(n - 1);\n  },\n};\n", "walk", 1),
    ("JavaScript property assigned a function", "a.js",
     "res.vary = function(field) {\n  vary(this, field);\n};\n", "res.vary", 0),
    ("JavaScript generator declaration", "a.js",
     "function* walk(node) {\n  yield node;\n  for (const c of node.kids) yield* walk(c);\n}\n", "walk", 2),
    ("TypeScript arrow after JSX whose braces lizard swallows", "a.tsx",
     "const Item = ({ value, ...props }) => {\n  return (\n    <Ctx.Provider value={{ value }}>\n"
     "      <Row onClick={() => pick(value)} {...props} />\n    </Ctx.Provider>\n  )\n}\n\n"
     "const walk = (node) => {\n  return node ? walk(node.next) : 0\n}\n", "walk", 2),
    ("JavaScript function declared in a method", "a.js",
     "class A {\n  m() {\n    function walk(n) {\n      return walk(n - 1);\n    }\n    return walk;\n  }\n}\n",
     "walk", 1),
    ("Go method calls the package function", "a.go",
     "package p\n\nfunc (c *C) Walk(n int) int {\n\treturn Walk(n)\n}\n", "(c*C)Walk", 0),
]


@pytest.mark.parametrize("label,path,source,name,want", METHODS, ids=[c[0] for c in METHODS])
def test_a_method_calls_itself_only_through_its_object_or_its_class(label, path, source, name, want):
    assert _cognitive(path, source, name) == want


def _typed(header: str, body: str, footer: str = "}\n") -> str:
    return header + body + footer


_RS_SPIN = ("    fn spin(n: u32) -> u32 {{\n        if n > 0 {{ return {call}(n - 1); }}\n"
            "        0\n    }}\n")
_SWIFT_F = ("    {decl} f(_ n: Int) -> Int {{\n        if n > 0 {{ return {call}(n - 1) }}\n"
            "        return 0\n    }}\n")
_ZIG_F = "    fn f(n: u32) u32 {{\n        if (n > 0) return {call}(n - 1);\n        return 0;\n    }}\n"

# A type's name reaches the functions its body defines: `R::spin(n - 1)` in a
# Rust impl, `A.f(n - 1)` in a Swift type, `R.f(n - 1)` in a Zig container.
# lizard names none of them with their type, so the pass reads the type off the
# stream. Rust looks a bare name in an impl up outside it, as Python does for a
# method: `walk(n)` there is the free function. Swift and Zig look it up in the
# type first.
TYPE_NAMES = [  # (label, path, source, name, Sonar value)
    ("Rust associated function through its type", "a.rs",
     _typed("impl R {\n", _RS_SPIN.format(call="R::spin")), "spin", 2),
    ("Rust generic impl", "a.rs",
     _typed("impl<T: Clone> W<T> {\n", _RS_SPIN.format(call="W::spin")), "spin", 2),
    ("Rust trait impl names its type after for", "a.rs",
     _typed("impl<T> Walk<T> for R<T> where T: Copy {\n", _RS_SPIN.format(call="R::spin")),
     "spin", 2),
    ("Rust through Self", "a.rs", _typed("impl R {\n", _RS_SPIN.format(call="Self::spin")), "spin", 2),
    ("Rust another type's function", "a.rs",
     _typed("impl R {\n", _RS_SPIN.format(call="Q::spin")), "spin", 1),
    ("Rust bare name in an impl is the free function", "a.rs",
     "fn walk(n: u32) -> u32 { n }\n\nimpl R {\n    fn walk(&self, n: u32) -> u32 {\n"
     "        if n > 0 { return walk(n - 1); }\n        0\n    }\n}\n", "walk & self", 1),
    ("Rust fn nested in a method calls itself", "a.rs",
     "impl R {\n    fn a(&self) -> u32 {\n        fn walk(n: u32) -> u32 {\n"
     "            if n > 0 { return walk(n - 1); }\n            0\n        }\n        walk(3)\n"
     "    }\n}\n", "walk", 2),
    ("Rust free function after an impl", "a.rs",
     "impl R {\n    fn a(&self) -> u32 { 1 }\n}\n\nfn walk(n: u32) -> u32 {\n"
     "    if n > 0 { return walk(n - 1); }\n    0\n}\n", "walk", 2),
    ("Swift static function through its class", "a.swift",
     _typed("class A: B {\n", _SWIFT_F.format(decl="static func", call="A.f")), "f", 2),
    ("Swift generic struct", "a.swift",
     _typed("struct Box<T> {\n", _SWIFT_F.format(decl="static func", call="Box.f")), "f", 2),
    ("Swift extension", "a.swift",
     _typed("extension A {\n", _SWIFT_F.format(decl="static func", call="A.f")), "f", 2),
    ("Swift class func is a modifier, not a type", "a.swift",
     _typed("class A {\n", _SWIFT_F.format(decl="class func", call="A.f")), "f", 2),
    ("Swift bare name in a type is its method", "a.swift",
     _typed("class A {\n", _SWIFT_F.format(decl="func", call="f")), "f", 2),
    ("Swift another type's function", "a.swift",
     _typed("class A {\n", _SWIFT_F.format(decl="static func", call="B.f")), "f", 1),
    ("Zig container function through its name", "a.zig",
     _typed("const R = struct {\n", _ZIG_F.format(call="R.f"), "};\n"), "f", 2),
    ("Zig packed struct", "a.zig",
     _typed("pub const R = packed struct {\n", _ZIG_F.format(call="R.f"), "};\n"), "f", 2),
    ("Zig tagged union", "a.zig",
     _typed("const U = union(enum) {\n", _ZIG_F.format(call="U.f"), "};\n"), "f", 2),
    ("Zig another container's function", "a.zig",
     _typed("const R = struct {\n", _ZIG_F.format(call="Q.f"), "};\n"), "f", 1),
]


@pytest.mark.parametrize("label,path,source,name,want", TYPE_NAMES, ids=[c[0] for c in TYPE_NAMES])
def test_a_type_name_reaches_the_functions_its_body_defines(label, path, source, name, want):
    assert _cognitive(path, source, name) == want


# A parameter, an import or an assignment spelled like the function binds the
# name in its body, where Python and JavaScript look it up first, so a call to it
# reaches that value. Python makes the name local to the whole body wherever the
# binding stands; a JavaScript `const` or `let` shadows from the start of its
# block. A keyword argument or an attribute spelled the same way binds nothing.
BINDINGS = [  # (label, path, source, name, Sonar value)
    ("Python parameter", "a.py", "def apply(x, apply):\n    return apply(x)\n", "apply", 0),
    ("Python import", "a.py",
     "def dumps(obj):\n    from json import dumps\n    return dumps(obj)\n", "dumps", 0),
    ("Python import list", "a.py",
     "def dumps(obj):\n    from json import loads, dumps\n    return dumps(obj)\n", "dumps", 0),
    ("Python import alias", "a.py",
     "def fetch(url):\n    from requests import get as fetch\n    return fetch(url)\n", "fetch", 0),
    ("Python assignment after the call", "a.py",
     "def run(cmd):\n    out = run(cmd)\n    run = None\n    return out\n", "run", 0),
    ("Python walrus", "a.py",
     "def fetch(url):\n    if (fetch := cache.get(url)):\n        return fetch(url)\n", "fetch", 1),
    ("Python keyword argument", "a.py",
     "def dumps(obj):\n    return dumps(obj, dumps=1)\n", "dumps", 1),
    ("Python attribute", "a.py",
     "def walk(node):\n    node.walk = None\n    return walk(node.next)\n", "walk", 1),
    ("JavaScript const", "a.js",
     "function route(app) {\n  const route = app.route;\n  return route('/x');\n}\n", "route", 0),
    ("JavaScript parameter", "a.js", "function apply(x, apply) {\n  return apply(x);\n}\n", "apply", 0),
    ("Go short declaration", "a.go",
     "package p\n\nfunc walk(n int) int {\n\twalk := next\n\treturn walk(n)\n}\n", "walk", 0),
    ("Go parameter", "a.go",
     "package p\n\nfunc apply(x int, apply func(int) int) int {\n\treturn apply(x)\n}\n", "apply", 0),
    ("Rust let", "a.rs",
     "fn walk(n: u32) -> u32 {\n    let walk = |k: u32| k + 1;\n    walk(n)\n}\n", "walk", 0),
    ("Rust let mut", "a.rs",
     "fn walk(n: u32) -> u32 {\n    let mut walk = step;\n    walk(n)\n}\n", "walk", 0),
    ("Rust use", "a.rs",
     "fn symlink(src: &Path, dst: &Path) {\n    use std::os::unix::fs::symlink;\n"
     "    symlink(src, dst).unwrap();\n}\n", "symlink", 0),
    ("Rust use list", "a.rs",
     "fn escape() {\n    use super::{escape, glob};\n    assert_eq!(\"a\", escape(\"a\"));\n}\n", "escape", 0),
    ("Rust use ends at its semicolon", "a.rs",
     "fn walk(n: u32) -> u32 {\n    use std::fs;\n    if n == 0 {\n        return 0;\n    }\n"
     "    walk(n - 1)\n}\n", "walk", 2),
    ("Swift parameter", "a.swift",
     "func apply(_ x: Int, apply: (Int) -> Int) -> Int {\n    return apply(x)\n}\n", "apply", 0),
    ("Swift let", "a.swift",
     "func sort(_ xs: [Int]) -> [Int] {\n    let sort = Sorter()\n    return sort(xs)\n}\n", "sort", 0),
    ("JavaScript use is a name", "a.js",
     "function use(p) {\n  return p ? use(p.next) : 0;\n}\n", "use", 2),
    ("Java parameter is looked up apart", "K.java",
     "class K {\n    static int walk(Walker walk, int n) {\n        return walk(walk, n - 1);\n    }\n}\n",
     "K::walk", 1),
]


@pytest.mark.parametrize("label,path,source,name,want", BINDINGS, ids=[c[0] for c in BINDINGS])
def test_a_name_bound_in_the_body_hides_the_function(label, path, source, name, want):
    assert _cognitive(path, source, name) == want


ARROWS = [  # an arrow's body can be an expression, which starts after its `=>`
    ("a.ts", "const fact = (n: number): number => n ? n * fact(n - 1) : 1;\n", "fact", 2),
    ("a.js", "function outer() {\n  const go = (n) => go(n - 1);\n  return go;\n}\n", "go", 1),
]


@pytest.mark.parametrize("path,source,name,want", ARROWS)
def test_an_arrow_with_an_expression_body_calls_itself(path, source, name, want):
    assert _cognitive(path, source, name) == want
