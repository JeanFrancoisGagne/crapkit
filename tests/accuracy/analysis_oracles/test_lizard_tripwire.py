"""Stock lizard 1.24.0 as a tripwire: crapkit changes only what its patch list says.

This counts as zero oracles. Stock lizard is what crapkit is built on, so it
agrees with crapkit by construction wherever crapkit leaves its readers alone,
and it is wrong where crapkit fixed it. What it catches is drift: a row that
moves in a language crapkit leaves to lizard's stock readers.

oracles/lizard_tripwire.py reads the same files through lizard's own API in a
process that imports no crapkit. The check pairs rows by (path, start,
long_name):

- Every admitted language now runs on a reader crapkit wrote or corrected, so
  each is in PATCHES: a difference passes only when a patch covers its column
  and its source shows the patch's construct. Swift, the C family, Java, Go and
  Zig run on crapkit's readers, which find, name and count what stock lizard
  misses, so their patches excuse every column. Rust and the JavaScript family
  excuse a difference only near the constructs crapkit reads differently, except
  in the nesting column, which crapkit reads off its cognitive pass in every
  language and lizard off its ND extension (TRIP-NESTING).
- Each patch must still excuse at least one probe difference, so a patch
  upstream has made redundant shows up here.
- A language no patch names excuses nothing.

Push reads the probe files. The nightly stock-language corpus check went with
the last stock reader (Swift).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from accuracy.analysis_oracles import analysis_tables

pytestmark = pytest.mark.process

ORACLE = Path(__file__).parent / "oracles" / "lizard_tripwire.py"
COLUMNS = ("start", "end", "nloc", "params", "ccn_std", "ccn_mod", "nesting")
C_FAMILY = (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".m", ".mm")
JS_FAMILY = (".js", ".cjs", ".mjs", ".jsx", ".ts", ".tsx", ".vue")

# A construct lizard reads as C where Rust means something else: `?`, an operator
# with no operand before it, a let-else, a `where`, `loop` or `for`, a signature
# ending in `;`, a parameter written as a pattern or with a bracketed type, and a
# `#` (an attribute, a raw string or a raw identifier), which lizard reads as a C
# preprocessor line.
RUST_SYNTAX = (r"\?|\|\||&&|\blet\b[^\n]*\belse\b|\bwhere\b|\bloop\b|\bfor\b|\bfn\b[^{]*;|"
               r"\bfn\s+\w+[^(]*\([^)]*[\[{(]|#")


@dataclass(frozen=True)
class Patch:
    id: str
    suffixes: tuple
    columns: tuple | None  # None: rows and every column
    construct: str | None  # a regex over the function's lines; None: any source
    source: str


PATCHES = (
    Patch("TRIP-PY", (".py",), None, None,
          "docs/upgrading.md 'Analysis version 11' section; docs/agent-json.md `nesting` row: a "
          "Python row reads nesting off crapkit's cognitive pass"),
    Patch("TRIP-SHELL", (".sh", ".bash"), None, None,
          "README.md 'Shell, PowerShell and Rust run on crapkit's own readers. lizard ships "
          "none for shell or PowerShell'"),
    Patch("TRIP-POWERSHELL", (".ps1", ".psm1"), None, None,
          "README.md 'Shell, PowerShell and Rust run on crapkit's own readers. lizard ships "
          "none for shell or PowerShell'"),
    Patch("TRIP-C-FAMILY", C_FAMILY, None, None,
          "README.md 'C, C++, Objective-C and Java run on lizard's readers with crapkit's "
          "fixes on top'; docs/configuration.md 'cpp, objectivec and java run on lizard's "
          "readers with crapkit's fixes on top': rows lizard hid, invented or misnamed, "
          "every declared parameter, and the && of a reference"),
    Patch("TRIP-JAVA", (".java",), None, None,
          "README.md 'C, C++, Objective-C and Java run on lizard's readers with crapkit's "
          "fixes on top'; docs/configuration.md 'cpp, objectivec and java run on lizard's "
          "readers with crapkit's fixes on top': methods after annotations and enum "
          "bodies, qualified names, text blocks, every declared parameter"),
    Patch("TRIP-RUST-MATCH", (".rs",), ("ccn_std", "ccn_mod"), r"\bmatch\b",
          "README.md 'Its Rust reader scores a 7-arm match as ccn 2 (filed as lizard #494)'"),
    Patch("TRIP-RUST-SYNTAX", (".rs",), None, RUST_SYNTAX,
          "README.md 'It also reads a Rust signature, a closure's empty ||, a let-else and a "
          "for that is no loop the way Rust means them'; 'It lists #[inline] fn f() { written "
          "on one line'; docs/configuration.md 'rust, shell and powershell run on crapkit's "
          "own readers'"),
    Patch("TRIP-GO-ZIG", (".go", ".zig"), None, None,
          "README.md 'Go and Zig read through crapkit's subclasses of lizard's readers, which "
          "end a signature where the language does'; docs/configuration.md 'go and zig run "
          "on subclasses of lizard's readers'"),
    Patch("TRIP-SWIFT", (".swift",), None, None,
          "README.md 'Python and Swift read through crapkit's subclasses of lizard's readers "
          "too'; docs/configuration.md 'swift runs on crapkit's own reader too': rows lizard "
          "hid or made up, raw identifiers, and the decisions Swift spells with ? and ??"),
    Patch("TRIP-JS-EXPRESSIONS", JS_FAMILY, None, r"=>|`|\?[^\n]*:",
          "README.md 'Expression arrows in arrays and argument lists are measured "
          "separately'; docs/architecture/2026-09-06/arrow-reader-review.md line 7 (comma, "
          "bracket and ternary-colon states)"),
    Patch("TRIP-NESTING", (*JS_FAMILY, ".rs"), ("nesting",), None,
          "docs/agent-json.md `nesting` row: 'Maximum nesting depth, read off crapkit's "
          "cognitive pass in every language'; the other languages' patches already excuse "
          "every column"),
)


@dataclass(frozen=True)
class Difference:
    path: str
    start: int
    long_name: str
    column: str  # a COLUMNS name, or "row" for a function one side does not list
    stock: object
    crapkit: object
    context: str  # the source lines the construct regex reads


def stock_rows(root: str, paths: list[str]) -> list[dict]:
    """Stock lizard's rows, from an isolated interpreter that never sees crapkit's src."""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    done = subprocess.run([sys.executable, "-I", str(ORACLE), root, *paths],
                          capture_output=True, text=True, encoding="utf-8", env=env,
                          check=False)
    assert done.returncode == 0, done.stderr
    return [json.loads(line) for line in done.stdout.splitlines()]


def _key(row: dict) -> tuple:
    return row["path"], row["start"], row["long_name"]


def _lines(text: str, first: int, last: int) -> str:
    return "\n".join(text.splitlines()[first - 1:last])


def _context(text: str, rows: list[dict], key: tuple) -> str:
    """The lines of every row on either side, in key's file, whose span holds key's start."""
    path, start = key[0], key[1]
    spans = [(row["start"], row["end"]) for row in rows
             if row["path"] == path and row["start"] <= start <= row["end"]]
    return "\n".join(_lines(text, first, last) for first, last in spans)


def _row_differences(key: tuple, stock: dict | None, mine: dict | None, context: str):
    if stock is None or mine is None:
        return [Difference(*key, "row", stock, mine, context)]
    return [Difference(*key, column, stock[column], mine[column], context)
            for column in COLUMNS if stock[column] != mine[column]]


def differences(stock: list[dict], mine: list[dict], sources: dict) -> list[Difference]:
    by_stock = {_key(row): row for row in stock}
    by_mine = {_key(row): row for row in mine}
    both = [*stock, *mine]
    return [difference for key in sorted(set(by_stock) | set(by_mine))
            for difference in _row_differences(key, by_stock.get(key), by_mine.get(key),
                                               _context(sources[key[0]], both, key))]


def _covers(patch: Patch, difference: Difference) -> bool:
    if not difference.path.lower().endswith(patch.suffixes):
        return False
    if patch.columns is not None and difference.column not in patch.columns:
        return False
    return patch.construct is None or bool(re.search(patch.construct, difference.context))


def excused_by(difference: Difference) -> str | None:
    """The id of the first patch that covers this difference, or None."""
    return next((patch.id for patch in PATCHES if _covers(patch, difference)), None)


def _text(content) -> str:
    return content.decode("utf-8", "replace") if isinstance(content, bytes) else content


def compare(files: dict, measured) -> list[Difference]:
    paths = sorted(files)
    sources = {path: _text(files[path]) for path in paths}
    return differences(stock_rows(measured.root, paths), list(measured.rows), sources)


def _unexcused(found: list[Difference]) -> list[Difference]:
    return [difference for difference in found if excused_by(difference) is None]


# --- the comparison and the patch rule, on hand-written rows -----------------------

def _hand_row(path, start, end, **columns) -> dict:
    base = {"path": path, "start": start, "end": end, "long_name": "f ( )", "nloc": end - start + 1,
            "params": 0, "ccn_std": 1, "ccn_mod": 1, "nesting": 0}
    return {**base, **columns}


def test_a_match_that_moves_only_ccn_is_excused_and_a_moved_end_is_not():
    source = "fn f(k: i32) -> i32 {\n    match k {\n        1 => 1,\n        _ => 0,\n    }\n}\n"
    stock = [_hand_row("a.rs", 1, 6, ccn_std=1, ccn_mod=1)]
    mine = [_hand_row("a.rs", 1, 6, ccn_std=2, ccn_mod=2)]
    found = differences(stock, mine, {"a.rs": source})
    assert [(d.column, excused_by(d)) for d in found] == [("ccn_std", "TRIP-RUST-MATCH"),
                                                         ("ccn_mod", "TRIP-RUST-MATCH")]
    moved = differences(stock, [_hand_row("a.rs", 1, 5)], {"a.rs": source})
    assert [(d.column, excused_by(d)) for d in moved] == [("end", None), ("nloc", None)]


def test_a_rust_let_else_is_excused_as_rust_syntax_and_a_plain_moved_end_is_not():
    source = ("fn f(a: Option<i32>) -> i32 {\n    let Some(x) = a else {\n        return 0;\n"
              "    };\n    x\n}\n")
    stock = [_hand_row("a.rs", 1, 6, ccn_std=1, ccn_mod=1)]
    found = differences(stock, [_hand_row("a.rs", 1, 6, ccn_std=2, ccn_mod=2)], {"a.rs": source})
    assert {excused_by(d) for d in found} == {"TRIP-RUST-SYNTAX"}
    plain = "fn g(k: i32) -> i32 {\n    k + 1\n}\n"
    moved = differences([_hand_row("b.rs", 1, 3)], [_hand_row("b.rs", 1, 2)], {"b.rs": plain})
    assert {excused_by(d) for d in moved} == {None}


def test_a_stock_language_excuses_nothing():
    """Kotlin, which lizard reads and crapkit does not admit, is named by no patch."""
    source = "fun f(k: Int): Int {\n    return if (k > 0) 1 else 0\n}\n"
    found = differences([_hand_row("a.kt", 1, 3)], [_hand_row("a.kt", 1, 3, ccn_std=3)],
                        {"a.kt": source})
    assert [(d.column, excused_by(d)) for d in found] == [("ccn_std", None)]


def test_crapkits_c_family_and_java_readers_excuse_every_column():
    """C, C++, Objective-C and Java run on crapkit's readers: a row stock lizard
    never lists and a moved column both pass, and each names its patch."""
    source = "int f(int k) {\n  return k ? 1 : 0;\n}\n"
    found = differences([_hand_row("a.cpp", 1, 3)],
                        [_hand_row("a.cpp", 1, 3, params=1), _hand_row("b.java", 1, 3)],
                        {"a.cpp": source, "b.java": source})
    assert [(d.path, d.column, excused_by(d)) for d in found] == [
        ("a.cpp", "params", "TRIP-C-FAMILY"), ("b.java", "row", "TRIP-JAVA")]


def test_a_nesting_difference_needs_no_construct_in_javascript_or_rust():
    """crapkit reads nesting off its cognitive pass and lizard off ND, so the column
    moves in functions with none of the constructs the JavaScript and Rust patches
    name; another column moved there is still unexcused."""
    sources = {"a.js": "function f(a) {\n  while (a) { a--; }\n}\n",
               "a.rs": "fn f(a: i32) {\n    while a > 0 {}\n}\n"}
    for path, source in sources.items():
        found = differences([_hand_row(path, 1, 3, nesting=2)],
                            [_hand_row(path, 1, 3, nesting=1, ccn_std=2)], {path: source})
        assert [(d.column, excused_by(d)) for d in found] == [("ccn_std", None),
                                                             ("nesting", "TRIP-NESTING")]


def test_a_js_row_needs_an_arrow_template_or_ternary_near_it():
    arrow = "const f = [\n  (a) => a || 0,\n];\n"
    plain = "function f(a) {\n  return a;\n}\n"
    split = differences([], [_hand_row("a.js", 2, 2)], {"a.js": arrow})
    assert [excused_by(d) for d in split] == ["TRIP-JS-EXPRESSIONS"]
    moved = differences([_hand_row("b.js", 1, 3)], [_hand_row("b.js", 1, 2)], {"b.js": plain})
    assert {excused_by(d) for d in moved} == {None}


def test_a_row_only_crapkit_lists_reads_the_stock_span_that_holds_it():
    source = "function t(n) {\n  return `${n}\n  x`;\n}\nfunction after() {\n  return 1;\n}\n"
    stock = [_hand_row("a.ts", 1, 7)]
    mine = [_hand_row("a.ts", 1, 4), _hand_row("a.ts", 5, 7, long_name="after ( )")]
    found = differences(stock, mine, {"a.ts": source})
    assert {excused_by(d) for d in found} == {"TRIP-JS-EXPRESSIONS"}


# --- probes (push) and the full corpus (nightly) ------------------------------------

@pytest.fixture(scope="module")
def probe_differences(oracle, probe_inventory):
    oracle("lizard")
    return compare(analysis_tables.probe_files(), probe_inventory)


def test_every_probe_difference_is_excused(probe_differences):
    assert _unexcused(probe_differences) == []


@pytest.mark.parametrize("patch", PATCHES, ids=lambda patch: patch.id)
def test_every_patch_still_excuses_a_probe_difference(probe_differences, patch):
    assert any(excused_by(d) == patch.id for d in probe_differences), (
        f"{patch.id} excuses nothing: stock lizard now reads these probes as crapkit does; "
        "retire the patch or add a probe that shows it")
