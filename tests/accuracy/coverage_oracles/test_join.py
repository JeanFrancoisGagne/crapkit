"""The coverage join: each scored function takes its own artifact function's counts, and only those.

The oracle places every function counts_table reads off an artifact (a
coverage.py region, an istanbul fnMap entry) on a scored row by position, with
json.load and no crapkit:

- a coverage.py region lands on the row of the same bare name that starts on
  its start_line, else on the same-named row whose span holds its first line; a
  report before coverage.py 7.13.1 has no start_line
  (https://coverage.readthedocs.io/en/7.16.1/changes.html#version-7-13-1), and
  crapkit refuses it, so no such recording reaches the join (ruling CO-B2);
- an istanbul function lands on the row that starts on its decl.start.line
  (docs/lanes.md#what-the-istanbul-parser-reads).

Then, over the probe run and the kit's measured corpus:

1. every artifact function lands on a row, and every measured row is landed on
   by exactly one artifact function, whose README ratio is the row's cov;
2. a row nothing lands on is untested, and a floored row (README remedy:
   a Python def-line layout, a shared span) reads 0 whatever landed on it.

Metamorphic (R03): reversing the lane order, running the lanes in parallel,
and adding a lane that measured the same files again leave every score alone;
two lanes that measured one file differently give one score in either order.
Self-diff, not independent: the start-line index picks what the scan picks.
"""
from dataclasses import dataclass
from fractions import Fraction
import json
import os

from hypothesis import example, given, strategies as st
import pytest

from accuracy.coverage_oracles import counts_table, mini_repo, probe_repo, under_test
from accuracy.kit import corpus_run, rulings, surfaces
from accuracy.kit.settings import pure

SCORE = under_test.crapkit("score")
# Recordings whose join the rulings rows already explain: raw v8-to-istanbul is
# not the istanbul model (D7, CO4); a TypeScript function whose span runs into
# the next one's takes that one's counts (CO-B5).
RAW_V8 = {"c8-12.0.0", "jest-v8-30.5.2"}
DEFECTS = {(producer, scenario): "CO-B5" for scenario in probe_repo.SCENARIOS
           for producer in ("vitest-v8-5.0.1", "vitest-istanbul-5.0.1", "jest-babel-30.5.2")}


@dataclass(frozen=True)
class Scored:
    path: str
    name: str
    start: int
    end: int
    cov: float
    flag: str


def _bare(name: str) -> str:
    """`Box.method( self , value )` or `Box.method` -> `method`."""
    return name.split("(")[0].strip().split(".")[-1].split("::")[-1]


def _holds(row: Scored, line: int) -> bool:
    return row.start <= line <= row.end


def _innermost(named: list[Scored], line: int) -> list[Scored]:
    holders = [row for row in named if _holds(row, line)]
    return [min(holders, key=lambda row: row.end - row.start)] if holders else []


def _python_place(fn, rows: list[Scored]) -> list[Scored]:
    """The same-named row that starts on the region's start, else the
    innermost same-named row that holds it."""
    named = _named(rows, fn.name)
    return [row for row in named if row.start == fn.start] or _innermost(named, fn.start)


def _named(rows: list[Scored], name: str) -> list[Scored]:
    return [row for row in rows if _bare(row.name) == _bare(name)]


def _place(fn, rows: list[Scored]) -> list[Scored]:
    """The rows one artifact function lands on."""
    if fn.path.endswith(".py"):
        return _python_place(fn, rows)
    return [row for row in rows if row.start == fn.start]


def _landings(functions: list, by_path: dict) -> tuple[dict, list[str]]:
    """{row: the functions landing on it}, and a line per function landing nowhere."""
    landed: dict[Scored, list] = {}
    nowhere = []
    for fn in functions:
        places = _place(fn, by_path.get(fn.path, []))
        nowhere += [] if places else [f"{fn.path}:{fn.start} {fn.name} lands on no row"]
        for row in places:
            landed.setdefault(row, []).append(fn)
    return landed, nowhere


def join_findings(functions: list, rows: list[Scored]) -> list[str]:
    """Every way the scored rows break the position join, as readable lines."""
    by_path: dict[str, list[Scored]] = {}
    for row in rows:
        by_path.setdefault(row.path, []).append(row)
    landed, nowhere = _landings(functions, by_path)
    return nowhere + [line for row in rows for line in _row_findings(row, landed.get(row, []))]


def _floored(row: Scored, rows_on_span: int) -> bool:
    """README.md#remedy-what-to-do-about-it: a Python def-line layout or a
    shared span reads 0, untested."""
    return rows_on_span > 1 or (row.path.endswith(".py") and row.start == row.end)


def _row_findings(row: Scored, landed: list) -> list[str]:
    if row.flag != "measured":
        return []
    if len(landed) != 1:
        return [f"{row.path}:{row.start} {row.name} is measured but {len(landed)} functions land on it"]
    want = float(counts_table.ratio(landed[0]))
    return [] if row.cov == want else [f"{row.path}:{row.start} {row.name} cov {row.cov} != {want}"]


def _floor_findings(rows: list[Scored]) -> list[str]:
    spans: dict[tuple, int] = {}
    for row in rows:
        spans[(row.path, row.start, row.end)] = spans.get((row.path, row.start, row.end), 0) + 1
    return [f"{row.path}:{row.start} {row.name} is floored but reads {row.cov} {row.flag}"
            for row in rows if _floored(row, spans[(row.path, row.start, row.end)])
            and (row.cov, row.flag) != (0.0, "untested")]


# --- the probe run --------------------------------------------------------------------------------

def _artifact_functions(slot) -> list:
    artifact = json.loads(slot.artifact.read_bytes())
    if slot.parser == "coveragepy":
        return counts_table.coveragepy_rows(artifact)
    return counts_table.istanbul_rows(artifact, "line")


def _slot_rows(probe_run, slot) -> list[Scored]:
    return [Scored(path, row.name, start, row.end, row.cov, row.flag)
            for (path, start), row in probe_run.scored(slot.producer, slot.scenario).items()]


def _findings(probe_run, slot) -> list[str]:
    rows = _slot_rows(probe_run, slot)
    functions = [fn for fn in _artifact_functions(slot) if fn.start > 0]
    return join_findings(functions, rows) + _floor_findings(rows)


def _recordings() -> list[tuple[str, str]]:
    """(producer, scenario) for every committed recording and the live run."""
    committed = [(slot.producer, slot.scenario) for slot in probe_repo.slots()]
    live = [("coveragepy-live", scenario) for scenario in probe_repo.SCENARIOS]
    return [pair for pair in committed + live if pair[0] not in RAW_V8]


def _cases():
    for producer, scenario in _recordings():
        ruling = DEFECTS.get((producer, scenario))
        marks = rulings.applies(ruling) if ruling else ()
        yield pytest.param(producer, scenario, ruling,
                           marks=marks if isinstance(marks, pytest.MarkDecorator) else (),
                           id=f"{producer}-{scenario}")


@pytest.mark.process
@pytest.mark.parametrize("producer, scenario, ruling", list(_cases()))
def test_every_artifact_function_joins_its_own_row(probe_run, producer, scenario, ruling):
    """A region with no lines at all (coverage.py's pragma-excluded function,
    ruling CO-B3's subject) has no position to land on and is left out."""
    findings = _findings(probe_run, probe_run.slot(producer, scenario))
    if ruling and findings:
        raise rulings.RulingDefect(f"{ruling}: {findings}")

    assert findings == []


# --- the kit's measured corpus -----------------------------------------------------------------

@pytest.fixture(scope="module")
def measured_corpus(request, tmp_path_factory):
    """The session's small corpus once it lands; until then the kit's seed
    corpus, measured under the shared session root so other tests reuse it."""
    if corpus_run.SMALL.is_dir():
        return request.getfixturevalue("small_corpus")
    base = tmp_path_factory.getbasetemp()
    shared = base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base
    return corpus_run.measure(corpus_run.SEED, shared)


def _corpus_rows(run) -> list[Scored]:
    rows = surfaces.read_tsv(run.output("scored.tsv"))[1]
    return [Scored(row["path"], row["long_name"], int(row["start"]), int(row["end"]),
                   float(row["cov"]), row["flag"]) for row in rows]


# Small-corpus files that carry a reader defect on purpose, each a past bug's
# shape: a .vue function's row starts on its <script> block's line count, not the
# file's (AO-VUE-LINES, CG1), `function f<T>` gets no row (D1a), and a regular
# expression holding a backtick hides the functions after it (D1c). Their artifact
# functions land on no row because the rows are wrong, not the join. While
# CO-READER-JOIN is a defect the corpus join sets these files aside and pins them
# in a test of their own; once the row reads fixed they rejoin the whole corpus.
READER_JOIN = "CO-READER-JOIN"
READER_DEFECT_FILES = ("src/web/Counter.vue", "src/web/generic.ts", "src/web/templates.ts")


def _in_test_directory(path: str) -> bool:
    """docs/configuration.md#exclude: a path with a component `test`, `tests` or
    `__tests__`, in any case, leaves the corpus before anything is scored."""
    return any(part.lower() in ("test", "tests", "__tests__") for part in path.split("/"))


def _corpus_functions(run) -> list:
    """Every artifact function in a file the corpus scores: a recorded js lane also
    measured src/web/__tests__, which no row can stand for."""
    return [fn for rows in counts_table.table(run.root).values() for fn in rows
            if not _in_test_directory(fn.path)]


def _kept(items: list, aside: bool) -> list:
    """Every item, or every item outside the reader-defect files while `aside` holds."""
    return [item for item in items if not (aside and item.path in READER_DEFECT_FILES)]


def _set_aside(items: list) -> list:
    return [item for item in items if item.path in READER_DEFECT_FILES]


@pytest.mark.process
def test_counts_table_joins_every_measured_corpus_row(measured_corpus):
    aside = rulings.load()[READER_JOIN].ruling == "defect"
    functions = _kept(_corpus_functions(measured_corpus), aside)
    rows = _kept(_corpus_rows(measured_corpus), aside)

    assert functions and any(row.flag == "measured" for row in rows)
    assert join_findings(functions, rows) + _floor_findings(rows) == []


@rulings.applies(READER_JOIN)
@pytest.mark.process
def test_reader_defect_files_join_their_artifact_functions(measured_corpus):
    """The set-aside files: each function the recorded istanbul fnMap holds lands on
    its own row once the TypeScript and Vue readers are fixed."""
    functions = _set_aside(_corpus_functions(measured_corpus))
    rows = _set_aside(_corpus_rows(measured_corpus))
    findings = join_findings(functions, rows) + _floor_findings(rows)

    assert functions and rows
    rulings.pin_ruling(READER_JOIN, crapkit=f"{len(findings)} findings", oracle="0 findings")


# --- metamorphic: lanes in any order, in parallel, twice -------------------------------------------

LANGUAGE = {"py": ("python", "coveragepy", "coveragepy-7.16.1", "py/shapes.py"),
            "js": ("javascript", "istanbul", "vitest-istanbul-5.0.1", "js/shapes.js")}


def _recording(language: str, scenario: str) -> bytes:
    _, parser, producer, probe = LANGUAGE[language]
    artifact = json.loads((probe_repo.RECORDED / producer / f"{scenario}.json").read_bytes())
    if parser == "istanbul":
        artifact = {probe: artifact[probe]}
    return json.dumps(artifact).encode()


def _lane_config(lanes: list[tuple[str, str]], parallel: int) -> str:
    scopes = [mini_repo.scope(name, [name], [spec[0]]) for name, spec in LANGUAGE.items()]
    tables = [mini_repo.lane(f"{language}-{scenario}", LANGUAGE[language][1], [language])
              for language, scenario in lanes]
    knob = f"max_parallel_lanes = {parallel}\n"
    return mini_repo.config(scopes, tables).replace("[exclude]", knob + "[exclude]", 1)


def _lane_scores(top, lanes: list[tuple[str, str]], parallel: int = 1) -> dict:
    """{(path, start, name): (cov, flag, crap)} for the probes scored by these
    lanes, each (language, scenario) copying that recording."""
    tree = {"crapkit.toml": _lane_config(lanes, parallel)}
    tree.update({spec[3]: (probe_repo.PROBES / spec[3]).read_bytes() for spec in LANGUAGE.values()})
    tree.update({f"recorded/{language}-{scenario}.json": _recording(language, scenario)
                 for language, scenario in lanes})
    driver = mini_repo.build(top, tree)
    result = driver.run("coverage", "--export", "scored.tsv")
    assert result.code == 0, result.stderr
    rows = surfaces.read_tsv((driver.root / "scored.tsv").read_text(encoding="utf-8"))[1]
    return {(row["path"], row["start"], row["long_name"]): (row["cov"], row["flag"], row["crap"])
            for row in rows}


@pytest.mark.process
def test_lane_order_swap_keeps_scores(tmp_path):
    """R03: two lanes serially, against the same two in the other order, run in
    parallel, with a lane before and after each that measured the same file
    with fewer calls. The union of what ran is what the two lanes measured."""
    base = _lane_scores(tmp_path / "a", [("py", "call"), ("js", "call")])
    shuffled = [("js", "idle"), ("js", "call"), ("py", "call"), ("py", "idle")]

    assert _lane_scores(tmp_path / "b", shuffled, parallel=2) == base


# --- self-diff: the start-line index against the scan ---------------------------------------------

FN = under_test.crapkit("score").FnCoverage


@st.composite
def candidates(draw):
    """Up to six functions on a short file, so starts collide often."""
    starts = draw(st.lists(st.integers(1, 8), min_size=0, max_size=6))
    return [FN(f"f{index}", start, start + draw(st.integers(0, 4)), True, 2, draw(st.integers(0, 2)))
            for index, start in enumerate(starts)]


@dataclass(frozen=True)
class Row:
    start: int
    end: int


@given(candidates(), st.integers(1, 9), st.integers(0, 4))
@example([FN("outer", 1, 7, True, 2, 1), FN("inner", 5, 6, True, 2, 2)], 5, 5)
@example([FN("a", 1, 1, True, 2, 0), FN("b", 2, 2, True, 2, 1)], 1, 1)
@pure
def test_the_start_index_picks_what_the_scan_picks(found, start, length):
    """Self-diff: score._best_exact over the start line's bucket, falling back
    to the scan, agrees with score._best_match over every candidate. The
    examples: a nested function starting on the row's line beats a longer
    overlap from its encloser; an exact start beats a better-covered neighbour."""
    row = Row(start, start + length)
    bucket = [fn for fn in found if fn.start == start]
    indexed = SCORE._best_exact(row, bucket) or SCORE._best_match(row, found)

    assert indexed == SCORE._best_match(row, found)


def test_ratio_is_the_readme_term():
    """counts_table.ratio, the join oracle's number, on the README's three cases."""
    counts = counts_table.Counts("a.py", "f", 1, 3, ("a", "b"), ("a",), (2, 3), (2,), True)

    assert counts_table.ratio(counts) == Fraction(1, 2)
    assert counts_table.ratio(counts.__class__(*("a.py", "f", 1, 3, (), (), (2, 3), (), True))) == 0
    assert counts_table.ratio(counts.__class__(*("a.py", "f", 1, 3, (), (), (), (), True))) == 1
