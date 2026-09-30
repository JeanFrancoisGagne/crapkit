"""The cognitive pass's edges the weekly readers run left unchecked, each worked
by hand from lizardcognitive's rules.

Swift: a trailing closure takes the next parameter without a default, past
those with one, and the call fits only when every parameter after it has a
default; a call's labels are read as the call spells them, and a label spelled
like a keyword (`for:`) is renamed in the stream, in the call and in the
declaration alike. A brace language's else-if carries a header, which is no
body; a body ends with its statement at any bracket depth, and only the `while`
of a do-while is its tail. A break is labeled only by a label, spelled with a
digit or a leading underscore too. A word a type's header reads (`trait`,
`impl`, `class`, a Zig container) opens a type only where a type is declared,
and a type with no name gives its functions no name to be called through.

A conditional operator's arms sit one level below the conditionals open around
them and end with their bracket, their statement, or a line the next line does
not carry on; `??`, and a `?` that a member access or a type spells, waits for
no `:`. A Python `match` opens a match statement only where it starts one. A
JavaScript function is a method only where a class body or an object literal
declares it.
"""
import pytest

from crapkit.analyze import analyze_source


def _rows(path: str, source: str) -> list[tuple]:
    """(start line, cognitive, nesting) of each function, in lizard's order."""
    return [(r.start, r.cognitive, r.nesting) for r in analyze_source(path, source, note=False)]


# --- Swift calls ---------------------------------------------------------------------------------

@pytest.mark.parametrize("source, cognitive", [
    # the closure takes body, and y after it has no default: no call to f
    ("func f(x: Int = 0, _ body: () -> Void, y: Int) {\n    f(x: 1) { }\n}\n", 0),
    # the closure takes body, the last parameter, which has a default
    ("func f(x: Int, body: () -> Void = {}) {\n    f(x: 1) { }\n}\n", 1),
    # the closure alone takes a labeled parameter
    ("func f(body: () -> Void) {\n    f { }\n}\n", 1),
    # the closure alone leaves a second required parameter unpassed
    ("func f(_ a: Int, _ body: () -> Void) {\n    f { }\n}\n", 0),
    # a closed call that passes over a parameter with no default after its last argument
    ("func f(a: Int = 0, b: Int) {\n    f(a: 1)\n}\n", 0),
])
def test_a_swift_call_fits_only_when_every_parameter_it_leaves_has_a_default(source, cognitive):
    assert _rows("a.swift", source) == [(1, cognitive, 0)]


def test_a_trailing_closure_to_a_name_the_body_binds_calls_that_binding():
    """`let run = ...` shadows the function, so `run { }` calls the closure."""
    source = ("func run(_ body: () -> Void) {\n    let run = { (b: () -> Void) in b() }\n"
              "    run { }\n}\n")

    assert _rows("a.swift", source) == [(1, 0, 0)]


def test_a_closure_argument_whose_body_opens_with_a_closure_is_one_argument():
    """The `{` inside the argument's closure passes nothing more to f."""
    source = "func f(body: () -> Void) {\n    f(body: { { }() })\n}\n"

    assert _rows("a.swift", source) == [(1, 1, 0)]


@pytest.mark.parametrize("source", [
    "func adapt(_ r: Int, for s: Int) {\n    adapt(r, for: s)\n}\n",
    "func find(for s: Int) {\n    find(for: s - 1)\n}\n",
])
def test_a_label_spelled_like_a_keyword_matches_the_call_that_spells_it(source):
    """The parameters are read off the stream, where `for:` reaches the pass
    renamed in the call and in the declaration alike; lizard's own list keeps
    the declaration's `for`."""
    assert _rows("a.swift", source) == [(1, 1, 0)]


# --- Swift and Rust headers without parentheses ---------------------------------------------------

@pytest.mark.parametrize("source, cognitive", [
    # a closure after the if's block, on the next line, is no body: its if pays 1
    ("func f(a: Bool, xs: [Int]) {\n    if a {\n    }\n    xs.forEach {\n        if a { }\n    }\n}\n", 2),
    # the else after the block goes on with the if and leaves nothing waiting
    ("func f(a: Bool, xs: [Int]) {\n    if a { } else { }\n    xs.forEach { if a { } }\n}\n", 3),
])
def test_a_block_after_a_structure_s_block_waits_for_nothing(source, cognitive):
    assert _rows("a.swift", source) == [(1, cognitive, 1)]


def test_a_match_in_an_if_header_leaves_one_structure_waiting():
    """The `{` after the match's block is the if's body, and the block after the
    if nests nothing."""
    source = "fn f(x: u8, a: bool) {\n    if match x { _ => true } {\n    }\n    {\n        if a { }\n    }\n}\n"

    assert _rows("a.rs", source) == [(1, 3, 1)]


def test_a_block_called_in_an_if_header_leaves_the_if_waiting_for_its_body():
    """`{ g }(1)` is the condition, and the inner if sits in the if's body."""
    source = "fn f(g: fn(u8) -> bool, a: bool) {\n    if { g }(1) {\n        if a { }\n    }\n}\n"

    assert _rows("a.rs", source) == [(1, 3, 2)]


# --- bodies without braces ---------------------------------------------------------------------

@pytest.mark.parametrize("source, row", [
    # a conditional in an else-if's header sits at the if's level
    ("void f(int a, int b, int c, int d) {\n    if (a) { x(); } else if (b ? c : d) { y(); }\n}\n", (1, 3, 1)),
    ("void f(int a, int b, int c, int d) {\n    if (a) x(); else if (b ? c : d) y();\n}\n", (1, 3, 1)),
    # the last else ends the else-if's body and keeps the for's: the while pays 3
    ("void f(int a, int b) {\n    for (;;) if (a) x(); else if (b) y(); else while (b) z();\n}\n",
     (1, 8, 3)),
])
def test_an_else_if_s_header_is_no_body(source, row):
    assert _rows("a.c", source) == [row]


@pytest.mark.parametrize("source, row", [
    ("void f(int a) {\n    { { { { if (a) x(); else while (a) y(); } } } }\n}\n", (1, 4, 2)),
    ("void f(int a) {\n    { { { { do x(); while (a); } } } }\n}\n", (1, 1, 1)),
])
def test_a_body_ends_with_its_statement_at_any_bracket_depth(source, row):
    """Five brackets deep: the else ends the if's body, and the while after a
    do's statement is its tail."""
    assert _rows("a.c", source) == [row]


@pytest.mark.parametrize("path, source, row", [
    ("a.c", "void f(int a) {\n    if (a) x();\n    while (a) y();\n}\n", (1, 2, 1)),
    ("A.java", "class A {\n  void f(boolean a) {\n    if (a) x();\n    while (a) y();\n  }\n}\n",
     (2, 2, 1)),
])
def test_a_while_after_a_statement_that_is_no_do_s_ends_the_body(path, source, row):
    assert _rows(path, source) == [row]


def test_a_do_while_in_a_body_keeps_the_body_to_its_condition():
    """The conditional in the do-while's condition sits in the for's body."""
    source = "void f(int a, int b, int c) {\n    for (;;) do x(); while (a ? b : c);\n}\n"

    assert _rows("a.c", source) == [(1, 5, 2)]


def test_a_zig_payload_closes_and_a_comma_ends_the_prong_s_body():
    """Each prong's if sits in the switch alone: 1 + 2 + 2."""
    source = ("fn f(n: u8, opt: ?u8) void {\n    switch (n) {\n        0 => if (opt) |v| g(v),\n"
              "        1 => if (opt) |w| g(w),\n        else => {},\n    }\n}\n")

    assert _rows("a.zig", source) == [(1, 5, 2)]


@pytest.mark.parametrize("line", ["i++", "i--"])
def test_a_javascript_line_ending_in_an_increment_ends_the_statement(line):
    source = f"function f(a, i) {{\n  if (a) {line}\n  if (a) x()\n}}\n"

    assert _rows("a.js", source) == [(1, 2, 1)]


# --- jumps ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("source, cognitive", [
    ("fun f(xs: List<Int>) {\n    for (x in xs) { if (x > 0) { break } }\n}\n", 3),
    ("fun f(a: Int?) {\n    while (true) { g(a ?: break) }\n}\n", 1),
])
def test_a_break_before_a_closing_bracket_jumps_to_no_label(source, cognitive):
    assert [row[1] for row in _rows("a.kt", source)] == [cognitive]


@pytest.mark.parametrize("path, source", [
    ("A.java", "class A {\n  void f(int[] a) {\n    a1: for (int x : a) { for (int y : a) { break a1; } }\n  }\n}\n"),
    ("A.java", "class A {\n  void f(int[] a) {\n    _a: for (int x : a) { for (int y : a) { break _a; } }\n  }\n}\n"),
    ("a.js", "function f(a) {\n  _outer: for (const x of a) {\n    for (const y of a) { continue _outer; }\n  }\n}\n"),
])
def test_a_label_with_a_digit_or_a_leading_underscore_is_a_label(path, source):
    """Two loops, 1 + 2, and the labeled jump, +1."""
    assert [row[1] for row in _rows(path, source)] == [4]


def test_a_jump_s_label_is_only_the_token_after_it():
    """`g` after the unlabeled break's `;` is no label."""
    source = "function f(a, b) {\n  while (a) { if (b) break; g(); }\n}\n"

    assert _rows("a.js", source) == [(1, 3, 2)]


@pytest.mark.parametrize("path, source, cognitive", [
    ("a.py", "def goto(n):\n    return goto(n - 1)\n", 1),
    ("a.js", "function goto(n) {\n  return goto(n - 1);\n}\n", 1),
    ("a.ps1", "function f {\n    goto\n}\n", 0),
])
def test_goto_is_a_name_where_the_language_has_no_goto(path, source, cognitive):
    assert [row[1] for row in _rows(path, source)] == [cognitive]


# --- the type a function is defined in -------------------------------------------------------------

def test_a_class_key_names_no_class():
    """`{class: 1}` opens no class, so the block after it is a block, and the
    arrow assigned in it calls itself."""
    source = "const o = {class: 1};\nif (o) {\n  walk = (n) => walk(n - 1);\n}\n"

    assert _rows("a.js", source) == [(3, 1, 0)]


@pytest.mark.parametrize("call, cognitive", [("walk(n - 1)", 0), ("Walk::walk(n - 1)", 1)])
def test_a_trait_s_function_is_a_method_of_the_trait(call, cognitive):
    """A bare call in a method reaches a free function; a call through the
    trait's name reaches the method."""
    source = f"trait Walk {{\n    fn walk(n: u32) -> u32 {{\n        {call}\n    }}\n}}\n"

    assert _rows("a.rs", source) == [(2, cognitive, 0)]


def test_an_impl_in_a_return_type_opens_no_type():
    """`-> impl Iterator` is a return type, so step, in walk's body, is no
    method and calls itself."""
    source = ("fn walk() -> impl Iterator<Item = u32> {\n    fn step(n: u32) -> u32 { step(n - 1) }\n"
              "    std::iter::empty()\n}\n")

    assert _rows("a.rs", source) == [(2, 1, 0), (1, 0, 0)]


@pytest.mark.parametrize("other", ["Other", "XXXX"])
def test_an_impl_for_a_type_with_no_name_is_reached_through_no_type_name(other):
    """`impl Walk for ()` names no type, so a call through any type's name is
    that type's function. XXXX is the spelling a mutation tool gives an empty
    name; a type spelled so is another type all the same."""
    source = f"impl Walk for () {{\n    fn walk(n: u32) {{ {other}::walk(n - 1) }}\n}}\n"

    assert _rows("a.rs", source) == [(2, 0, 0)]


@pytest.mark.parametrize("before, cognitive", [
    ("class A {\n    class func f() {\n", [0, 0, 0]),
    ("func f(file: File) {\n    let e = file.extension\n    if e.isEmpty {\n", [0, 1, 0]),
])
def test_a_swift_type_word_that_declares_no_type_opens_no_type(before, cognitive):
    """`class func` declares a method and `file.extension` names a member, so
    the block after them is a block: g in it overloads the g below, which takes
    the call too, and the call is not g's own (the first row)."""
    source = f"{before}        func g(_ n: Int) {{ g(n - 1) }}\n    }}\n}}\nfunc g(_ n: Int, _ m: Int = 0) {{ }}\n"

    assert [row[1] for row in _rows("a.swift", source)] == cognitive


@pytest.mark.parametrize("source", [
    # a field's type: the container is no declaration's value, so it has no name
    "const S = struct {\n    inner: struct {\n        fn walk(n: u8) u8 {\n            return inner.walk(n - 1);\n"
    "        }\n    },\n};\n",
    # a container a function returns has no name
    "fn Make() type {\n    return struct {\n        fn walk(n: u8) u8 {\n            return XXXX.walk(n - 1);\n"
    "        }\n    };\n}\n",
])
def test_a_zig_container_that_is_no_declaration_s_value_has_no_name(source):
    assert _rows("a.zig", source)[0][1:] == (0, 0)


# --- the arms of a conditional operator ------------------------------------------------------------

def _nesting(path: str, source: str) -> list[int]:
    return [row[2] for row in _rows(path, source)]


@pytest.mark.parametrize("path, source, nesting", [
    # each group's arms close with its bracket: two groups side by side reach 1
    ("A.java", "class A {\n  int f(Object a, Object b) {\n    return (a == null ? 0 : 1) ^ (b == null ? 0 : 1);\n"
     "  }\n}\n", 1),
    # a conditional in the middle arm's group reaches 2
    ("a.js", "function f(a, b) {\n  return a ? (b ? 1 : 2) : 3;\n}\n", 2),
    # a JavaScript line the line above does not carry on ends the arms before it
    ("a.js", "function f(a, b) {\n  x = a ? 1 : 2\n  y = b ? 3 : 4\n}\n", 1),
    # `a ?? b` waits for no `:`, so the `:` of a key after it opens no arms
    ("a.js", "function f(a, b) {\n  return { k: a ?? b, j: 1 };\n}\n", 0),
    # `a ?? b` in an arm leaves the conditional around it waiting for its `:`
    ("a.js", "function f(a, b, c) {\n  return c ? a ?? b : 0;\n}\n", 1),
    # a `?` of `a?.B` or `int?` in an arm leaves the conditional around it waiting too
    ("a.cs", "class A {\n  int F(bool c, A a) {\n    x = c ? a?.B : 0;\n  }\n}\n", 1),
    ("a.cs", "class A {\n  int F(bool c, object a) {\n    x = c ? (int?)a : 0;\n  }\n}\n", 1),
    # a `?` read in a type waits no longer than its statement, and its bracket
    ("a.cs", "class A {\n  int F(bool c) {\n    int? x = null; return c ? 1 : 2;\n  }\n}\n", 1),
    ("a.cs", "class A {\n  int F(int? a, bool c) {\n    x = c ? 1 : 2;\n  }\n}\n", 1),
])
def test_a_conditional_s_arms_reach_one_level_below_the_conditionals_open(path, source, nesting):
    assert _nesting(path, source) == [nesting]


def test_a_statement_end_inside_a_conditional_type_s_object_leaves_the_file_readable():
    """The `;` inside `{ a: number; b: number }` ends no conditional outside
    the braces."""
    source = ("type A<T> = T extends string ? { a: number; b: number } : never;\n"
              "function f(a: boolean) {\n  if (a) { }\n}\n")

    assert _rows("a.ts", source) == [(2, 1, 1)]


# --- more bodies -----------------------------------------------------------------------------------

@pytest.mark.parametrize("path, source, row", [
    # a catch inside parentheses ends with them: the if after it nests nothing
    ("a.zig", "fn f(a: usize, c: bool) usize {\n    const x = (g(a) catch 0) + 1;\n    if (c) return x;\n"
     "    return 0;\n}\n", (1, 2, 1)),
    # a Zig loop's continue expression is a header: the if in it sits at the loop's level
    ("a.zig", "fn f(n: u8, a: bool) void {\n    var i: u8 = 0;\n    while (i < n) : (i += if (a) 1 else 2) {}\n}\n",
     (1, 3, 1)),
    # constexpr is a header word: the conditional in the header sits at the if's level
    ("a.cpp", "void f() {\n    if constexpr (A ? B : C) { x(); }\n}\n", (1, 2, 1)),
    # a do's body starts at once: a group that opens it is the body, not a header
    ("a.c", "void f(int a, int b) {\n    do (a ? g : h)(); while (b);\n}\n", (1, 3, 2)),
    # a structure in a macro's brackets waits for no block after them
    ("a.rs", "fn f(x: Option<u8>, y: bool) {\n    let a = matches!(x, Some(_) if y);\n"
     "    let b = g(|| { if y { } });\n}\n", (1, 2, 1)),
])
def test_a_body_or_a_header_ends_where_its_brackets_do(path, source, row):
    assert _rows(path, source) == [row]


def test_a_guard_s_else_is_the_guard_s_only():
    """The if's else after a guard pays its +1."""
    source = "func f(a: Int?, b: Bool) {\n    guard let x = a else { return }\n    if b { } else { }\n}\n"

    assert _rows("a.swift", source) == [(1, 3, 1)]


# --- Python match ----------------------------------------------------------------------------------

def test_a_match_that_starts_no_statement_opens_no_match():
    source = "def f(match):\n    if match is not None:\n        return 1\n"

    assert _rows("a.py", source) == [(1, 1, 1)]


def test_a_function_named_match_calls_itself():
    assert _rows("a.py", "def match(n):\n    return match(n - 1)\n") == [(1, 1, 0)]


# --- JavaScript names ------------------------------------------------------------------------------

def test_a_name_the_window_before_the_function_does_not_hold_reads_as_a_function():
    """Past eight tokens back, lizard's name reads as a function's, which a
    bare call reaches."""
    source = "const walk = async <T extends Record<string, unknown>>(a: T, b: T): Promise<void> => walk(a, b);\n"

    assert _rows("a.ts", source) == [(1, 1, 0)]


def test_a_function_declared_in_a_method_is_reached_through_no_class_name():
    source = "class A {\n  m() {\n    function walk(n) {\n      return A.walk(n - 1);\n    }\n  }\n}\n"

    assert _rows("a.js", source) == [(3, 0, 0), (2, 0, 0)]


@pytest.mark.parametrize("other", ["Other", "XXXX"])
def test_an_object_literal_s_method_is_reached_through_no_class_name(other):
    """An object literal names no class; XXXX, as above, is another name."""
    source = f"const o = {{\n  walk(n) {{\n    return {other}.walk(n - 1);\n  }},\n}};\n"

    assert _rows("a.js", source) == [(2, 0, 0)]


def test_a_generator_method_is_a_method():
    """`*walk(n) {` in a class body is a method, so a bare call reaches another walk."""
    assert _rows("a.js", "class A {\n  *walk(n) {\n    yield* walk(n - 1);\n  }\n}\n") == [(2, 0, 0)]


@pytest.mark.parametrize("before", ["if (a) {\n} else {\n", "const c = x.class\nif (c) {\n"])
def test_a_block_after_a_word_or_a_class_member_is_no_class_body(before):
    """The arrow assigned in the block is no class field, so it calls itself."""
    assert _rows("a.js", f"{before}  walk = (n) => walk(n - 1);\n}}\n") == [(3, 1, 0)]


# --- calls and their arguments ---------------------------------------------------------------------

def test_a_java_call_that_passes_fewer_arguments_than_the_method_requires_is_another_s():
    source = "class A {\n  int f(int a, int b) {\n    return f(a);\n  }\n}\n"

    assert _rows("A.java", source) == [(2, 0, 0)]


@pytest.mark.parametrize("path, source", [
    ("a.py", "def f(a, b, c):\n    return a and a_b(b and c)\n"),
    ("a.py", "def f(a, b, c):\n    return a and _check(b and c)\n"),
    ("a.js", "function f(a, b, c) {\n  return a && $(b && c);\n}\n"),
    ("a.js", "function f(a, b, c) {\n  return a && g(a)(b && c);\n}\n"),
    ("a.js", "function f(a, b, c, fs) {\n  return a && fs[0](b && c);\n}\n"),
])
def test_a_call_s_arguments_hold_a_sequence_of_their_own(path, source):
    """A bracket after a name, a call or an index is a call: the `&&` inside it
    starts a sequence, 1 + 1."""
    assert _rows(path, source) == [(1, 2, 0)]


@pytest.mark.parametrize("line, cognitive", [
    # a group compared is an operand, and its first operator pays
    ("if ($x -and $c -eq ($a -and $b)) { }", 3),
    # a group after -and continues its sequence
    ("if ($a -and ($b -and $c)) { }", 2),
    # a parameter with a digit, -v2, is no operator: the group after it is an argument
    ("if ($y -and (Get-Thing -v2 ($a -and $b))) { }", 3),
])
def test_a_powershell_operator_binds_the_group_beside_it(line, cognitive):
    assert _rows("a.ps1", f"function f {{\n    {line}\n}}\n") == [(1, cognitive, 1)]


@pytest.mark.parametrize("prefix, cognitive", [("a::f(n - 1)", 0), ("b::f(n - 1)", 1)])
def test_a_cpp_function_is_called_through_its_own_qualifier_alone(prefix, cognitive):
    """In a::b::f, `b::f` is f and `a::f` is another function."""
    assert _rows("a.cpp", f"int a::b::f(int n) {{\n    return {prefix};\n}}\n") == [(1, cognitive, 0)]


@pytest.mark.parametrize("path, source, cognitive", [
    # a label is no parameter's name: `find(find:)` calls the function
    ("a.swift", "func find(find x: Int) {\n    find(find: x - 1)\n}\n", 1),
    # a parameter named like the function hides it
    ("a.swift", "func apply(apply: () -> Void) {\n    apply(apply: {})\n}\n", 0),
    # a default holding a `:` is no type
    ("a.py", "def walk(walk={\"a\": 1}):\n    return walk()\n", 0),
    # a `*` names no parameter
    ("a.py", "def f(a, *, b):\n    return f(a, b=b)\n", 1),
])
def test_a_parameter_s_name_is_the_word_before_its_type(path, source, cognitive):
    assert _rows(path, source) == [(1, cognitive, 0)]


def test_a_shell_function_on_one_line_calls_itself():
    assert _rows("a.sh", "f() { f; }\n") == [(1, 1, 0)]


# --- where a signature ends ------------------------------------------------------------------------

def test_a_python_signature_s_default_costs_nothing():
    assert _rows("a.py", "def f(a=1 if x else 2):\n    return a\n") == [(1, 0, 0)]


def test_a_rust_for_binder_in_a_body_s_type_is_no_loop():
    assert _rows("a.rs", "fn f() {\n    let g: &dyn for<'a> Fn(&'a u8) = &|_| {};\n}\n") == [(1, 0, 0)]


def test_a_zig_return_type_s_if_ends_at_the_body():
    """The if in the return type pays 1 and its else 1; the body's if sits at level 1."""
    assert _rows("a.zig", "fn f(x: bool) if (A) u8 else u16 {\n    if (x) {}\n}\n") == [(1, 3, 1)]


# --- lines -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("comment", ["// done", "/* done */"])
def test_a_comment_after_a_break_is_no_label(comment):
    source = f"fun f(xs: List<Int>) {{\n    for (x in xs) {{\n        break {comment}\n    }}\n}}\n"

    assert _rows("a.kt", source) == [(1, 1, 1)]


def test_a_backslash_before_trailing_blanks_carries_the_sequence_on():
    """gcc reads a backslash, blanks and a line break as a continuation."""
    source = "int f(int a, int b, int c) {\n    return a && \\  \n        b && c;\n}\n"

    assert _rows("a.c", source) == [(1, 1, 0)]


def test_a_class_indented_by_one_blank_holds_its_methods():
    """f is A's method, so the bare call reaches another f."""
    assert _rows("a.py", "class A:\n def f(self, n):\n  return f(n - 1)\n") == [(2, 0, 0)]


# --- Python blocks ---------------------------------------------------------------------------------

@pytest.mark.parametrize("source, row", [
    # the match after the if pays 1 of its own
    ("def f(a, x):\n    if a:\n        pass\n    match x:\n        case 1:\n            pass\n", (1, 2, 1)),
    # the match in the if pays 1 and the level it sits in
    ("def f(a, x):\n    if a:\n        match x:\n            case 1:\n                pass\n", (1, 3, 2)),
])
def test_a_match_statement_pays_its_nesting_on_top_of_the_total(source, row):
    assert _rows("a.py", source) == [row]


def test_a_comprehension_s_level_outlasts_a_call_inside_it():
    """The if after `g(xs)` sits in the comprehension: for 1, if 1 + 1."""
    assert _rows("a.py", "def f(xs, a):\n    return [x for x in g(xs) if a]\n") == [(1, 3, 1)]
