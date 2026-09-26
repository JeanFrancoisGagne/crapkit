"""Four constants that each name a language vocabulary crapkit had half-learned.

The istanbul file-filter guard, the mutation operator guard, and the README
sentence that tells a user which languages exist. Each one was written for the ts/py pair and never widened.
"""
import re
from pathlib import Path

import pytest

from crapkit import mutate
from crapkit.config import (SUPPORTED_LANGUAGES, SUPPORTED_PARSERS, ConfigError,
                            _SOURCE_SUFFIXES, load_config_text)
from crapkit.mutate import file_mutants
from crapkit.universe import LANGUAGE_EXTENSIONS

ROOT = Path(__file__).resolve().parent.parent.parent


def _doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _lane_toml(command: str) -> str:
    return ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["javascript"]\n'
            '[[lane]]\nname = "unit"\n'
            f'command = "{command}"\n'
            'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')


# --- _SOURCE_SUFFIXES: the vitest file-filter trap ----------------------------

def test_a_cjs_filter_beside_coverage_is_rejected_like_a_js_one():
    """universe.py already claims .cjs for javascript, so a .cjs positional
    filter narrows the include set exactly as a .js one does."""
    with pytest.raises(ConfigError, match="file filter"):
        load_config_text(_lane_toml("vitest run src/legacy.cjs --coverage"))


@pytest.mark.parametrize("command", [
    "vitest run --coverage --config vitest.ci.ts",
    "vitest run --coverage --exclude src/legacy.cjs",
    "vitest run --coverage --reporter ./tools/my-reporter.ts",
])
def test_a_source_path_that_is_a_flags_value_is_not_a_file_filter(command: str):
    """The same misread the pytest guard had (#19, #22): `--exclude src/x.cjs`
    is one option and its value, not a positional narrowing the include set."""
    assert load_config_text(_lane_toml(command)).lanes[0].name == "unit"


def test_the_filter_guard_knows_every_extension_an_istanbul_lane_can_run():
    """The guard reads vitest command strings, so it needs the js/ts family and
    nothing else: a .swift or .go filter never appears in one."""
    js_family = {ext for lang in ("typescript", "tsx", "javascript")
                 for ext in LANGUAGE_EXTENSIONS[lang]}

    assert js_family <= set(_SOURCE_SUFFIXES)


# --- mutate._PROTECT: the Swift range operators -------------------------------

SWIFT_HALF_OPEN = ("func total(_ b: Int) -> Int {\n"
                   "    for i in 0..<b { print(i) }\n"
                   "    return b\n"
                   "}\n")


def test_a_swift_half_open_range_is_not_a_comparison():
    """`0..<=b` and `0..>=b` do not compile, so both mutants died on the
    compiler rather than on a test and read as killed for the wrong reason."""
    mutants = file_mutants(SWIFT_HALF_OPEN, changed_lines={2}, language="swift")

    assert [m.mutated for m in mutants] == []


def test_a_real_comparison_beside_a_range_still_mutates():
    src = ("func total(_ b: Int) -> Int {\n"
           "    for i in 0..<b where i > 2 { print(i) }\n"
           "    return b\n"
           "}\n")

    mutated = {m.mutated.strip() for m in file_mutants(src, changed_lines={2}, language="swift")}

    assert mutated == {"for i in 0..<b where i >= 2 { print(i) }",
                       "for i in 0..<b where i <= 2 { print(i) }"}


def test_the_closed_range_operator_shields_the_tokens_inside_it():
    """No operator in `_OPS` sits inside `...` today, so `file_mutants` cannot
    show this. The guard is the pair: `..<` and `...` are one vocabulary, and a
    dotted operator added to either set must not split a Swift range."""
    assert mutate._covered_by_longer("for i in 0...b", 10, "..") is True


def test_no_admitted_language_mutates_to_nothing_without_saying_why():
    """`file_mutants` reads `_OPS.get(language, _OPS["typescript"])`, so an
    unnamed language takes the C-family table SILENTLY. Six languages arrived in
    one wave on that fall-through. The rule that keeps it honest: an admitted
    language either refuses out loud, or produces mutants for the one comparison
    every table in this module holds. A silent empty list would read on a report
    as a function nothing could mutate."""
    comparison = "if (a > b) { return a; }\n"

    silent = []
    for language in sorted(SUPPORTED_LANGUAGES):
        route = mutate.mutation_language(f"probe{LANGUAGE_EXTENSIONS[language][0]}")
        if mutate.refusal(route):
            continue
        source = f'<script>{comparison}</script>' if language == 'vue' else comparison
        if not file_mutants(source, changed_lines={1}, language=route):
            silent.append(language)

    assert silent == []


CPP_BRANCH = ("int pick(int a, int b) {\n"
              "  if (a > b && a != 0) { return a; }\n"
              "  return b;\n"
              "}\n")


def test_the_c_family_mutates_off_the_table_it_falls_through_to():
    """C, C++, Objective-C, Java and Zig were admitted with no table of their
    own, which is a decision rather than an omission: they spell `==`, `!=`,
    `<`, `>`, `&&`, `||` exactly as the C-family table does."""
    mutated = {m.mutated.strip()
               for m in file_mutants(CPP_BRANCH, changed_lines={2},
                                     language=mutate.mutation_language("src/pick.cpp"))}

    assert mutated == {"if (a > b && a == 0) { return a; }",
                       "if (a >= b && a != 0) { return a; }",
                       "if (a <= b && a != 0) { return a; }",
                       "if (a > b || a != 0) { return a; }"}


def test_a_member_arrow_is_not_a_comparison():
    """`->` is already in `_PROTECT` for Rust's return arrow, and C's member
    access is the same two characters: without it `n->size` mutates to `n->=size`
    and the mutant dies on the compiler, reading as killed by a test."""
    src = "int deref(Node* n) {\n  if (n->size >= 2) { return 1; }\n  return 0;\n}\n"

    mutated = {m.mutated.strip()
               for m in file_mutants(src, changed_lines={2},
                                     language=mutate.mutation_language("src/deref.c"))}

    assert mutated == {"if (n->size > 2) { return 1; }", "if (n->size < 2) { return 1; }"}


# --- one token in its own language, never a comparison -------------------------

@pytest.mark.parametrize("source", [
    "ch <- v\n",
    "ch<-v\n",
    "v := <-ch\n",
    "func pump(in <-chan int, out chan<- int) {}\n",
])
def test_the_go_channel_arrow_is_not_a_comparison(source):
    """Go spec, Send statements and Receive operator: `<-` is one token. Read as
    `<` it grew `ch <=- v` and `v := >=-ch`, which do not compile, so every one
    died on the compiler and counted as a kill no test made."""
    assert file_mutants(source, None, "go") == []


def test_a_go_comparison_beside_the_arrow_still_mutates():
    """The arrow takes only its own two characters: a `<` of its own on the
    same line keeps its mutants."""
    mutated = [m.mutated for m in file_mutants("ok := <-ch < n\n", None, "go")]

    assert mutated == ["ok := <-ch <= n", "ok := <-ch >= n"]


def test_less_than_minus_is_a_comparison_where_go_is_not_the_language():
    """Only Go lexes `<-` as one token. In C++, TypeScript and Java `a<-1` is
    `a < -1`, and its mutants are real ones."""
    languages = ("cpp", "typescript", "java")
    mutated = {language: [m.mutated for m in file_mutants("x = a<-1;\n", None, language)]
               for language in languages}

    assert mutated == {language: ["x = a<=-1;", "x = a>=-1;"] for language in languages}


def _script(language: str, line: str) -> str:
    return f"<script>\n{line}</script>\n" if language == "vue" else line


@pytest.mark.parametrize("language", ["javascript", "typescript", "tsx", "vue", "java"])
@pytest.mark.parametrize("line", ["x = a >>> b;\n", "a >>>= b;\n"])
def test_the_unsigned_shift_is_not_a_comparison(language, line):
    """ECMAScript and the JLS lex `>>>` and `>>>=` as one token. Split into
    `>>` and a `>` it grew `a >><= b`, which does not compile, and `a >>>= b`,
    an assignment where a shift stood."""
    assert file_mutants(_script(language, line), None, language) == []


def test_a_comparison_after_an_unsigned_shift_still_mutates():
    mutated = [m.mutated for m in file_mutants("ok = (a >>> 1) > b;\n", None, "java")]

    assert mutated == ["ok = (a >>> 1) >= b;", "ok = (a >>> 1) <= b;"]


@pytest.mark.parametrize("language", ["cpp", "objectivec"])
@pytest.mark.parametrize("line", ["auto r = a <=> b;\n", "void f() <% g(); %>\n"])
def test_the_three_way_comparison_and_brace_digraphs_are_one_token(language, line):
    """C++ lexes `<=>` whole, and C and C++ read `<%` and `%>` as `{` and `}`.
    Split, `a <=> b` grew `a <> b` and `a >> b`, and `<% g(); %>` grew
    `<=% g(); %>`: none of them compiles."""
    assert file_mutants(line, None, language) == []


def test_a_comparison_of_a_three_way_result_still_mutates():
    mutated = [m.mutated for m in file_mutants("bool lt = (a <=> b) < 0;\n", None, "cpp")]

    assert mutated == ["bool lt = (a <=> b) <= 0;", "bool lt = (a <=> b) >= 0;"]


@pytest.mark.parametrize("line", ["let y = x |> f\n", "let m = a <> b\n",
                                  "let r = f <^> xs\n", "let v = a >>> b\n"])
def test_a_swift_custom_operator_is_one_token(line):
    """The Swift reference, Lexical Structure, Operators: a run of operator
    characters is one operator. `x |> f` grew `x |>= f` and `x |<= f`, operators
    nobody declared, so the compiler killed them."""
    assert file_mutants(line, None, "swift") == []


def test_a_swift_comparison_that_is_its_whole_run_still_mutates():
    mutated = [m.mutated for m in file_mutants("let ok = xs |> count >= n\n", None, "swift")]

    assert mutated == ["let ok = xs |> count > n", "let ok = xs |> count < n"]


# --- a connective that opens an operand, and an operator's own name ------------

@pytest.mark.parametrize("line", [
    "fn f() { let v = o.unwrap_or_else(|| 0); }\n",
    "fn f() { spawn(move || { g(); }); }\n",
    "fn f() { let y = &&x; }\n",
    "fn f(x: &&str) {}\n",
    "fn f() { let n = xs.iter().filter(|&&x| x.ok).count(); }\n",
])
def test_a_rust_closure_or_double_borrow_is_not_a_connective(line):
    """With nothing on its left, Rust's `||` is a closure with no parameters and
    its `&&` borrows twice. Flipped, `spawn(move && { g(); })` and `let y = ||x;`
    do not compile, so the compiler killed them and the run counted the kills."""
    assert file_mutants(line, None, "rust") == []


@pytest.mark.parametrize("text, mutated", [
    ("fn f() { let ok = a\n    && b; }\n", ["    || b; }"]),
    ("fn f() { let ok = x as bool && y; }\n", ["fn f() { let ok = x as bool || y; }"]),
    ("fn f() { let ok = xs.any(|x| x.ok) || fut.await && v?; }\n",
     ["fn f() { let ok = xs.any(|x| x.ok) || fut.await || v?; }",
      "fn f() { let ok = xs.any(|x| x.ok) && fut.await && v?; }"]),
])
def test_a_rust_connective_after_an_operand_still_mutates(text, mutated):
    """rustfmt starts a continuation line with the operator, so the operand it
    joins sits on the line above."""
    assert [m.mutated for m in file_mutants(text, None, "rust")] == mutated


@pytest.mark.parametrize("line", [
    "void f() { for (auto&& x : xs) { g(x); } }\n",
    "void f(int&& y) { g(y); }\n",
    "auto f() -> int&&;\n",
    "void *p = &&done;\n",
])
def test_a_cpp_reference_or_label_address_is_not_a_connective(line):
    """`auto&& x` and `int&& y` declare rvalue references, and GNU C's `&&done`
    takes a label's address. `auto|| x` does not compile."""
    assert file_mutants(line, None, "cpp") == []


@pytest.mark.parametrize("language, text, changed", [
    ("cpp", "void f(Foo&& other) { g(); }\n", None),
    ("cpp", "void f(const Foo &&other) { g(); }\n", None),
    ("cpp", "void f(std::vector<int>&& v) { g(); }\n", None),
    ("cpp", "Foo(Foo&&) = default;\n", None),
    ("cpp", "Foo& operator=(Foo&&) noexcept;\n", None),
    ("cpp", "void f(Foo&&, int n);\n", None),
    ("cpp", "template <typename... Args>\nvoid f(Args&&... args) { g(); }\n", {2}),
    ("objectivec", "void f(Foo&& other) { g(); }\n", None),
])
def test_a_cpp_reference_after_a_type_name_is_not_a_connective(language, text, changed):
    """A type is a name, so the token before `&&` cannot tell `Foo&& other` from
    `a && b`. clang-format hugs a reference to its type or its name and spaces a
    connective on both sides, and nothing after `Foo&&)` can be a right operand.
    `void f(Foo|| other)` does not compile, so the run counted a kill no test made."""
    assert file_mutants(text, changed, language) == []


def test_a_cast_to_a_reference_is_a_type_argument_not_two_comparisons():
    """`static_cast<` always opens a type. Its `<T&&>` read as two comparisons and
    a connective, and none of the five mutants compiled."""
    assert file_mutants("auto y = static_cast<T&&>(x);\n", None, "cpp") == []


@pytest.mark.parametrize("text, changed, mutated", [
    ("bool f() { return a&&b; }\n", None, ["bool f() { return a||b; }"]),
    ("bool f() { return f(a)&& b; }\n", None, ["bool f() { return f(a)|| b; }"]),
    ("bool f() { return a &&\n    b; }\n", None, ["bool f() { return a ||"]),
    ("template <typename... Ts>\nbool all(Ts... ts) { return (ts && ...); }\n", {2},
     ["bool all(Ts... ts) { return (ts || ...); }"]),
    ("bool f(int n) { return static_cast<int>(n) > 0 && ok; }\n", None,
     ["bool f(int n) { return static_cast<int>(n) >= 0 && ok; }",
      "bool f(int n) { return static_cast<int>(n) <= 0 && ok; }",
      "bool f(int n) { return static_cast<int>(n) > 0 || ok; }"]),
])
def test_a_cpp_connective_spaced_like_one_still_mutates(text, changed, mutated):
    """Unspaced, spaced on both sides, broken after the operator, or a fold over
    a pack: each joins two operands."""
    assert [m.mutated for m in file_mutants(text, changed, "cpp")] == mutated


@pytest.mark.parametrize("line, mutated", [
    ("bool f() { return this && n-- && ok; }\n",
     ["bool f() { return this || n-- && ok; }", "bool f() { return this && n-- || ok; }"]),
    ("if constexpr (std::is_same_v<T, int> && N > 0) {}\n",
     ["if constexpr (std::is_same_v<T, int> && N >= 0) {}",
      "if constexpr (std::is_same_v<T, int> && N <= 0) {}",
      "if constexpr (std::is_same_v<T, int> || N > 0) {}"]),
])
def test_a_cpp_connective_after_an_operand_still_mutates(line, mutated):
    """`this`, a postfix `n--` and a template's closing `>` each end an operand."""
    assert [m.mutated for m in file_mutants(line, None, "cpp")] == mutated


@pytest.mark.parametrize("language, line", [
    ("cpp", "struct A { bool operator<(const A& o) const; };\n"),
    ("cpp", "struct A { bool operator==(const A& o) const; };\n"),
    ("objectivec", "struct A { bool operator&&(const A& o) const; };\n"),
    ("swift", "struct A { static func < (l: A, r: A) -> Bool { f() } }\n"),
])
def test_a_declared_operator_is_a_name_not_an_operation(language, line):
    """`bool operator<=(...)` declares a different operator, so every caller of
    `<` and the definition out of line stop compiling."""
    assert file_mutants(line, None, language) == []


# --- the language set a user actually reads -----------------------------------

DISPLAY = {"typescript": "TypeScript", "tsx": "TSX", "javascript": "JavaScript",
           "python": "Python", "swift": "Swift", "go": "Go", "rust": "Rust",
           "shell": "shell", "cpp": "C++", "objectivec": "Objective-C",
           "vue": "Vue", "java": "Java", "zig": "Zig", "powershell": "PowerShell"}


def _unnamed(prose: str) -> list[str]:
    """Display names this prose does not carry, as whole words.

    `\\b` cannot express this: it asks for a word character on the inside of the
    boundary, and `C++` ends in punctuation, so `\\bC\\+\\+\\b` matches nothing at
    all. Asserting no word character sits on either side says what was meant and
    still refuses `Rust` inside `Rusty`.
    """
    return [name for name in DISPLAY.values()
            if not re.search(rf"(?<!\w){re.escape(name)}(?!\w)", prose)]


def test_every_supported_language_has_a_display_name():
    assert set(DISPLAY) == SUPPORTED_LANGUAGES


def test_the_readme_intro_names_every_supported_language():
    """README.md's opening paragraph is the only place a user learns the set."""
    assert _unnamed(_doc("README.md").split("```", 1)[0]) == []


def test_the_handbook_standfirst_names_every_supported_language():
    standfirst = _doc("docs/handbook.html").split('class="standfirst"', 1)[1].split("</p>", 1)[0]

    assert _unnamed(standfirst) == []


def test_the_pygments_deferral_note_names_every_language_it_counts():
    """`_pygdefer` justifies hiding pygments by listing what crapkit reads, and
    the list is the argument: an Erlang reader is safe to stub only while no
    scope can name Erlang. It went stale twice, once per language admitted in
    the same wave, so the module's own docstring is pinned to the set."""
    note = (ROOT / "src" / "crapkit" / "_pygdefer.py").read_text(encoding="utf-8")
    header = note.split('"""', 2)[1]

    missing = sorted(lang for lang in SUPPORTED_LANGUAGES
                     if not re.search(rf"(?<!\w){lang}(?!\w)", header))

    assert missing == []


# --- the handbook's language table --------------------------------------------

_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)


def _language_rows() -> dict[str, list[str]]:
    """The handbook's language table as {label: [extensions cell, coverage cell]}.

    Keyed on crapkit's own label rather than the display name, because the label
    is what a `crapkit.toml` has to spell and a table a user reads to write one
    has to show it.
    """
    table = _doc("docs/handbook.html").split('<table id="languages">', 1)[1].split("</table>", 1)[0]
    rows = {}
    for row in _ROW.findall(table):
        cells = [re.sub(r"<[^>]+>", "", cell).strip() for cell in _CELL.findall(row)]
        if len(cells) == 3 and cells[0] != "Language":
            rows[cells[0]] = cells[1:]
    return rows


def test_the_handbook_table_carries_a_row_for_every_supported_language():
    """The standfirst names the languages; only this table says what to write in
    `languages = [...]` to get one, which is the sentence a user acts on."""
    assert SUPPORTED_LANGUAGES <= set(_language_rows())


def test_every_row_names_every_extension_its_language_claims():
    rows = _language_rows()

    unlisted = {lang: [ext for ext in exts if ext not in rows.get(lang, ("", ""))[0]]
                for lang, exts in LANGUAGE_EXTENSIONS.items()}

    assert {lang: missing for lang, missing in unlisted.items() if missing} == {}


def test_a_row_for_a_language_no_scope_can_name_says_so():
    """A table that lists what crapkit does NOT read earns its honesty the hard
    way: every such row has to be marked, or the next reader takes it for an
    admitted language whose config line just fails validation."""
    extra = set(_language_rows()) - SUPPORTED_LANGUAGES

    unmarked = [lang for lang in extra if "not admitted" not in " ".join(_language_rows()[lang])]

    assert unmarked == []


def test_the_cc_only_rows_are_exactly_the_languages_init_writes_the_key_for():
    """The table is where a reader learns their language has no parser; the set
    is what makes `crapkit init` write `coverage_optional` for their scope. Two
    answers to one question, so they have to be the same answer."""
    from crapkit.scaffold import COVERABLE_LANGUAGES

    marked = {lang for lang, cells in _language_rows().items()
              if cells[1] == "cc-only" and lang in SUPPORTED_LANGUAGES}

    assert marked == SUPPORTED_LANGUAGES - COVERABLE_LANGUAGES


def test_no_row_claims_a_coverage_parser_crapkit_does_not_have():
    """`cc-only` is a real answer and the two parsers are the only other ones.
    A row naming a third would promise a join that nothing in crapkit performs."""
    claims = [(lang, cells[1]) for lang, cells in _language_rows().items()]

    unbacked = [(lang, claim) for lang, claim in claims
                if "cc-only" not in claim and "not admitted" not in claim
                and not any(parser in claim for parser in SUPPORTED_PARSERS)]

    assert unbacked == []
