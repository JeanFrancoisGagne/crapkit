"""JavaScript and TypeScript functions as crapkit lists them, against the
TypeScript 6.0.2 compiler (oracles/ts_functions.cjs).

The compiler lists every function-like node with a body: declarations,
expressions, arrows, methods, accessors and constructors. Three comparisons
per file, each reporting the first place crapkit and the compiler part:

- declarations: how many functions start on each line;
- spans: where each function that starts on a shared line ends;
- params: how many parameters it declares.

Push reads the probe files and the named shapes of analysis_shapes. A file
whose first difference is a recorded defect is a strict xfail on its rulings
row; a difference crapkit makes on purpose is a definition row.

Nightly reads every JavaScript, TypeScript and Vue file of the full corpus
that crapkit scores (docs/configuration.md: a path with a component named
test, tests or __tests__, or one opening on a dot, leaves the corpus first).
It joins rows to functions as test_js_corpus_oracles does and compares every
paired function's end; each difference is set aside by a rulings row or
reported (see the comment above ROW).
"""
from collections import Counter
from dataclasses import dataclass
import importlib
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from accuracy.analysis_oracles import (analysis_corpora, analysis_js, analysis_shapes,
                                       analysis_tables)
from accuracy.analysis_oracles.oracles import node_oracles
from accuracy.kit import oracles, rulings, runlog

pytestmark = pytest.mark.process
SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue")


def _files() -> dict:
    return analysis_js.push_files()


@pytest.fixture(scope="module")
def compiled(js_push):
    return js_push.compiled


@pytest.fixture(scope="module")
def measured(js_push):
    return js_push.measured


# --- the three comparisons -----------------------------------------------------------------------

def first_count_difference(rows: list[dict], fns: list) -> tuple[int, int] | None:
    """(crapkit's rows, the compiler's functions) on the first line where the
    two counts differ."""
    ours, theirs = Counter(row["start"] for row in rows), Counter(fn.start for fn in fns)
    lines = sorted(set(ours) | set(theirs))
    return next(((ours[line], theirs[line]) for line in lines if ours[line] != theirs[line]), None)


def first_end_difference(rows: list[dict], fns: list) -> tuple[int, int] | None:
    """(crapkit's end minus the compiler's, 0) for the first function whose end differs."""
    moved = [row["end"] - fn.end for row, fn in sorted(analysis_js.pairs(rows, fns), key=_start)]
    return next(((shift, 0) for shift in moved if shift), None)


def first_params_difference(rows: list[dict], fns: list) -> tuple[int, int] | None:
    pairs = sorted(analysis_js.pairs(rows, fns), key=_start)
    return next(((row["params"], fn.params) for row, fn in pairs if row["params"] != fn.params),
                None)


def _start(pair: tuple) -> int:
    return pair[0]["start"]


# The first difference of each file that has one, and the rulings row that
# records it. A defect row is a strict xfail until the fix lands.
DECLARATIONS = {"ts/generic.ts": "D1a", "ts/regex.ts": "D1c", "vue/label.vue": "AO-VUE-ROWS",
                "vue/equivalence.vue": "AO-VUE-ROWS",
                "js/object_methods.js": "AO-JS-OBJECT-MEMBERS",
                "js/template_arrow.js": "AO-JS-TEMPLATE-ARROW", "js/overloads.ts": "AO-TS-OVERLOADS"}
ENDS = {"ts/flow.ts": "AO-TS-END-SPAN", "ts/shapes.ts": "AO-TS-END-SPAN",
        "ts/sonar.ts": "AO-TS-END-SPAN", "ts/templates.ts": "AO-TS-END-SPAN",
        "ts/fntype.ts": "D1b-SPAN", "ts/regex.ts": "D1c-SPAN"}
PARAMS = {**{path: "AO-JS-ARROW-PARAMS" for path in
             ("js/callbacks.js", "jsx/list.jsx", "js/sibling_arrows.js", "js/arrows_in_calls.js",
              "js/object_methods.js")},
          "ts/shapes.ts": "AO-TS-PARAMS-DESTRUCTURE", "ts/fntype.ts": "D1b"}


def _params(table: dict) -> list:
    return [pytest.param(path, id=path, marks=analysis_tables.marks(table.get(path, "")))
            for path in sorted(_files())]


def _judge(found: tuple | None, ruling: str) -> None:
    """No difference, or exactly the one the file's rulings row records."""
    if ruling:
        analysis_tables.check(*map(str, found or ("none", "none")), ruling)
    else:
        assert found is None, f"crapkit and the compiler part: crapkit {found[0]}, compiler {found[1]}"


def _both(measured, compiled, path: str) -> tuple[list, list]:
    return measured.in_file(path), [fn for fn in compiled if fn.path == path]


@pytest.mark.parametrize("path", _params(DECLARATIONS))
def test_declarations_have_rows(path, measured, compiled):
    _judge(first_count_difference(*_both(measured, compiled, path)), DECLARATIONS.get(path, ""))


@pytest.mark.parametrize("path", _params(ENDS))
def test_spans_end_where_the_compiler_ends_them(path, measured, compiled):
    _judge(first_end_difference(*_both(measured, compiled, path)), ENDS.get(path, ""))


@pytest.mark.parametrize("path", _params(PARAMS))
def test_params_match_the_compiler(path, measured, compiled):
    _judge(first_params_difference(*_both(measured, compiled, path)), PARAMS.get(path, ""))


def test_sibling_arrows_are_separate_functions(measured, compiled):
    """Two arrows on one line are two functions with one start line (R21)."""
    rows, fns = _both(measured, compiled, "js/sibling_arrows.js")

    assert Counter(row["start"] for row in rows)[1] == Counter(fn.start for fn in fns)[1] == 2


# --- a switch whose last case ends in a bare return (calc-bug analysis-oracles-160) ---------------

SWITCH_RETURN = ("function kind(c) {\n  switch (c) {\n    case 1:\n      return 'one'\n"
                 "    default:\n      return 'many'\n  }\n}\n\nfunction after() {\n  return 1\n}\n")
AFTER_LINE = 10


@rulings.applies("AO-JS-SWITCH-RETURN")
def test_a_switch_ending_in_a_bare_return_closes_its_function(oracle, measure_set, tmp_path):
    """ECMA-262 sec. 15.2: after is a function declaration of its own."""
    oracle("typescript")
    files = {"cases/switch_return.js": SWITCH_RETURN}
    paths = node_oracles.write(files, tmp_path)
    compiled = node_oracles.functions(oracles.node_modules("push"), tmp_path, paths)
    rows = measure_set(files).in_file("cases/switch_return.js")

    rulings.pin_ruling("AO-JS-SWITCH-RETURN",
                       crapkit=sum(row["start"] == AFTER_LINE for row in rows),
                       oracle=sum(fn.start == AFTER_LINE for fn in compiled))


# --- nightly: the full corpus -----------------------------------------------------------------

TEST_DIRECTORIES = {"test", "tests", "__tests__"}


def scored(path: str) -> bool:
    """Whether crapkit's corpus keeps `path` (docs/configuration.md "[exclude]")."""
    folders = path.split("/")[:-1]
    return not any(part.lower() in TEST_DIRECTORIES or part.startswith(".") for part in folders)


@pytest.mark.parametrize("path, kept", [
    ("src/a.ts", True), ("src/testing/a.ts", True), ("latest/a.ts", True),
    ("src/.eslintrc.js", True), ("src/test/a.ts", False), ("src/Tests/a.ts", False),
    ("pkg/__tests__/a.tsx", False), (".github/gen.js", False), ("src/.hidden/a.ts", False)])
def test_scored_follows_the_documented_exclusions(path, kept):
    assert scored(path) is kept


def _paths(measured, compiled) -> list[str]:
    return sorted({fn.path for fn in compiled} | {row["path"] for row in measured.rows})


# Declarations go through test_js_corpus_oracles' join: a line where crapkit
# and the compiler list as many functions pairs them in order, and a row or a
# function left over is set aside only when its file rule or a shape in that
# module's SHAPES table with the "row" column explains it. Each pair's end is
# then compared. A differing end is set aside when
# - it is the corpus function SWITCH_RETURNS names, at the ends pinned there
#   (AO-JS-SWITCH-RETURN);
# - the function has a return type and crapkit ends it on the line of the
#   first token after its last character, exactly where AO-TS-END-SPAN says;
# - its file rule, or a shape that throws off rows, reaches it: held by the
#   function or by a function inside it, or marked on one of its lines.
ROW = "row"
NO_SHAPE = ("", ())
# express lib/response.js: stringify's replace callback, whose switch ends in
# `return c` with no semicolon: (crapkit's end, the compiler's).
SWITCH_RETURNS = {("express/lib/response.js", 1037): (1050, 1049)}
# Fewest paired functions whose end the corpus check compares.
MINIMUM_ENDS = 950
SKIPPED = re.compile(r"(?:\s+|//[^\n]*|/\*.*?\*/)*", re.S)


@dataclass(frozen=True)
class Place:
    """One corpus file as the end check reads it."""
    fns: list
    marks: list  # [(shape, line, reach)] from ts_functions.cjs
    text: str


def typed_end(text: str, line: int, column: int) -> int | None:
    """AO-TS-END-SPAN's end for a function whose last character sits at
    (line, UTF-16 column): the line of the first token after it, whitespace and
    comments skipped (ECMA-262 sec. 12.2 to 12.4); None when no token follows."""
    lines = text.split("\n")
    head = lines[line - 1].encode("utf-16-le")[:2 * (column + 1)].decode("utf-16-le")
    at = SKIPPED.match(text, sum(map(len, lines[:line - 1])) + line - 1 + len(head)).end()
    return text.count("\n", 0, at) + 1 if at < len(text) else None


def _inside(fn, fns: list) -> list:
    """fn and every function whose span lies inside fn's."""
    return [inner for inner in fns if fn.start <= inner.start and inner.end <= fn.end]


def _names_within(fn, place: Place) -> list:
    held = [name for inner in _inside(fn, place.fns) for name in inner.shapes]
    return held + [name for name, at, _ in place.marks if fn.start <= at <= fn.end]


def end_reason(fn, place: Place, shapes: dict, files: dict) -> str:
    """The rulings ids that leave fn's end out, joined by +: its file's rule
    and every shape with the row column held by fn or a function inside it, or
    marked on one of its lines."""
    ids = {shapes[name][0] for name in _names_within(fn, place)
           if ROW in shapes.get(name, NO_SHAPE)[1]}
    rule = files.get("." + fn.path.rpartition(".")[2].lower())
    return "+".join(sorted(ids | ({rule} - {None})))


def end_set_aside(row: dict, fn, place: Place, table) -> str:
    """Why an end crapkit and the compiler disagree on is set aside, or ""
    when no rulings row records it. `table` holds SHAPES and FILES."""
    ends = (row["end"], fn.end)
    if SWITCH_RETURNS.get((fn.path, fn.start)) == ends:
        return "AO-JS-SWITCH-RETURN"
    if fn.return_type and row["end"] == typed_end(place.text, fn.end, fn.end_column):
        return "AO-TS-END-SPAN"
    return end_reason(fn, place, table.SHAPES, table.FILES)


def _judge_end(outcome, row: dict, fn, place: Place, table) -> None:
    if row["end"] == fn.end:
        outcome.compared["end"] += 1
        return
    reason = end_set_aside(row, fn, place, table)
    if reason:
        outcome.set_aside[("end", reason)] += 1
    else:
        outcome.problems.append(("end", fn.path, fn.start, fn.name, row["end"], fn.end))


def _js_corpus():
    """test_js_corpus_oracles: its Outcome, join, SHAPES and FILES. Read when
    the nightly check runs, so the push checks above import nothing from it."""
    return importlib.import_module("accuracy.analysis_oracles.test_js_corpus_oracles")


def corpus_outcome(files: dict, measured, listing: tuple):
    """Rows joined to the compiler's functions file by file, then every pair's
    end judged: an Outcome of compared counts, set-asides and problems."""
    table = _js_corpus()
    compiled, marks = listing
    outcome = table.Outcome()
    for path in _paths(measured, compiled):
        place = Place(analysis_js.in_file(compiled, path), marks.get(path, []),
                      files[path].decode("utf-8", "replace"))
        for row, fn in table.join(outcome, path, measured.in_file(path), place.fns, place.marks):
            _judge_end(outcome, row, fn, place, table)
    return outcome


@pytest.mark.nightly
def test_corpus_declarations_and_spans_match_the_compiler(oracle, measure_set, tmp_path):
    """Every JS, TS and Vue file of the full corpus that crapkit scores: each
    line holds as many rows as the compiler finds functions there, and each
    paired function ends where the compiler ends it, unless a rulings row sets
    it aside. Set-asides are counted in the run log, never hidden."""
    oracle("typescript")
    every = analysis_corpora.corpus_files(SUFFIXES)
    files = {path: data for path, data in every.items() if scored(path)}
    runlog.note("skipped_files", oracle="typescript: test and dot directories",
                count=len(every) - len(files))
    paths = node_oracles.write(files, tmp_path)
    listing = node_oracles.listing(oracles.node_modules("push"), tmp_path, paths)
    outcome = corpus_outcome(files, measure_set(files), listing)
    runlog.note("skipped_files", oracle="typescript: rows and ends set aside",
                count=sum(outcome.set_aside.values()))

    assert outcome.problems == []
    assert outcome.compared["end"] >= MINIMUM_ENDS, dict(outcome.compared)


# --- the end check's own rules, by hand ------------------------------------------------------

EMOJI_LINE = 'function h(): string { return "\U0001F600" };\nnext();\n'


@pytest.mark.parametrize("text, line, column, expected", [
    ("function f(): number {\n  return 1;\n}\n\n// c\n/* a\n b */\nnext();\n", 3, 0, 8),
    ("const g = (): void => {\n};\n", 2, 0, 2),
    # The emoji is two UTF-16 units: the brace sits at column 35, the `;` after it on line 1.
    (EMOJI_LINE, 1, 35, 1),
    ("function k(): void {\n}\n  // tail\n", 2, 0, None)])
def test_typed_end_is_the_line_of_the_next_token(text, line, column, expected):
    assert typed_end(text, line, column) == expected


# The probe files whose first end difference is AO-TS-END-SPAN.
TYPED_END_FILES = sorted(path for path, ruling in ENDS.items() if ruling == "AO-TS-END-SPAN")


def _typed_moves(measured, compiled, path: str) -> list[tuple]:
    """(crapkit's end, typed_end's line) for each typed function of a probe
    file that crapkit ends elsewhere than the compiler."""
    text = _files()[path].decode("utf-8")
    return [(row["end"], typed_end(text, fn.end, fn.end_column))
            for row, fn in analysis_js.pairs(*_both(measured, compiled, path))
            if fn.return_type and row["end"] != fn.end]


def test_typed_ends_match_crapkit_on_the_probes(measured, compiled):
    """Every typed function crapkit ends elsewhere than the compiler, on the
    probe files ENDS records under AO-TS-END-SPAN, ends on typed_end's line."""
    moved = {path: _typed_moves(measured, compiled, path) for path in TYPED_END_FILES}
    wrong = {path: [pair for pair in pairs if pair[0] != pair[1]] for path, pairs in moved.items()}

    assert all(moved.values()), moved
    assert wrong == dict.fromkeys(TYPED_END_FILES, [])


def _fn(start: int, end: int, *shapes: str, path: str = "a.ts", typed: bool = False):
    return SimpleNamespace(path=path, start=start, end=end, end_column=0, shapes=set(shapes),
                           return_type=typed, name="f")


HAND_TABLE = SimpleNamespace(
    SHAPES={"get_set_method": ("R-GET", ("row", "ccn_std")), "function_type": ("R-TYPE", ("row",)),
            "paren_value": ("R-PAREN", ("ccn_std",)), "jsx_spread": ("R-SPREAD", ("row",))},
    FILES={".vue": "R-VUE"})
HAND_PLACE = Place([_fn(1, 10), _fn(3, 5, "get_set_method"), _fn(12, 14, "jsx_spread")],
                   [("function_type", 7, "line"), ("paren_value", 8, "own"),
                    ("jsx_spread", 11, "after")],
                   "function f(): void {\n}\n\nnext();\n")


@pytest.mark.parametrize("fn, row_end, expected", [
    # A row shape held inside and one marked on a line; a value-only shape and
    # shapes outside the span leave nothing.
    (_fn(1, 10), 9, "R-GET+R-TYPE"),
    (_fn(12, 14), 13, "R-SPREAD"),
    (_fn(20, 22), 21, ""),
    (_fn(1, 2, path="b.vue"), 3, "R-VUE"),
    # Typed: crapkit's end must be the next token's line (4), else the shapes decide.
    (_fn(1, 2, typed=True), 4, "AO-TS-END-SPAN"),
    (_fn(1, 2, typed=True), 3, ""),
    # The pinned switch function at its pinned ends only.
    (_fn(1037, 1049, path="express/lib/response.js"), 1050, "AO-JS-SWITCH-RETURN"),
    (_fn(1037, 1049, path="express/lib/response.js"), 1051, "")])
def test_end_set_aside_names_the_row_that_explains_it(fn, row_end, expected):
    assert end_set_aside({"end": row_end}, fn, HAND_PLACE, HAND_TABLE) == expected
