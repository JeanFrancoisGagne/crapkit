"""Stock lizard 1.24.0 as a tripwire: crapkit changes only what its patch list says.

This counts as zero oracles. Stock lizard is what crapkit is built on, so it
agrees with crapkit by construction wherever crapkit leaves its readers alone,
and it is wrong where crapkit fixed it. What it catches is drift: a row that
moves in a language crapkit leaves to lizard's stock readers.

oracles/lizard_tripwire.py reads the same files through lizard's own API in a
process that imports no crapkit. The check pairs rows by (path, start,
long_name):

- C, C++, Objective-C, Java, Go, Swift and Zig: every row and every column
  (start, end, nloc, params, ccn_std, ccn_mod, nesting) equals stock lizard's.
- The languages in PATCHES: a difference passes only when the patch covers its
  column and its source shows the patch's construct. Each patch must still
  excuse at least one probe difference, so a patch upstream has made redundant
  shows up here.

Push reads the probe files; nightly reads the full-corpus members of the
stock-reader languages.
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
STOCK_SUFFIXES = (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".m", ".mm", ".java", ".go",
                  ".swift", ".zig")
JS_FAMILY = (".js", ".cjs", ".mjs", ".jsx", ".ts", ".tsx", ".vue")


@dataclass(frozen=True)
class Patch:
    id: str
    suffixes: tuple
    columns: tuple | None  # None: rows and every column
    construct: str | None  # a regex over the function's lines; None: any source
    source: str


PATCHES = (
    Patch("TRIP-PY", (".py",), None, None,
          "README.md 'analysis version 11' paragraph; docs/agent-json.md `nesting` row: a "
          "Python row reads nesting off crapkit's cognitive pass"),
    Patch("TRIP-SHELL", (".sh", ".bash"), None, None,
          "README.md 'Three readers are crapkit's own. lizard ships none for shell or "
          "PowerShell'"),
    Patch("TRIP-POWERSHELL", (".ps1", ".psm1"), None, None,
          "README.md 'Three readers are crapkit's own. lizard ships none for shell or "
          "PowerShell'"),
    Patch("TRIP-RUST-MATCH", (".rs",), ("ccn_std", "ccn_mod"), r"\bmatch\b",
          "README.md 'Its Rust reader scores a 7-arm match as ccn 2 (filed as lizard #494)'"),
    Patch("TRIP-JS-EXPRESSIONS", JS_FAMILY, None, r"=>|`|\?[^\n]*:",
          "README.md 'Expression arrows in arrays and argument lists are measured "
          "separately'; docs/architecture/2026-09-06/arrow-reader-review.md line 7 (comma, "
          "bracket and ternary-colon states)"),
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


def test_a_stock_language_excuses_nothing():
    source = "int f(int k) {\n  return k ? 1 : 0;\n}\n"
    found = differences([_hand_row("a.c", 1, 3)], [_hand_row("a.c", 1, 3, ccn_std=2)],
                        {"a.c": source})
    assert [(d.column, excused_by(d)) for d in found] == [("ccn_std", None)]


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


def test_stock_reader_languages_equal_stock_lizard_on_the_probes(probe_differences,
                                                                 probe_inventory):
    compared = [row for row in probe_inventory.rows if row["path"].endswith(STOCK_SUFFIXES)]
    assert {Path(row["path"]).suffix for row in compared} >= {".c", ".cpp", ".m", ".java",
                                                               ".go", ".swift", ".zig"}
    assert _unexcused(probe_differences) == []


@pytest.mark.parametrize("patch", PATCHES, ids=lambda patch: patch.id)
def test_every_patch_still_excuses_a_probe_difference(probe_differences, patch):
    assert any(excused_by(d) == patch.id for d in probe_differences), (
        f"{patch.id} excuses nothing: stock lizard now reads these probes as crapkit does; "
        "retire the patch or add a probe that shows it")


@pytest.mark.nightly
@pytest.mark.parametrize("language", ["go", "java", "c", "cpp", "objc", "swift", "zig"])
def test_stock_reader_languages_equal_stock_lizard_on_the_corpus(oracle, corpus_language,
                                                                 language):
    oracle("lizard")
    files, measured = corpus_language(language)
    assert measured.rows
    assert _unexcused(compare(files, measured)) == []
