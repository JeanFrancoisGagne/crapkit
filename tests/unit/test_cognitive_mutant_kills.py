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
