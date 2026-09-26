"""JavaScript and TypeScript functions as crapkit lists them, against the
TypeScript 6.0.2 compiler (oracles/ts_functions.cjs).

The compiler lists every function-like node with a body: declarations,
expressions, arrows, methods, accessors and constructors. Three comparisons
per file, each reporting the first place crapkit and the compiler part:

- declarations: how many functions start on each line;
- spans: where each function that starts on a shared line ends;
- params: how many parameters it declares.

Push reads the probe files and the named shapes of analysis_shapes; nightly
reads every JavaScript, TypeScript and Vue file of the full corpus that
crapkit scores (docs/configuration.md: a path with a component named test,
tests or __tests__, or one opening on a dot, leaves the corpus first). A file
whose first difference is a recorded defect is a strict xfail on its rulings
row; a difference crapkit makes on purpose is a definition row.
"""
from collections import Counter
from pathlib import Path

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


def _differences_in(measured, compiled, path: str) -> tuple:
    rows, fns = measured.in_file(path), analysis_js.in_file(compiled, path)
    return first_count_difference(rows, fns), first_end_difference(rows, fns)


def _paths(measured, compiled) -> list[str]:
    return sorted({fn.path for fn in compiled} | {row["path"] for row in measured.rows})


def _corpus_differences(measured, compiled) -> dict:
    found = {path: _differences_in(measured, compiled, path)
             for path in _paths(measured, compiled)}
    return {path: pair for path, pair in found.items() if pair != (None, None)}


@pytest.mark.nightly
def test_corpus_declarations_and_spans_match_the_compiler(oracle, measure_set, tmp_path):
    """Every JS, TS and Vue file of the full corpus that crapkit scores, reported
    file by file with its first difference; the files a defect covers are
    counted, not hidden: the assertion names every file whose difference no
    rulings row records."""
    oracle("typescript")
    every = analysis_corpora.corpus_files(SUFFIXES)
    files = {path: data for path, data in every.items() if scored(path)}
    runlog.note("skipped_files", oracle="typescript: test and dot directories",
                count=len(every) - len(files))
    paths = node_oracles.write(files, tmp_path)
    compiled = node_oracles.functions(oracles.node_modules("push"), tmp_path, paths)
    differences = _corpus_differences(measure_set(files), compiled)
    runlog.note("skipped_files", oracle="typescript: files with a difference",
                count=len(differences))

    assert differences == {}
