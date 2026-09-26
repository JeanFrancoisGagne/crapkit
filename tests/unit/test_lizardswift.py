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


# --- decisions: what adds 1 to ccn, and what does not --------------------------------------------
#
# NIST SP 500-235 sec. 4.1: v(G) = 1 + the binary decisions. A switch adds one per
# case-labeled statement in ccn_std; ccn_mod counts the switch once.

def _counts(source: str) -> tuple:
    row = analyze.analyze_source("case.swift", source)[0]
    return row.ccn_std, row.ccn_mod, row.ccn, row.cognitive, row.nesting


@pytest.mark.parametrize("condition", ["if case let .failure(e) = r {",
                                       "if let a = r.value, case .failure = r {",
                                       "while case let e? = next() {",
                                       "for case let e? in r.items {"])
def test_a_case_pattern_in_a_condition_is_no_switch_case(condition):
    """Statements, If Statement and Case pattern: `if case let` is one condition of
    one if. The `case` read as a switch case: ccn_std 3 and a second nesting level."""
    source = f"func g(r: R) {{\n    {condition}\n        show(1)\n    }}\n}}\n"

    assert _counts(source)[:3] == (2, 2, 2)
    assert _counts(source)[4] == 1


def test_a_case_pattern_in_a_guard_is_no_switch_case():
    source = "func g(r: R) {\n    guard case .failure = r else {\n        return\n    }\n}\n"

    assert _counts(source)[:3] == (2, 2, 2)


def test_a_switch_case_still_counts_one_each():
    source = ("func pick(k: Int) -> Int {\n    switch k {\n    case 1:\n        return 10\n"
              "    case 2:\n        return 20\n    default:\n        return 0\n    }\n}\n")

    assert _counts(source)[:3] == (3, 2, 2)


@pytest.mark.parametrize("expression,decisions", [("a ?? 0", 1), ("a ?? b ?? 0", 2)])
def test_each_nil_coalescing_operator_is_one_decision(expression, decisions):
    """Basic Operators, Nil-Coalescing Operator: `a ?? b` picks one of two values."""
    source = f"func f(a: Int?, b: Int?) -> Int {{\n    return {expression}\n}}\n"

    assert _counts(source)[:3] == (1 + decisions,) * 3


@pytest.mark.parametrize("label", ["for", "while", "if", "catch", "guard", "case"])
def test_a_keyword_argument_label_is_no_structure(label):
    """Functions, Specifying Argument Labels: `for name` is a label and a parameter name.
    Read as a loop it cost ccn 1, cognitive 1 and a nesting level. The long name, and
    with it the ratchet key, stays as lizard spelled it."""
    source = f"func value({label} name: String) -> String {{\n    return name\n}}\n"

    assert _counts(source) == (1, 1, 1, 0, 0)
    assert _rows(source) == [(f"value {label} name : String", 1, 3)]


def test_a_keyword_label_after_a_line_break_is_no_structure():
    source = "func f(a: Int) {\n    run(a,\n        for: a)\n    run(\n        while: a)\n}\n"

    assert _counts(source) == (1, 1, 1, 0, 0)


@pytest.mark.parametrize("mark", ["done: (() -> Void)? = nil", "error: (any Error)?", "all: [Int]?",
                                  "seen: Set<Int>?"])
def test_an_optional_type_mark_is_no_decision(mark):
    """Types, Optional Type: `(() -> Void)?` is a type. A `?` right after `)`, `]` or `>`
    read as a conditional operator: ccn 2, cognitive 1, nesting 1."""
    source = f"func optionalMark({mark}) {{\n    show(1)\n}}\n"

    assert _counts(source) == (1, 1, 1, 0, 0)
    assert _rows(source)[0][0] == "optionalMark " + " ".join(
        mark.replace("(", " ").replace(")", " ").replace("[", " [ ").replace("]", " ] ")
        .replace("<", " < ").replace(">", " > ").replace(":", " :").replace("- >", "->").split())


@pytest.mark.parametrize("chain", ["f()?.g()", "a[0]?.b", "{ $0 }()?.b"])
def test_an_optional_chain_is_no_decision(chain):
    """Optional Chaining: `f()?.g()` calls g only when f returned a value, which the
    function does not branch on."""
    source = f"func chain() {{\n    let x = {chain}\n}}\n"

    assert _counts(source)[:3] == (1, 1, 1)


@pytest.mark.parametrize("ternary", ["(a > b) ? 1 : 2", "f(a) ? 1 : 2", "a > b\n        ? 1\n        : 2"])
def test_a_ternary_after_a_bracket_is_still_a_decision(ternary):
    """Swift writes the conditional operator with whitespace on both sides of `?`, which
    is what tells it from an optional mark glued to what it follows."""
    source = f"func pick(a: Int, b: Int) -> Int {{\n    return {ternary}\n}}\n"

    assert _counts(source)[:3] == (2, 2, 2)


# --- the parameter count, and nesting ----------------------------------------------------------

@pytest.mark.parametrize("parameters,count", [("pair: (Int, Int)", 1),
                                              ("done: (Int, String) -> Void", 1),
                                              ("r: Result<URLRequest, any Error>", 1),
                                              ("v: [Int] = [1, 2], d: [String: Int] = [\"a\": 1, \"b\": 2]", 2),
                                              ("s: Set<Set<Int>>, n: Int", 2),
                                              ("a: Int, b: Int", 2)])
def test_a_comma_inside_one_parameter_counts_no_parameter(parameters, count):
    """Functions, Function Parameters: `pair: (Int, Int)` is one parameter of tuple type.
    Every comma in the list counted one. The long name keeps each comma."""
    source = f"func f({parameters}) {{\n    show(1)\n}}\n"
    row = analyze.analyze_source("case.swift", source)[0]

    assert row.params == count
    assert row.long_name.count(",") == parameters.count(",")


@pytest.mark.parametrize("parameters", ["a: Bool = 1 < 2, b: Int",
                                        "a: Int = 1 << 2, b: Int",
                                        "a: Int = y >> 1, b: (Int, Int)",
                                        "a: Bool = x > 0, b: (Int, Int)",
                                        "by less: (Int, Int) -> Bool = { $0 < $1 }, limit: Int",
                                        "a: Bool = x>0, b: (Int, Int)",
                                        "by less: (Int, Int) -> Bool = { $0<$1 }, limit: Int",
                                        "a: Bool = x >= 0, b: (Int, Int)",
                                        "a: Set<Int> = Set<Int>(), b: (Int, Int)",
                                        "a: [String: Int] = Dictionary<String, Int>(), b: Int",
                                        "a: Result<Int, Error >, b: Int",
                                        "a: () -> Result<\n        Int,\n        Error,\n    > = { .success(1) },"
                                        "\n    b: (Int, Int)"])
def test_a_comparison_in_a_default_value_opens_no_bracket(parameters):
    """Lexical Structure, Operators: `1 < 2` has whitespace on both sides of `<`, so it
    compares, while `Set<Int>` opens a generic argument clause. Every `<` and `>` in the
    list counted as a bracket, so `1 < 2, b` read as one parameter and `x > 0` closed a
    bracket no `<` had opened. A `>` closes a clause only when a `<` is open, spaced or
    on its own line, and a `}` closes the `<` of `{ $0<$1 }` that no `>` did."""
    source = f"func f({parameters}) {{\n    show(1)\n}}\n"
    row = analyze.analyze_source("case.swift", source)[0]

    assert row.params == 2
    assert row.long_name.count("<") == parameters.count("<")


@pytest.mark.parametrize("body,depth",[("try first()\n    try second()", 0),
                                        ("try? first()\n    try! second()", 0),
                                        ("if a {\n        try first()\n        try second()\n    }", 1)])
def test_a_try_expression_opens_no_nesting_level(body, depth):
    """Error Handling: `try` marks an expression that can throw and opens no block. The
    nesting pass opened a level at each plain `try` that no brace closed."""
    source = f"func tryTwice(a: Bool) throws {{\n    {body}\n}}\n"

    assert _counts(source)[4] == depth


@pytest.mark.parametrize("raw", ['##"a"#b"##', '#"""\n    { if\n    """#'])
def test_a_raw_string_ends_at_as_many_hashes_as_opened_it(raw):
    """Strings and Characters, Extended String Delimiters: `##"a"#b"##` is one string,
    and a raw string over lines holds no code."""
    source = f"func f(a: Bool) -> String {{\n    let s = {raw}; if a {{ return s }}\n    return \"\"\n}}\n"
    lines = source.count("\n")

    assert [(r.start, r.end, r.ccn) for r in analyze.analyze_source("case.swift", source)] == [(1, lines, 2)]


def test_register_raises_when_lizard_resolves_something_else(monkeypatch):
    """A lizard release that stops reading `languages()` out of module globals must
    break loudly here, not measure Swift with the reader that hides functions."""
    from lizard_languages.swift import SwiftReader as StockSwiftReader

    from crapkit import lizardswift

    monkeypatch.setattr(lizardswift.lizard, "get_reader_for", lambda _: StockSwiftReader)
    with pytest.raises(RuntimeError, match="resolves '.swift' to SwiftReader, not CorrectedSwiftReader"):
        lizardswift.register()
