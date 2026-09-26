"""JavaScript, TypeScript and Vue ccn, cognitive and nesting against ESLint and
sonarjs over the full corpus's JS/TS/Vue members.

The push modules (test_complexity_oracles, test_cognitive_oracles and
test_nesting_oracles) read the probes. This one reads every .js, .jsx, .mjs,
.cjs, .ts, .tsx and .vue file of express, ky, zod, shadcn-ui and element-plus
that crapkit scores, one member per test: one node process lists the
functions with the TypeScript compiler (oracles/ts_functions.cjs) and one runs
ESLint's complexity rule (classic and modified), max-depth and sonarjs
cognitive-complexity over the member's file list (oracles/eslint_probe.mjs).

Each compiler function is joined to crapkit's row by path and start line, in
order along a line (analysis_js). A function or a row left unjoined is a
problem. Each value goes through analysis_js.ORACLES: its transform and its
set-asides are rulings rows that the push modules pin by hand cases. On top of
those, SHAPES names each construct crapkit is known to misread (ts_functions.cjs
tags every function holding one) and the columns it throws off: a function
holding one is set aside for those columns and counted, never compared. Every
shape is a rulings row whose probe pins crapkit's value by hand.
"""
from collections import Counter
from dataclasses import dataclass, field

import pytest

from accuracy.analysis_oracles import analysis_corpora, analysis_js, analysis_tables
from accuracy.analysis_oracles.oracles import node_oracles
from accuracy.kit import oracles, rulings, runlog

pytestmark = [pytest.mark.nightly, pytest.mark.process, pytest.mark.platform("linux")]

MEMBERS = ("express", "ky", "zod", "shadcn-ui", "element-plus")
MODES = tuple(analysis_js.ORACLES)
ROW = "row"
VALUES = ("ccn_std", "ccn_mod", "cognitive", "nesting")
EVERY = (ROW, *VALUES)
# Fewest functions each mode must compare per member once shapes are set aside.
MINIMUM = {"express": 50, "ky": 25, "zod": 70, "shadcn-ui": 3, "element-plus": 8}

# ts_functions.cjs shape -> (rulings row, the columns a function holding it leaves out).
SHAPES = {
    "paren_value": ("AO-JS-PAREN-VALUE", VALUES),
    "paren_value_call": ("AO-JS-PAREN-VALUE-CALL", EVERY),
    "name_spelled": ("AO-JS-COG-RECURSION-NAME", ("cognitive",)),
    "keyword_name": ("AO-JS-KEYWORD-NAME", VALUES),
    "function_word_key": ("AO-JS-KEYWORD-FUNCTION-KEY", EVERY),
    "regex_as_code": ("AO-JS-REGEX-AS-CODE", EVERY),
    "optional_call": ("AO-JS-OPTIONAL-CALL-END", EVERY),
    "arrow_body_below": ("AO-JS-ARROW-BODY-BELOW", EVERY),
    "type_question": ("AO-TS-TYPE-QUESTION", VALUES),
    "function_type": ("AO-TS-FUNCTION-TYPE-ALIAS", EVERY),
    "function_type_nested": ("AO-TS-FUNCTION-TYPE-ROW", EVERY),
    "typed_initializer": ("AO-TS-FUNCTION-TYPE-VAR", EVERY),
    "get_set_method": ("AO-JS-OBJECT-MEMBERS", EVERY),
    "generic_declaration": ("D1a", EVERY),
    "ternary_call_row": ("AO-JS-TERNARY-CALL-ROW", (ROW,)),
    "jsx_spread": ("AO-JSX-SPREAD-ROWS", EVERY),
    "optional_chain_tsx": ("AO-TSX-OPTIONAL-CHAIN-ND", ("nesting",)),
    "foreign_keyword": ("AO-JS-FOREIGN-KEYWORD", VALUES),
    "paren_type_param": ("AO-TS-PAREN-TYPE-PARAM", EVERY),
    "overload_signature": ("AO-TS-OVERLOADS", (ROW,)),
    "braceless_if": ("AO-ND-BRACELESS", ("nesting",)),
    "braceless_body": ("AO-COG-BRACELESS-BODY", ("cognitive",)),
}
# A file whose every row crapkit misplaces: the suffix and its rulings row.
FILES = {".vue": "AO-VUE-ROWS"}

# docs/configuration.md "Test directories are excluded unconditionally": a path
# with a component named test, tests or __tests__, or one opening on a dot,
# leaves crapkit's corpus before any glob is read.
TEST_DIRECTORIES = {"test", "tests", "__tests__"}


def scored(path: str) -> bool:
    folders = path.split("/")[:-1]
    return not any(part.lower() in TEST_DIRECTORIES or part.startswith(".") for part in folders)


# --- one member: its files, the compiler's functions, ESLint's numbers, crapkit's rows -----------

@dataclass(frozen=True)
class Member:
    compiled: list
    marks: dict  # path -> [(shape, line, reach)]
    measured: object
    numbers: dict  # mode -> {Fn: number}
    fatal: dict  # path -> the parser's message, for a file ESLint could not read


def member_run(files: dict, work, measured) -> Member:
    paths = node_oracles.write(files, work)
    modules = oracles.node_modules("push")
    compiled, marks = node_oracles.listing(modules, work, paths)
    found, fatal = node_oracles.lint(modules, work, MODES, paths)
    return Member(compiled, marks, measured,
                  {mode: analysis_js.numbers(compiled, found[mode], mode) for mode in MODES},
                  fatal)


@pytest.fixture(scope="module")
def member(full_corpus, measure_set, tmp_path_factory, oracle):
    for name in ("typescript", "eslint", "eslint-plugin-sonarjs"):
        oracle(name)

    def run(name: str) -> Member:
        files = analysis_corpora.member_files(full_corpus, name, analysis_js.SUFFIXES)
        kept = {path: data for path, data in files.items() if scored(path)}
        runlog.note("skipped_files", oracle=f"{name}: test and dot directories",
                    count=len(files) - len(kept))
        return member_run(kept, tmp_path_factory.mktemp(name), measure_set(kept))
    return run


# --- the join and the comparison ------------------------------------------------------------

@dataclass
class Outcome:
    compared: Counter = field(default_factory=Counter)
    set_aside: Counter = field(default_factory=Counter)
    problems: list = field(default_factory=list)


def _ids(shapes, column: str) -> set:
    return {SHAPES[name][0] for name in shapes if name in SHAPES and column in SHAPES[name][1]}


def _file_rules(path: str) -> set:
    rule = FILES.get("." + path.rpartition(".")[2].lower())
    return {rule} if rule else set()


def shape_reason(fn, column: str) -> str:
    """The rulings ids that leave fn's `column` out, joined by +: its file's
    rule and its shapes."""
    return "+".join(sorted(_ids(fn.shapes, column) | _file_rules(fn.path)))


def _held(fns: list, line: int) -> list:
    """The shapes of every function whose span holds `line`."""
    return [name for fn in fns if fn.start <= line <= fn.end for name in fn.shapes]


def _marked(marks: list, line: int) -> list:
    """The shapes on `line`, and those above it that throw off the rest of the file."""
    return [name for name, at, reach in marks if at == line or (reach == "after" and at <= line)]


def row_reason(row: dict, fns: list, marks: list) -> str:
    """Why a row with no compiler function beside it is set aside: its file's
    rule, a shape of a function holding its line, a shape on its line, or one
    above it that throws off the rest of the file."""
    names = _held(fns, row["start"]) + _marked(marks, row["start"])
    return "+".join(sorted(_ids(names, ROW) | _file_rules(row["path"])))


def _unjoined(outcome: Outcome, reason: str, problem: tuple) -> None:
    if reason:
        outcome.set_aside[(ROW, reason)] += 1
    else:
        outcome.problems.append(problem)


def _unjoined_line(outcome: Outcome, place: tuple, ours: list, theirs: list, context: tuple):
    """Every row and function of a line whose counts differ: set aside or a problem."""
    for row in ours:
        _unjoined(outcome, row_reason(row, *context),
                  (ROW, *place, row["long_name"], "no function"))
    for fn in theirs:
        _unjoined(outcome, shape_reason(fn, ROW), (ROW, *place, fn.name, "no row"))


def _starts(rows: list, fns: list) -> list:
    return sorted({row["start"] for row in rows} | {fn.start for fn in fns})


def join(outcome: Outcome, path: str, rows: list, fns: list, marks: list) -> list:
    """(row, function) pairs of one file, zipped on each line where both list
    as many; everything else is set aside or a problem."""
    joined = []
    for line in _starts(rows, fns):
        ours, theirs = analysis_js.rows_on(rows, line), analysis_js.fns_on(fns, line)
        if len(ours) == len(theirs):
            joined += zip(ours, theirs)
        else:
            _unjoined_line(outcome, (path, line), ours, theirs, (fns, marks))
    return joined


def compare(outcome: Outcome, row: dict, fn, mode: str, numbers: dict) -> None:
    oracle = analysis_js.ORACLES[mode]
    reason = shape_reason(fn, oracle.column) or analysis_js.aside(fn, oracle)
    if reason:
        outcome.set_aside[(mode, reason)] += 1
        return
    want = analysis_js.expected(fn, oracle, numbers)
    outcome.compared[mode] += 1
    if row[oracle.column] != want:
        outcome.problems.append((mode, fn.path, fn.start, fn.name, row[oracle.column], want))


def _paths(run: Member) -> list:
    return sorted({fn.path for fn in run.compiled} | {row["path"] for row in run.measured.rows})


def _compare_pairs(outcome: Outcome, pairs: list, numbers: dict) -> None:
    for row, fn in pairs:
        for mode in MODES:
            compare(outcome, row, fn, mode, numbers[mode])


def judge(run: Member) -> Outcome:
    outcome = Outcome(problems=[("fatal", path, message)
                                for path, message in sorted(run.fatal.items())])
    for path in _paths(run):
        pairs = join(outcome, path, run.measured.in_file(path),
                     analysis_js.in_file(run.compiled, path), run.marks.get(path, []))
        _compare_pairs(outcome, pairs, run.numbers)
    return outcome


@pytest.mark.parametrize("name", MEMBERS)
def test_member_matches_eslint_and_sonarjs(name, member):
    """Every function the compiler lists has crapkit's row, and each of
    ccn_std, ccn_mod, cognitive and nesting equals ESLint's or sonarjs's number
    after the documented transform, unless a rulings row sets it aside."""
    outcome = judge(member(name))
    runlog.note("skipped_files", oracle=f"ESLint and sonarjs on {name}: set aside",
                count=sum(outcome.set_aside.values()))

    assert outcome.problems == []
    assert {mode: outcome.compared[mode] >= MINIMUM[name] for mode in MODES} == \
        dict.fromkeys(MODES, True), dict(outcome.compared)


# --- each shape and transform pinned by a case: crapkit's value and the oracle's -----------------

# Shape -> (file, line, column). The file is a probe under probes/ (a hand
# probe pins crapkit's value there too) or one of CASES below; the line is
# the function's start, or for a row with no function the row's line. The
# compiler must find the shape there, and its rulings row must hold crapkit's
# value and the oracle's: ESLint's or sonarjs's number after its transform, or
# the compiler's end or count of functions on the line.
SHAPE_CASES = {
    "paren_value": ("js/corpus_paren_value.js", 1, "ccn_std"),
    "paren_value_call": ("js/corpus_paren_call.js", 1, "end"),
    "name_spelled": ("js/corpus_recursion_name.js", 1, "cognitive"),
    "keyword_name": ("js/corpus_keyword_member.js", 1, "ccn_std"),
    "function_word_key": ("cases/async_key.ts", 1, "end"),
    "regex_as_code": ("ts/corpus_regex_code.ts", 1, "ccn_std"),
    "optional_call": ("ts/corpus_optional_call.ts", 1, "end"),
    "arrow_body_below": ("ts/corpus_arrow_below.ts", 1, "ccn_std"),
    "type_question": ("ts/corpus_type_question.ts", 1, "ccn_std"),
    "function_type": ("cases/function_type_alias.ts", 3, "rows"),
    "function_type_nested": ("ts/corpus_function_type.ts", 1, "end"),
    "typed_initializer": ("cases/typed_initializer.ts", 1, "rows"),
    "get_set_method": ("cases/get_method.js", 2, "rows"),
    "generic_declaration": ("ts/generic.ts", 1, "rows"),
    "ternary_call_row": ("ts/corpus_ternary_call.ts", 5, "rows"),
    "optional_chain_tsx": ("tsx/corpus_optional_chain.tsx", 1, "nesting"),
    "jsx_spread": ("cases/spread.tsx", 1, "rows"),
    "foreign_keyword": ("js/corpus_def_param.js", 1, "nesting"),
    "paren_type_param": ("ts/corpus_paren_type.ts", 1, "rows"),
    "overload_signature": ("cases/overloads.ts", 1, "rows"),
    "braceless_if": ("js/corpus_braceless_if.js", 1, "nesting"),
    "braceless_body": ("js/corpus_braceless_body.js", 1, "cognitive"),
}
# A JSX spread probe cannot sit under probes/tsx: the function it misreads is
# the one after it, and test_metamorphic_source appends one to every probe.
CASES = {
    "cases/spread.tsx": ("export function Card() {\n  return <div {...props} />\n}\n\n"
                         "export function sink(a) {\n  return a\n}\n"),
    "cases/typed_initializer.ts": ("export const parse: (e: E) => P = (e) => {\n  if (e) {\n"
                                   "    return 1;\n  }\n  return 0;\n};\n"),
    "cases/get_method.js": ("export const api = {\n  get(k) {\n    return k;\n  },\n};\n"),
    "cases/async_key.ts": ("export function context(i: I) {\n  const ctx = { async: true };\n"
                           "  return ctx;\n}\n\nexport function after(a) {\n  return a;\n}\n"),
    "cases/function_type_alias.ts": "export type Parse = <T>(\n  a: T,\n) => T;\n",
    "cases/overloads.ts": ("export function pick(a: string): string;\n"
                           "export function pick(a: any): any {\n  return a;\n}\n"),
    "cases/pattern_defaults.ts": ("export function Separator({\n  orientation = \"vertical\",\n"
                                  "}: P) {\n  return orientation;\n}\n"),
}
MODE_OF = {"ccn_std": "complexity-classic", "cognitive": "cognitive", "nesting": "max-depth"}


@pytest.fixture(scope="module")
def cases(measure_set, tmp_path_factory, oracle):
    for name in ("typescript", "eslint", "eslint-plugin-sonarjs"):
        oracle(name)
    probes = analysis_tables.probe_files()
    files = {**{path: probes[path] for path, _, _ in SHAPE_CASES.values() if path in probes},
             **CASES}
    return member_run(files, tmp_path_factory.mktemp("shape-cases"), measure_set(files))


def _oracle_value(run: Member, fns: list, column: str):
    """The oracle's value for the one function in `fns`: its end, or the
    transformed number of the column's ESLint or sonarjs mode."""
    (fn,) = fns
    if column == "end":
        return fn.end
    mode = MODE_OF[column]
    return analysis_js.expected(fn, analysis_js.ORACLES[mode], run.numbers[mode])


def _values(run: Member, path: str, line: int, column: str) -> tuple:
    """(crapkit's value, the oracle's) on the case's line."""
    rows = analysis_js.rows_on(run.measured.in_file(path), line)
    fns = analysis_js.fns_on(analysis_js.in_file(run.compiled, path), line)
    if column == "rows":
        return len(rows), len(fns)
    (row,) = rows
    return row[column], _oracle_value(run, fns, column)


def _found(run: Member, shape: str, path: str, line: int) -> bool:
    fns = analysis_js.fns_on(analysis_js.in_file(run.compiled, path), line)
    marked = any(name == shape and at == line for name, at, _ in run.marks.get(path, []))
    return marked or any(shape in fn.shapes for fn in fns)


@pytest.mark.parametrize("shape", [
    pytest.param(shape, marks=analysis_tables.marks(SHAPES[shape][0])) for shape in sorted(SHAPES)])
def test_each_shape_pins_its_rulings_row(shape, cases):
    path, line, column = SHAPE_CASES[shape]

    assert _found(cases, shape, path, line), f"{shape} is not found on {path}:{line}"
    crapkit, oracle_value = _values(cases, path, line, column)
    rulings.pin_ruling(SHAPES[shape][0], crapkit=crapkit, oracle=oracle_value)


def test_every_shape_has_a_case_and_a_rulings_row():
    rows = rulings.load()

    assert sorted(SHAPE_CASES) == sorted(SHAPES)
    assert sorted({ruling for ruling, _ in SHAPES.values()} - set(rows)) == []


def test_eslint_pattern_defaults_transform_is_pinned(cases):
    """AO-ESLINT-PATTERN-DEFAULTS: ESLint's complexity counts the default value
    inside a destructured parameter (an AssignmentPattern); the transform takes
    it off, which gives crapkit's count: 1, no decision."""
    path = "cases/pattern_defaults.ts"
    (fn,) = analysis_js.fns_on(analysis_js.in_file(cases.compiled, path), 1)
    (row,) = analysis_js.rows_on(cases.measured.in_file(path), 1)
    raw = cases.numbers["complexity-classic"][fn]

    assert (fn.defaults, fn.pattern_defaults) == (0, 1)
    assert analysis_js.expected(fn, analysis_js.ORACLES["complexity-classic"],
                                cases.numbers["complexity-classic"]) == row["ccn_std"]
    rulings.pin_ruling("AO-ESLINT-PATTERN-DEFAULTS", crapkit=row["ccn_std"], oracle=raw)


INLINE = ("// eslint-disable-next-line complexity\nfunction f(a) {\n  if (a) {\n    return 1;\n"
          "  }\n  return 0;\n}\n/* eslint max-depth: [\"error\", 9] */\n"
          "/* eslint-disable sonarjs/cognitive-complexity */\n")


def test_a_file_s_own_eslint_comments_hide_no_number(tmp_path, oracle):
    """Corpus files carry `eslint-disable` and `/* eslint rule: ... */` comments
    (ky's Ky.ts disables complexity on its constructor). The probe turns inline
    configuration off, so every function keeps its number: f is 1 + one if."""
    oracle("eslint-plugin-sonarjs")
    paths = node_oracles.write({"inline.js": INLINE}, tmp_path)
    found, fatal = node_oracles.lint(oracles.node_modules("push"), tmp_path, MODES, paths)

    assert fatal == {}
    assert {mode: [number for *_, number in found[mode]["inline.js"]] for mode in MODES} == {
        "complexity-classic": [2], "complexity-modified": [2], "max-depth": [1],
        "cognitive": [1]}
