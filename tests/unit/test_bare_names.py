"""The bare identifier a long name opens with, in every language crapkit reads.

lizard prints the signature it parsed, and only some of its readers spell the
parameter list with parentheses. Python gives `classify( score , limit = 1 )`
and shell gives `classify()`, so cutting at the first `(` found the name. Rust
gives `route cmd : & Cmd` and Go gives `Classify n int`, where the parameters
follow the name with nothing but a space, and that cut handed the whole
signature back: `brief src/lib.rs route` was rejected as an unknown name, and
the packet's `handle` read `route cmd : & Cmd`.

The rule is the leading token of whatever precedes the parameter list. Every
parenthesised language keeps the name it had, including the `::`-qualified
C++ and Java forms and an Objective-C selector's trailing colon, because none
of those holds a space.

Anonymity is unmoved: `(anonymous)` and `(anonymous) ( z )` both open with the
parenthesis, so an empty bare name is still the one test for it.
"""
import pytest

from crapkit.analyze import analyze_source
from crapkit.packet import ANONYMOUS, anonymous_starts, bare_name, handles
from crapkit.score import ScoredRow

# One named function per language, at the spelling its lizard reader actually
# parses. The expected value is the identifier a session types back.
SOURCES = {
    "app.py": "def classify(score, limit=1):\n    return score\n",
    "app.ts": "export function dispatch(a: number, b: Record<string, number>) {\n"
              "  return a;\n}\n",
    "app.sh": "classify() {\n  echo 1\n}\n",
    "app.ps1": "function Get-Thing {\n    param([int]$Code)\n    $Code\n}\n",
    "app.rs": "fn route(cmd: &Cmd) -> u8 {\n    0\n}\n",
    "app.go": "package main\n\nfunc Classify(n int) string {\n\treturn \"\"\n}\n",
}

EXPECTED = {"app.py": "classify", "app.ts": "dispatch", "app.sh": "classify",
            "app.ps1": "Get-Thing", "app.rs": "route", "app.go": "Classify"}


def row(long_name: str, start: int = 1) -> ScoredRow:
    return ScoredRow("s", "app.rs", long_name, start, start + 3, 2, 2, 2,
                     4, 1, 1, 0.0, "measured", 6.0, "test", 0)


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_the_bare_name_is_the_leading_identifier_in_every_language(name: str):
    """Rust and Go are the two that were wrong; the other four pin that the new
    rule did not move them."""
    (record,) = analyze_source(name, SOURCES[name])

    assert bare_name(record.long_name) == EXPECTED[name]


def test_a_rust_method_drops_its_receiver_and_its_types():
    """`impl` methods print the receiver as the first parameter, so the name is
    followed by ` & self , n : i32` and nothing else separates them."""
    src = "struct S;\nimpl S {\n    fn take(&self, n: i32) -> i32 { n }\n}\n"
    (record,) = analyze_source("app.rs", src)

    assert record.long_name == "take & self , n : i32"
    assert bare_name(record.long_name) == "take"


def test_a_go_functions_handle_is_the_identifier_not_the_signature():
    """What a fresh session read in the packet: `handle` is the string it copies
    into the next command, so a signature there is a name nothing accepts."""
    (record,) = analyze_source("app.go", SOURCES["app.go"])

    assert handles([row(record.long_name, 3)]) == {3: "Classify"}


@pytest.mark.parametrize("long_name,expected", [
    ("n::K::m( int a)", "n::K::m"),           # C++ through a namespace
    ("K::m( int a)", "K::m"),                 # Java
    ("doThing:( int )", "doThing:"),          # an Objective-C selector keeps its colon
    ("classify( score , limit = 1 )", "classify"),
    ("dispatch ( a , b Record )", "dispatch"),
])
def test_the_parenthesised_forms_keep_the_name_they_had(long_name: str, expected: str):
    """No space precedes the parameter list in any of these, so the leading-token
    rule and the split-on-`(` rule agree, byte for byte."""
    assert bare_name(long_name) == expected


def test_an_anonymous_function_still_has_no_bare_name():
    assert bare_name(ANONYMOUS) == ""
    assert bare_name(f"{ANONYMOUS} ( z )") == ""


def test_anonymity_is_still_the_empty_bare_name():
    """`anonymous_starts` and the store's handle listing both read anonymity off
    an empty prefix; a rule that named `(anonymous)` would renumber every handle.
    """
    rows = [row(ANONYMOUS, 9), row("route cmd : & Cmd", 20), row(ANONYMOUS, 41)]

    assert anonymous_starts(rows) == [9, 41]
    assert handles(rows) == {9: f"{ANONYMOUS}#1", 20: "route", 41: f"{ANONYMOUS}#2"}
