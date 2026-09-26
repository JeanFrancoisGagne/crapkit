"""Swift functions read by crapkit's Swift reader, counted by hand.

Every expected row below was read off the snippet above it by hand, from The Swift
Programming Language (5.10), never off a run. A row is (long name, first line, last
line), so a test also pins the name a mark is keyed on.

lizard 1.24.0's SwiftReader reads several Swift names as declarations. Each one opened
a function that took the next brace block as its body, so the function holding the
name ran on to a later `}` and the functions after it lost their rows.
"""
import pytest

from crapkit import analyze


def _rows(source: str) -> list[tuple[str, int, int]]:
    return [(r.long_name, r.start, r.end) for r in analyze.analyze_source("case.swift", source)]


def _row(source: str, name: str):
    return next(r for r in analyze.analyze_source("case.swift", source) if r.long_name == name)


RESET = ("override func reset() {\n"
         "        if flag {\n"
         "            super.reset()\n"
         "        }\n"
         "    }\n")


# --- an init call is a call ------------------------------------------------------------------

@pytest.mark.parametrize("call", ["super.init(id: id,\n                   q: q)",
                                  "self.init(id: id,\n                  q: q)",
                                  "Base.init(id: id,\n                  q: q)",
                                  "let k: K = .init(id: id,\n                  q: q)"])
def test_an_init_call_inside_an_initializer_opens_no_function(call):
    """Initialization, Initializer Delegation: `super.init(...)` calls an initializer.
    Read as a declaration, it took reset's braces: init ran to line 12, reset had no row."""
    source = ("class K: Base {\n"
              "    init(id: Int, q: Int) {\n"
              f"        {call}\n"
              "    }\n"
              "\n"
              f"    {RESET}"
              "}\n")

    assert _rows(source) == [("init id : Int , q : Int", 2, 5), ("reset", 7, 11)]


@pytest.mark.parametrize("mark", ["?", "!"])
def test_a_failable_initializer_is_listed_as_init(mark):
    """Initialization, Failable Initializers: `init?` and `init!` declare an initializer.
    The tokenizer read `init?` as one word that is not `init`, so it had no row."""
    source = ("struct Code {\n"
              f"    init{mark}(raw: Int) {{\n"
              "        if raw < 0 {\n"
              "            return nil\n"
              "        }\n"
              "    }\n"
              "\n"
              "    func shown() -> Int {\n"
              "        return 1\n"
              "    }\n"
              "}\n")

    assert _rows(source) == [("init raw : Int", 2, 6), ("shown", 8, 10)]
    assert _row(source, "init raw : Int").ccn == 2


# --- a word spelled like an accessor or a protocol ---------------------------------------------

@pytest.mark.parametrize("use", ["return r.get()",
                                 "switch r { case .get: return 1 default: return 0 }",
                                 "let all: [Verb] = [.get, .head]",
                                 "set(&total, 1)",
                                 "willSet(1)"])
def test_an_accessor_word_used_as_a_name_opens_no_function(use):
    """Declarations, Getter-Setter Blocks: an accessor opens a block after its word.
    `r.get()`, `case .get`, `.get,` and a call to a function named set are names."""
    source = ("struct Loader {\n"
              "    func load(r: Outcome) -> Int {\n"
              f"        {use}\n"
              "    }\n"
              "\n"
              "    func reset(flag: Bool) {\n"
              "        if flag {\n"
              "            show(1)\n"
              "        }\n"
              "    }\n"
              "}\n")

    assert _rows(source) == [("load r : Outcome", 2, 4), ("reset flag : Bool", 6, 10)]


def test_a_setter_access_level_opens_no_function():
    """`private(set) var` restricts the setter; it declares none."""
    source = ("struct Loader {\n"
              "    private(set) var count = 0\n"
              "\n"
              "    func reset(flag: Bool) {\n"
              "        if flag {\n"
              "            show(1)\n"
              "        }\n"
              "    }\n"
              "}\n")

    assert _rows(source) == [("reset flag : Bool", 4, 8)]


ACCESSORS = """struct Box {
    var total: Int {
        get {
            return a
        }
        set(v) {
            if v > 0 { a = v }
        }
    }
    var watched: Int = 0 {
        willSet {
            if newValue > 1 { log() }
        }
        didSet(old) {
            if old > 1 { log() }
        }
    }
    var ready: Bool {
        get async throws {
            return true
        }
    }
    subscript(i: Int) -> Int {
        return i
    }
    deinit {
        if x { y() }
    }
}
"""


def test_every_real_accessor_keeps_its_row():
    """The same words where they declare: each accessor and observer gets its row."""
    assert _rows(ACCESSORS) == [("get", 3, 5), ("set", 6, 8), ("willSet", 11, 13),
                                ("didSet", 14, 16), ("get", 19, 21),
                                ("subscript i : Int", 23, 25), ("deinit", 26, 28)]


@pytest.mark.parametrize("use", ["return Socket(protocol: name)", "return self.protocol",
                                 "return make(protocol)"])
def test_protocol_used_as_a_name_hides_no_later_function(use):
    """Declarations, Protocol Declaration: `protocol` starts one before a name. As an
    argument label or a member it started a protocol body that never ended, and no
    function after it had a row."""
    source = ("struct Socket {\n"
              "    func open(name: String) -> Socket {\n"
              f"        {use}\n"
              "    }\n"
              "\n"
              "    func close(flag: Bool) {\n"
              "        if flag {\n"
              "            show(1)\n"
              "        }\n"
              "    }\n"
              "}\n")

    assert _rows(source) == [("open name : String", 2, 4), ("close flag : Bool", 6, 10)]


def test_a_protocol_declaration_still_lists_no_requirement():
    """A requirement has no body, so a protocol's `func` and `{ get set }` are no rows."""
    source = ("protocol Named {\n"
              "    var name: String { get set }\n"
              "    func rename(to: String)\n"
              "}\n"
              "func after() -> Int {\n"
              "    return 1\n"
              "}\n")

    assert _rows(source) == [("after", 5, 7)]


# --- a `type` word, a `#` construct, a closure after a comma -----------------------------------

def test_a_property_named_type_keeps_the_brace_after_it():
    """Swift declares no type with the word `type`; `return type` returns a property.
    Go's rule skipped the two tokens after it, one of them value's `}`."""
    source = ("struct Trust {\n"
              "    let type: Int\n"
              "\n"
              "    func value() -> Int {\n"
              "        return type\n"
              "    }\n"
              "\n"
              "    func check(flag: Bool) {\n"
              "        if flag {\n"
              "            show(1)\n"
              "        }\n"
              "    }\n"
              "}\n")

    assert _rows(source) == [("value", 4, 6), ("check flag : Bool", 8, 12)]


def test_a_literal_default_keeps_the_rest_of_its_signature():
    """Expressions, Literal Expression: `#fileID` is a literal. Read as a preprocessor
    line it took the `)` and `{` after it, and no function from there on had a row."""
    source = ("func plain() -> Int {\n"
              "    return 1\n"
              "}\n"
              "\n"
              "func logged(file: String = #fileID, line: UInt = #line) -> String {\n"
              "    return file\n"
              "}\n"
              "\n"
              "func after() -> Int {\n"
              "    return 2\n"
              "}\n")

    assert _rows(source) == [("plain", 1, 3),
                             ("logged file : String = #fileID , line : UInt = #line", 5, 7),
                             ("after", 9, 11)]


@pytest.mark.parametrize("condition", ["#available(iOS 13, *)", "#unavailable(iOS 13)",
                                       "check(#selector(tap), #\"a\"b\"#)"])
def test_a_hash_construct_in_a_body_keeps_the_brace_after_it(condition):
    """Statements, Availability Condition: the if's `{` follows `#available(...)`. It was
    lost with the rest of the line, so the function ended at the if's `}`, and its
    decision was lost with it."""
    source = ("func availableIf() {\n"
              f"    if {condition} {{\n"
              "        show(1)\n"
              "    }\n"
              "    show(2)\n"
              "    show(3)\n"
              "}\n")

    assert _rows(source) == [("availableIf", 1, 7)]
    assert _row(source, "availableIf").ccn == 2


def test_a_compiler_directive_line_still_decides_nothing():
    """A `#if` condition picks what compiles, not what runs: the `&&` in it costs nothing."""
    source = ("func after(a: Bool) -> Int {\n"
              "#if DEBUG && TRACE\n"
              "    log()\n"
              "#endif\n"
              "    return 1\n"
              "}\n")

    assert [(r.start, r.end, r.ccn) for r in analyze.analyze_source("case.swift", source)] == [(1, 6, 1)]


def test_a_closure_after_a_comma_ends_where_its_brace_closes():
    """A closure passed after a `,` is an argument. Its `{` was read as a declared name,
    so its `}` ended the function one line early."""
    source = ("func each(v: [Int]) {\n"
              "    run(1, { x in\n"
              "        show(x)\n"
              "    })\n"
              "}\n")

    assert _rows(source) == [("each v : [ Int ]", 1, 5)]
