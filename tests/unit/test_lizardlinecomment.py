"""A `//` comment ends at its line's end, except in C, C++ and Objective-C.

lizard 1.24.0 reads a `//` comment that ends in a backslash on into the next
line, the way a C preprocessor splices lines. Java, JavaScript, TypeScript, TSX,
Vue, Swift and Rust splice nothing, so the line lizard took was code. A comment
holding a Windows path (`// C:\\dir\\`) above a signature cost the function its
row; one above an `if` hid the if, and the function ended at that if's `}`.

Every row below was counted by hand: the function runs from its signature line
to its closing brace, and its one `if` makes ccn 2 and cognitive 1.
"""
import subprocess
import sys

import lizard
import pytest
from lizard_languages.clike import CLikeReader
from lizard_languages.rust import RustReader as StockRustReader

from crapkit import lizardlinecomment
from crapkit.analyze import analyze_source
from crapkit.lizardrust import CorrectedRustReader


def rows(path: str, source: str) -> list[tuple]:
    return [(r.long_name, r.start, r.end, r.ccn_std, r.cognitive) for r in analyze_source(path, source)]


# Each file: a comment ending in a backslash above the function's signature, and
# another above its `if`.
JAVA = """class C {
  // C:\\dir\\
  int a(int n) {
    // C:\\dir\\
    if (n > 0) {
      return 1;
    }
    return 0;
  }
}
"""

JAVASCRIPT = """// C:\\dir\\
function a(n) {
  // C:\\dir\\
  if (n > 0) {
    return 1;
  }
  return 0;
}
"""

TYPESCRIPT = """// C:\\dir\\
function a(n: number): number {
  // C:\\dir\\
  if (n > 0) {
    return 1;
  }
  return 0;
}
"""

VUE = """<script>
// C:\\dir\\
function a(n) {
  // C:\\dir\\
  if (n > 0) {
    return 1;
  }
  return 0;
}
</script>
"""

SWIFT = """// C:\\dir\\
func a(n: Int) -> Int {
    // C:\\dir\\
    if n > 0 {
        return 1
    }
    return 0
}
"""

RUST = """// C:\\dir\\
fn a(n: u8) -> u8 {
    // C:\\dir\\
    if n > 0 {
        return 1;
    }
    0
}
"""

ENDS_AT_ITS_LINE = [
    ("a.java", JAVA, ("C::a( int n)", 3, 9, 2, 1)),
    ("a.js", JAVASCRIPT, ("a ( n )", 2, 8, 2, 1)),
    ("a.cjs", JAVASCRIPT, ("a ( n )", 2, 8, 2, 1)),
    ("a.mjs", JAVASCRIPT, ("a ( n )", 2, 8, 2, 1)),
    ("a.jsx", JAVASCRIPT, ("a ( n )", 2, 8, 2, 1)),
    ("a.ts", TYPESCRIPT, ("a ( n )", 2, 8, 2, 1)),
    ("a.tsx", TYPESCRIPT, ("a ( n )", 2, 8, 2, 1)),
    ("a.vue", VUE, ("a ( n )", 3, 9, 2, 1)),
    ("a.swift", SWIFT, ("a n : Int", 2, 8, 2, 1)),
    ("a.rs", RUST, ("a n : u8", 2, 8, 2, 1)),
]


@pytest.mark.parametrize("path,source,row", ENDS_AT_ITS_LINE, ids=[p for p, _, _ in ENDS_AT_ITS_LINE])
def test_a_comment_ending_in_a_backslash_ends_at_its_line(path, source, row):
    """lizard gave each of these no row: the comment above took the signature."""
    assert rows(path, source) == [row]


@pytest.mark.parametrize("path,source,row", ENDS_AT_ITS_LINE, ids=[p for p, _, _ in ENDS_AT_ITS_LINE])
def test_the_backslash_changes_nothing(path, source, row):
    """The same file with each comment's backslash gone reads the same row."""
    assert rows(path, source.replace("dir\\\n", "dir\n")) == [row]


C_FAMILY = """int a(int n) {
  // C:\\dir\\
  if (n > 0) {
    return 1;
  }
  return 0;
}
"""


@pytest.mark.parametrize("path", ["a.c", "a.cpp", "a.h", "a.m"])
def test_a_c_family_comment_still_takes_the_next_line(path):
    """C splices a line ending in a backslash onto the next before comments are
    read, so the `if (n > 0) {` line is comment, and the `}` under it ends a."""
    assert rows(path, C_FAMILY) == [("a( int n)", 1, 5, 1, 0)]


def test_a_backslash_inside_a_line_or_a_string_ends_nothing_early():
    """Only a backslash at the line's end ever spliced: one mid-comment, and a
    string whose last character is a backslash, leave the function as it was."""
    source = ('function a(n) {\n  // C:\\dir\\ and more\n  const s = "C:\\\\dir\\\\";\n'
              "  if (n > 0) {\n    return 1;\n  }\n  return 0;\n}\n")

    assert rows("a.js", source) == [("a ( n )", 1, 8, 2, 1)]


def test_a_template_after_a_comment_ending_in_a_backslash_is_still_masked():
    """The template-literal mask finds templates the way the tokenizer finds
    them, so it has to end the comment at its line too: the nested template on
    the line after it was skipped as comment and left unmasked, and its inner
    backtick took `b` into a template."""
    source = ("// C:\\dir\\\n"
              "const s = `a${`x`}c`;\n"
              "function b(n: number): number {\n  if (n > 0) {\n    return 1;\n  }\n  return 0;\n}\n")

    assert rows("a.ts", source) == [("b ( n )", 3, 8, 2, 1)]


RUST_TOKENS = """fn a<'a>(s: &'a str, c: char) -> &'a str {
    'outer: loop { if c == 'x' { break 'outer; } }
    /* block */ s // tail
}
"""


def test_the_rust_tokens_are_lizards_where_no_comment_splices():
    """RustReader.generate_tokens drops the addition it is handed, so the
    corrected reader restates its one rule (a lifetime or a label). This fails
    the day lizard's own Rust tokens change, which is when that restatement
    needs another look."""
    assert list(CorrectedRustReader.generate_tokens(RUST_TOKENS)) == list(StockRustReader.generate_tokens(RUST_TOKENS))


def test_registering_again_wraps_no_tokenizer_twice():
    before = {reader: reader.generate_tokens for reader in lizardlinecomment._READERS}

    lizardlinecomment.register()
    lizardlinecomment.register()

    assert {reader: reader.generate_tokens for reader in lizardlinecomment._READERS} == before


def test_register_refuses_a_reader_that_still_splices(monkeypatch):
    """A suffix that resolves to a reader that splices, which is what a lizard
    release that picks readers some other way would produce, is loud."""
    monkeypatch.setattr(lizard, "get_reader_for", lambda name: CLikeReader)

    with pytest.raises(RuntimeError, match=r"'java' to CLikeReader"):
        lizardlinecomment.register()


def test_a_spawned_child_importing_analyze_ends_comments_at_the_line():
    """A pool worker on Windows starts from a bare interpreter, so the rule has
    to ride the import."""
    probe = ("import crapkit.analyze;"
             "from lizard_languages import get_reader_for as g;"
             "print(all('b' in list(g('p' + s).generate_tokens('// a\\\\\\nb\\n'))"
             " for s in ('.java', '.js', '.ts', '.tsx', '.vue', '.swift', '.rs', '.go', '.zig')))")

    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         check=True).stdout

    assert out.strip() == "True"


def test_the_wrapper_puts_the_line_comment_ahead_of_every_addition():
    """What the stock tokenizer is handed: the source, LINE_COMMENT ahead of the
    caller's additions, and the caller's token class."""
    handed = []

    def stock(source_code, addition="", token_class=None):
        handed.append((source_code, addition, token_class))
        return iter(("t",))

    wrapped = lizardlinecomment._ending_line_comments(stock).__func__

    assert (list(wrapped("s", "|x", int)), list(wrapped("s"))) == (["t"], ["t"])
    assert handed == [("s", lizardlinecomment.LINE_COMMENT + "|x", int),
                      ("s", lizardlinecomment.LINE_COMMENT, None)]
    assert wrapped.crapkit_stock is stock


def test_registering_wraps_a_stock_tokenizer_that_lost_its_wrapper(monkeypatch):
    from lizard_languages.java import JavaReader
    stock = JavaReader.generate_tokens.crapkit_stock
    monkeypatch.setattr(JavaReader, "generate_tokens", staticmethod(stock))

    lizardlinecomment.register()

    assert JavaReader.generate_tokens.crapkit_stock is stock
    assert "b" in list(JavaReader.generate_tokens("// a\\\nb\n"))
