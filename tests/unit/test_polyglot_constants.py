"""Four constants that each name a language vocabulary crapkit had half-learned.

The istanbul file-filter guard, the definition-line pattern discovery reads, the
mutation operator guard, and the README sentence that tells a user which
languages exist. Each one was written for the ts/py pair and never widened.
"""
import re
from pathlib import Path

import pytest

from crapkit import discover, mutate
from crapkit.config import (SUPPORTED_LANGUAGES, SUPPORTED_PARSERS, ConfigError,
                            _SOURCE_SUFFIXES, load_config_text)
from crapkit.discover import callers
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


def test_the_filter_guard_knows_every_extension_an_istanbul_lane_can_run():
    """The guard reads vitest command strings, so it needs the js/ts family and
    nothing else: a .swift or .go filter never appears in one."""
    js_family = {ext for lang in ("typescript", "tsx", "javascript")
                 for ext in LANGUAGE_EXTENSIONS[lang]}

    assert js_family <= set(_SOURCE_SUFFIXES)


# --- discover._def_pattern: func and fun --------------------------------------

GO = ("package main\n"
      "\n"
      "func send(x int) int {\n"
      "\tif x == 0 {\n"
      "\t\treturn 0\n"
      "\t}\n"
      "\treturn send(x - 1)\n"
      "}\n"
      "\n"
      "func relay(x int) int {\n"
      "\treturn send(x)\n"
      "}\n")

SWIFT = ("func send(_ x: Int) -> Int {\n"
         "    if x == 0 {\n"
         "        return 0\n"
         "    }\n"
         "    return send(x - 1)\n"
         "}\n"
         "\n"
         "func relay(_ x: Int) -> Int {\n"
         "    return send(x)\n"
         "}\n")

KOTLIN = ("fun send(x: Int): Int {\n"
          "    if (x == 0) {\n"
          "        return 0\n"
          "    }\n"
          "    return send(x - 1)\n"
          "}\n"
          "\n"
          "fun relay(x: Int): Int {\n"
          "    return send(x)\n"
          "}\n")

RUST = ("fn send(x: i32) -> i32 {\n"
        "    if x == 0 {\n"
        "        return 0;\n"
        "    }\n"
        "    send(x - 1)\n"
        "}\n"
        "\n"
        "fn relay(x: i32) -> i32 {\n"
        "    send(x)\n"
        "}\n")

# The 1,308-of-1,342 spelling, counted over 259 real scripts: no keyword at all.
SHELL = ('send() {\n'
         '  local x="$1"\n'
         '  send "$x"\n'
         '}\n'
         '\n'
         'relay() {\n'
         '  send "$1"\n'
         '}\n')

# The body a paren opens. The method-shorthand branch misses it, because that one
# ends on ':' or '{'.
SHELL_SUBSHELL = ('send() (\n'
                  '  send "$1"\n'
                  ')\n'
                  '\n'
                  'relay() {\n'
                  '  send "$1"\n'
                  '}\n')


@pytest.fixture()
def grep(monkeypatch):
    """`git grep`, recorded instead of spawned."""
    def stub(root, args):
        return stub.output

    stub.output = ""
    monkeypatch.setattr(discover, "_grep_output", stub)
    return stub


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.mark.parametrize(("name", "source", "own_call", "outside_call"), [
    ("cmd/a.go", GO, 7, 11),
    ("cmd/a.swift", SWIFT, 5, 9),
    ("cmd/a.kt", KOTLIN, 5, 9),
    ("cmd/a.rs", RUST, 5, 9),
    ("cmd/a.sh", SHELL, 3, 7),
    ("cmd/b.sh", SHELL_SUBSHELL, 2, 6),
])
def test_a_func_or_fun_body_does_not_count_as_its_own_caller(
        tmp_path: Path, grep, name: str, source: str, own_call: int, outside_call: int):
    """`_def_pattern` matched def/class/function only, so a Swift, Kotlin or Go
    definition had no span and its own recursive call read as a caller.

    Rust needed `fn` added: `fun` does not claim it, and `pub fn` needs `pub` in
    the modifier set as well. Shell needs no keyword in its common spelling —
    the method-shorthand branch already claims `name() {` — but the
    subshell-bodied `name() (` opens with a paren the shorthand does not end on."""
    _write(tmp_path / name, source)
    grep.output = f"{name}:{own_call}:    return send(x - 1)\n{name}:{outside_call}:    return send(x)"

    found = callers(tmp_path, ["cmd"], name, "send")

    assert found["callers"] == [{"path": name, "line": outside_call}]
    assert found["count"] == 1


def test_a_rust_pub_fn_is_still_its_own_definition(tmp_path: Path, grep):
    """`pub` is Rust's export modifier and half of all Rust definitions carry it;
    without it in the modifier set the line stops matching at column 0."""
    _write(tmp_path / "cmd" / "a.rs", "pub fn send(x: i32) -> i32 {\n    send(x - 1)\n}\n")
    grep.output = "cmd/a.rs:2:    send(x - 1)\ncmd/b.rs:4:    send(1);"

    found = callers(tmp_path, ["cmd"], "cmd/a.rs", "send")

    assert found["callers"] == [{"path": "cmd/b.rs", "line": 4}]


def test_the_function_keyword_still_wins_over_the_shorter_forms(tmp_path: Path, grep):
    """`fun`, `func` and `fn` are prefixes of `function`; adding them must not
    stop a JavaScript definition from matching."""
    _write(tmp_path / "src" / "a.js", "function send(x) {\n  return send(x - 1);\n}\n")
    grep.output = "src/a.js:2:  return send(x - 1);\nsrc/b.js:4:  send(1);"

    found = callers(tmp_path, ["src"], "src/a.js", "send")

    assert found["callers"] == [{"path": "src/b.js", "line": 4}]


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
        if not file_mutants(comparison, changed_lines={1}, language=route):
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


def test_no_row_claims_a_coverage_parser_crapkit_does_not_have():
    """`cc-only` is a real answer and the two parsers are the only other ones.
    A row naming a third would promise a join that nothing in crapkit performs."""
    claims = [(lang, cells[1]) for lang, cells in _language_rows().items()]

    unbacked = [(lang, claim) for lang, claim in claims
                if "cc-only" not in claim and "not admitted" not in claim
                and not any(parser in claim for parser in SUPPORTED_PARSERS)]

    assert unbacked == []
