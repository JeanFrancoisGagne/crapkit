"""Hand-counted Go and Zig functions whose signatures lizard 1.24.0 misread.

lizard reads both languages with one state machine, GoLikeStates. After a
parameter list it waited for the next `{` wherever it was, and at package level
it read every `func (` as a method receiver. So a function type with no body
(`var hooks []func()`, a local `var cb func(int) error`, Zig's `const Cmp = fn
(u8) bool;`) opened a function that took the next block as its own, and a result
type with braces (`struct { usize, usize }`, Zig's `error{...}!u8`) ended the
function on its own signature line. It also split the parameter list at every
comma and named each piece by its last word, so a parameter of function type
counted its type's parameters and `v interface{}` counted nothing.

Every number below was counted by hand from the snippet above it: the Go spec
(Function types, Function declarations, Semicolons) and the Zig Language
Reference (Functions) say which words declare what.
"""
import lizard
import pytest

from crapkit.analyze import analyze_source
from crapkit.keys import bare_name
from crapkit.lizardgolike import register


def rows(path: str, source: str) -> list[tuple]:
    return [(r.long_name, r.start, r.end, r.ccn_std, r.params, r.nesting, r.cognitive)
            for r in analyze_source(path, source)]


def the_one(path: str, source: str, name: str):
    (record,) = [r for r in analyze_source(path, source) if bare_name(r.long_name) == name]
    return record


# --- Go: a function type is no function ----------------------------------------

# A package-level variable of function type, then the file's one function.
GO_VAR_FUNC_TYPE = """package b

var hooks []func()

func Add(n int) int {
\treturn n + 1
}
"""


def test_a_go_function_after_a_package_level_function_type_keeps_its_row():
    """lizard read `func()` as a method receiver and took the next line's `func`
    as the method's name, so Add had no row at all."""
    assert rows("b.go", GO_VAR_FUNC_TYPE) == [("Add n int", 5, 7, 1, 1, 0, 0)]


def test_every_go_function_type_outside_a_function_leaves_the_next_function_whole():
    """A type alias, result types, a multi-line type, a struct field on one
    line and a comment over lines all end where Go's semicolon rule ends them."""
    for head in ("type Hook = func(int) error\n",
                 "var hooks []func() (int, error)\n",
                 "var hooks []func() []int\n",
                 "var hook func() List[int]\n",
                 "var hook func() struct{}\n",
                 "var hook func(\n\ta int,\n) error\n",
                 "var cfg struct{ f func() }\n",
                 "var hook func() = nil\n",
                 "var hook func() /* a comment\nover lines */"):
        source = "package b\n\n" + head + "\nfunc Add(n int) int {\n\treturn n + 1\n}\n"

        assert [r[0] for r in rows("b.go", source)] == ["Add n int"], head


# A local variable of function type, then the function's own `if`.
GO_LOCAL_FUNC_TYPE = """package b

func Add(n int) int {
\tvar g func() int
\tif n > 0 {
\t\treturn 1
\t}
\treturn n + g()
}
"""


def test_a_go_local_function_type_leaves_its_function_the_if_below_it():
    """lizard opened a function at the local `func() int` and took the `if`
    block as its body: an anonymous row at ccn 2 and Add at ccn 1. The type
    declares a variable, so Add owns the if: ccn 2, nesting 1, cognitive 1."""
    assert rows("b.go", GO_LOCAL_FUNC_TYPE) == [("Add n int", 3, 9, 2, 1, 1, 1)]


def test_a_go_local_function_type_reads_like_any_other_local_type():
    """The same function with `var g int` is the control: every column but the
    name the type spells agrees, nloc included."""
    typed = analyze_source("b.go", GO_LOCAL_FUNC_TYPE)
    plain = analyze_source("b.go", GO_LOCAL_FUNC_TYPE.replace("func() int", "int"))

    assert [r._replace(long_name="") for r in typed] == [r._replace(long_name="") for r in plain]


def test_a_go_function_type_over_several_lines_leaves_its_lines_to_its_function():
    multi_line = GO_LOCAL_FUNC_TYPE.replace("func() int", "func(\n\t\ta int,\n\t) int")
    plain = GO_LOCAL_FUNC_TYPE.replace("func() int", "int")

    assert the_one("b.go", multi_line, "Add").nloc == the_one("b.go", plain, "Add").nloc + 2


def test_a_go_function_type_in_a_call_ends_at_its_comma():
    source = ("package b\n\nfunc Make(n int) int {\n\tc := make(chan func(), n)\n"
              "\tif n > 0 {\n\t\tclose(c)\n\t}\n\treturn n\n}\n")

    assert rows("b.go", source) == [("Make n int", 3, 9, 2, 1, 1, 1)]


# --- Go: a result type with braces ---------------------------------------------

GO_STRUCT_RESULT = """package b

func F() struct{ a int } {
\tif true {
\t}
\treturn struct{ a int }{1}
}

func G() {
}
"""


def test_a_go_function_returning_an_anonymous_struct_spans_its_body():
    """lizard took the result type's `{` for the body, so F ended on line 3."""
    assert rows("b.go", GO_STRUCT_RESULT) == [("F", 3, 7, 2, 0, 1, 1), ("G", 9, 10, 1, 0, 0, 0)]


def test_a_go_function_returning_a_function_still_opens_at_its_body():
    source = "package b\n\nfunc F() func(int) (int, error) {\n\tif true {\n\t}\n\treturn nil\n}\n"

    assert rows("b.go", source) == [("F", 3, 7, 2, 0, 1, 1)]


# --- Go: what still reads as it did ---------------------------------------------

def test_go_methods_generics_and_literals_keep_their_names_and_spans():
    source = """package b

func (s *Server) Handle(a int) error {
\tif a > 0 {
\t\treturn nil
\t}
\treturn nil
}

func Map[T any](xs []T, f func(T) T) []T {
\trun(func() {
\t\tf(xs[0])
\t})
\treturn xs
}

var handler = func(a int) {
\tif a > 0 {
\t}
}
"""
    assert rows("b.go", source) == [
        ("(s*Server)Handle a int", 3, 8, 2, 1, 1, 1),
        ("", 11, 13, 1, 0, 0, 0),
        ("Map xs [ ] T , f func T T", 10, 15, 1, 2, 0, 0),
        ("(a int)", 17, 20, 2, 0, 1, 1),
    ]


def test_a_package_level_go_literal_with_a_result_is_a_function():
    """lizard read `func(a int)` as a receiver and `error` as the method's name,
    then waited for a `(` that never came: the literal had no row and its `if`
    counted nowhere. It is the same anonymous function the literal without a
    result is."""
    source = "package b\n\nvar f = func(a int) error {\n\tif a > 0 {\n\t}\n\treturn nil\n}\n"
    struct_result = source.replace("error {", "struct{ a int } {")

    assert rows("b.go", source) == [("(a int)", 3, 7, 2, 0, 1, 1)]
    assert rows("b.go", struct_result) == [("(a int)", 3, 7, 2, 0, 1, 1)]


# --- Go: the parameter list ------------------------------------------------------

def test_a_go_parameter_whose_type_holds_braces_counts():
    """lizard names a parameter by the word its text ends with, and `v
    interface { }` ends with a brace, so Show counted 1 of its 2."""
    record = the_one("d.go", "package d\n\nfunc Show(v interface{}, w int) int {\n\treturn w\n}\n",
                     "Show")

    assert (record.params, record.long_name) == (2, "Show v interface { } , w int")


def test_a_go_parameter_of_function_type_counts_once():
    """The commas inside `func(int, string) error` separate the type's own
    parameters, not Each's. The long name keeps every token, so the key a mark
    was recorded under does not move."""
    record = the_one("d.go", "package d\n\nfunc Each(f func(int, string) error) int {\n\treturn 0\n}\n",
                     "Each")

    assert (record.params, record.long_name) == (1, "Each f func int , string error")


def test_go_parameters_in_brackets_and_grouped_names_count_as_written():
    source = ("package d\n\nfunc Mix(a, b int, m map[string][2]int, s struct{ x, y int }, "
              "fs ...func(a, b int)) int {\n\treturn a\n}\n")

    assert the_one("d.go", source, "Mix").params == 5


# --- Zig: a result type with braces ----------------------------------------------

ZIG_STRUCT_RESULT = """pub fn pair(n: usize) struct { usize, usize } {
    var low: usize = 0;
    if (n > 1) {
        low = 1;
    }
    return .{ low, n };
}
"""


def test_a_zig_function_returning_an_anonymous_struct_spans_its_body():
    """lizard took the struct's `{` for the body, so pair ended on line 1 at ccn 1."""
    assert rows("a.zig", ZIG_STRUCT_RESULT) == [("pair n : usize", 1, 7, 2, 1, 1, 1)]


def test_zig_error_set_and_tagged_union_results_are_types_too():
    error_set = ("fn open(p: []const u8) error{ NotFound, Denied }!u8 {\n"
                 "    if (p.len == 0) {\n        return error.NotFound;\n    }\n    return 1;\n}\n")
    tagged = ("fn tag(n: u8) union(enum) { a: u8, b: void } {\n"
              "    if (n > 0) {\n        return .{ .a = n };\n    }\n    return .b;\n}\n")

    assert [r[1:4] for r in rows("a.zig", error_set)] == [(1, 6, 2)]
    assert [r[1:4] for r in rows("a.zig", tagged)] == [(1, 6, 2)]


# --- Zig: a function type or a prototype is no function ---------------------------

def test_a_zig_local_function_type_leaves_its_function_the_if_below_it():
    source = """pub fn run(a: u8) u8 {
    const cb: *const fn (u8) void = &noop;
    if (a > 1) {
        cb(a);
    }
    return a;
}
"""
    assert rows("a.zig", source) == [("run a : u8", 1, 7, 2, 1, 1, 1)]


def test_a_zig_prototype_without_a_body_leaves_the_next_function_whole():
    """An `extern fn` declaration ends at its `;`. lizard took main's body as
    write's, so main had no row."""
    source = """extern fn write(fd: i32, len: usize) isize;

pub fn main() void {
    if (true) {
        _ = write(1, 2);
    }
}
"""
    assert rows("a.zig", source) == [("main", 3, 7, 2, 0, 1, 1)]


def test_zig_function_types_at_container_level_open_no_function():
    source = """const Cmp = fn (u8, u8) bool;

const V = struct {
    cb: *const fn (u8) void,
    pub fn call(self: V) void {
        if (true) {
            self.cb(1);
        }
    }
};
"""
    assert rows("a.zig", source) == [("call self : V", 5, 9, 2, 1, 1, 1)]


def test_a_zig_container_bound_to_a_type_keeps_its_functions():
    """Zig's `type` names a type. Go's reader reads the word after it as a
    type's name and skips a `struct` body whole, which here holds `get`."""
    source = "const S: type = struct {\n    pub fn get() u8 {\n        return 1;\n    }\n};\n"

    assert rows("a.zig", source) == [("get", 2, 4, 1, 0, 0, 0)]


# --- Zig: the parameter list -------------------------------------------------------

def test_a_zig_parameter_of_function_type_counts_once():
    source = ("pub fn sortWith(comptime T: type, items: []T, "
              "comptime lessThan: fn (T, T) bool) void {\n    _ = items;\n    _ = lessThan;\n}\n")
    record = the_one("a.zig", source, "sortWith")

    assert record.params == 3
    assert record.long_name == ("sortWith comptime T : type , items : [ ] T , "
                                "comptime lessThan : fn T , T bool")


# --- registration ----------------------------------------------------------------

def test_lizard_resolves_go_and_zig_to_the_corrected_readers():
    assert lizard.get_reader_for("main.go").__name__ == "CorrectedGoReader"
    assert lizard.get_reader_for("main.zig").__name__ == "CorrectedZigReader"


def test_registering_go_and_zig_leaves_every_other_reader_alone():
    register()
    assert lizard.get_reader_for("a.rs").__name__ == "CorrectedRustReader"
    assert lizard.get_reader_for("a.java").__name__ == "JavaReader"


def test_register_raises_when_lizard_resolves_something_else(monkeypatch):
    monkeypatch.setattr(lizard, "get_reader_for", lambda _name: None)
    with pytest.raises(RuntimeError, match="did not take: lizard resolves 'go' to no reader"):
        register()


# --- switch prongs and select: ccn_std and ccn_mod ----------------------------------

GO_SELECT = """package p

func Pick(c, d chan int) int {
\tselect {
\tcase x := <-c:
\t\treturn x
\tcase y := <-d:
\t\treturn y
\tdefault:
\t\treturn 0
\t}
}
"""


def columns(path: str, source: str, name: str) -> tuple:
    record = the_one(path, source, name)
    return record.ccn_std, record.ccn_mod, record.ccn, record.cognitive


def test_a_go_select_counts_once_under_the_modified_rule():
    """The Go spec calls select the switch over channel operations, and lizard's
    -m counts a switch with all its cases as one decision. Two cases and a
    default: ccn_std 1 + 2 = 3, ccn_mod 1 + 1 = 2. It read 1, because each case
    took a point off and the select added none."""
    assert columns("p.go", GO_SELECT, "Pick")[:3] == (3, 2, 2)


def test_select_is_a_switch_only_where_the_reader_says_so():
    """A shell `select` is a loop and a Python `select` is a name."""
    shell = "pick() {\n  select x in a b; do\n    echo \"$x\"\n  done\n}\n"
    python = "def pick(select):\n    select = select + 1\n    return select\n"

    for path, source, name in (("a.sh", shell, "pick"), ("a.py", python, "pick")):
        std, mod, _, _ = columns(path, source, name)
        assert mod == std, path


ZIG_SWITCH_ELSE = """pub fn pick(k: i32) i32 {
    switch (k) {
        1 => return 10,
        2 => return 20,
        else => return 0,
    }
}
"""


def test_a_zig_else_prong_is_the_default_and_counts_nothing():
    """NIST SP 500-235 sec. 4.1 counts a switch's case labels with the default
    excluded: two prongs, ccn_std 3. -m reads the switch once, ccn_mod 2. Sonar
    charges the switch +1 and nothing for its prongs, cognitive 1. It read 4, 5
    and 2: the `else =>` counted as a prong, -m added the switch without taking
    the prongs back, and the cognitive pass read the `else` as an else."""
    assert columns("a.zig", ZIG_SWITCH_ELSE, "pick") == (3, 2, 2, 1)


def test_every_zig_default_prong_spelling_is_free():
    """`_ =>` over a non-exhaustive enum and `inline else =>` reach what no
    other prong matched, the way `else =>` does."""
    for default in ("_ => return 0,", "inline else => return 0,"):
        source = ZIG_SWITCH_ELSE.replace("else => return 0,", default)

        assert columns("a.zig", source, "pick")[:3] == (3, 2, 2), default


def test_a_zig_prong_counts_once_whatever_it_matches():
    """An exhaustive switch of three prongs, one of them two items and one a
    range: 1 + 3 = 4, and -m reads 2."""
    source = """fn grade(n: u8) u8 {
    return switch (n) {
        0, 1 => 1,
        2...9 => 2,
        10...255 => 3,
    };
}
"""
    assert columns("a.zig", source, "grade")[:3] == (4, 2, 2)


# --- Zig words that decide nothing: try, ?, || ----------------------------------------

def test_a_zig_try_opens_no_nesting_level():
    """lizard's nesting extension counts `try` as a structure and never closes
    it, so three tries read nesting 3. Sonar's nesting rises inside a structure
    that holds a block; `try` holds none."""
    source = "pub fn tries() !void {\n    try first();\n    try second();\n    try third();\n}\n"
    record = the_one("a.zig", source, "tries")

    assert (record.nesting, record.cognitive) == (0, 0)


def test_a_try_inside_an_if_is_as_deep_as_the_if():
    source = ("pub fn guarded(a: bool) !void {\n    if (a) {\n        try first();\n"
              "        try second();\n    }\n}\n")

    assert the_one("a.zig", source, "guarded").nesting == 1


def test_a_zig_optional_decides_nothing():
    """`?usize` is a type and `x.?` unwraps one: neither is a conditional
    operator, so cognitive 0 and nesting 0. Both read 1."""
    typed = "pub fn indexOf(haystack: []const u8) ?usize {\n    return find(haystack, 0);\n}\n"
    unwrap = "pub fn first(p: ?*const u8) u8 {\n    return p.?.*;\n}\n"

    for source, name in ((typed, "indexOf"), (unwrap, "first")):
        record = the_one("a.zig", source, name)
        assert (record.cognitive, record.nesting, record.ccn) == (0, 0, 1), name


def test_a_zig_error_set_merge_is_no_boolean_operator():
    """Zig spells boolean or as `or`; `||` merges two error sets, a type. The
    `if` +1 and the `or` +1 are the whole cognitive score; it read 3. lizard's
    nesting extension still reads the `||` as a level, whatever the reader
    says, so the nesting column is not pinned here."""
    source = ("fn open(p: []const u8) (error{Empty} || Io)!u8 {\n"
              "    if (p.len == 0 or p[0] == 0) {\n        return error.Empty;\n    }\n    return 1;\n}\n")
    record = the_one("a.zig", source, "open")

    assert (record.ccn, record.cognitive) == (3, 2)


def test_zig_words_other_languages_nest_on_are_names_in_zig():
    """`case`, `def` and `foreach` are no Zig keywords, so a variable with one of
    those names opens nothing."""
    source = "fn names() u8 {\n    const case = 1;\n    const def = 2;\n    return case + def;\n}\n"

    assert the_one("a.zig", source, "names").nesting == 0

