"""Dark lines: the lines crapkit names as never run, against the lines the probe drivers never ran.

The expected lines come from ground_truth.tsv, never from a producer: a line in
a function's `stmts` but not its `stmts_run` holds a statement the driver never
ran, and every module-level statement runs at import. crapkit marks a line dark
when a statement that starts on it has zero hits (ruling D6), so a file's hand
set is the union over its functions of stmts minus stmts_run.

1. For every recording of a documented producer, crapkit's dead lines per file
   equal the hand set, both from the walk a lane scores with and from the
   reread verify falls back to.
2. load_uncovered over the scored probe repo, which next-item, brief and
   explain call, gives each function the share of the hand set inside its span.
3. istanbul-reports' lcov (DA lines) counts a line as run when any statement
   on it ran. It parts from crapkit only on a line that holds a run statement
   and an unrun one: ruling D6.
4. The fold across lanes keeps a line dark only when no lane ran it, in any
   lane order (a Hypothesis model of the intersection).
5. Parse consistency, not independent: coverage.py's own Cobertura and lcov
   reports of the same data name the dark lines its JSON names.
"""
import json
import xml.etree.ElementTree as ElementTree

from hypothesis import given, strategies as st
import pytest

from accuracy.coverage_oracles import counts_table, ground_table, probe_repo, under_test
from accuracy.kit import rulings
from accuracy.kit.settings import pure

RECORDED = probe_repo.RECORDED
LIVE = "coveragepy-live"
PRODUCERS = sorted(name for name in ground_table.FAMILIES if name != LIVE)
PROBES = {**{name: probes for name, (_, probes) in probe_repo.PRODUCERS.items()},
          LIVE: probe_repo.PYTHON}
UNCOVERED, CONFIG, ISTANBUL, COVERAGE_PY = (under_test.crapkit(name) for name in (
    "uncovered", "config", "coverage_istanbul", "coverage_py"))


def hand_dark(producer: str, scenario: str) -> dict[str, set[int]]:
    return ground_table.dark(producer, scenario, PROBES[producer])


def _python(producer: str) -> bool:
    return ground_table.FAMILIES[producer] == "coveragepy"


def _walked(producer: str, scenario: str) -> dict:
    artifact = RECORDED / producer / f"{scenario}.json"
    if _python(producer):
        return COVERAGE_PY.parse_coveragepy_both_file(artifact, path_prefix="")[1]
    return ISTANBUL.parse_istanbul_both_file(artifact, repo_root="")[1]


def _reread(producer: str, scenario: str) -> dict:
    artifact = RECORDED / producer / f"{scenario}.json"
    if _python(producer):
        return COVERAGE_PY.parse_coveragepy_missing_file(artifact, path_prefix="")
    return ISTANBUL.parse_istanbul_missing_file(artifact, repo_root="")


def _nonempty(found: dict) -> dict:
    return {path: set(lines) for path, lines in found.items() if lines}


WALKED = [name for name in PRODUCERS if name not in probe_repo.REFUSED]


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
@pytest.mark.parametrize("producer", WALKED)
def test_dead_lines_are_the_statements_the_driver_never_ran(producer, scenario):
    expected = _nonempty(hand_dark(producer, scenario))

    assert _nonempty(_walked(producer, scenario)) == expected
    assert _nonempty(_reread(producer, scenario)) == expected


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
@pytest.mark.parametrize("producer", probe_repo.REFUSED)
def test_a_report_without_start_line_is_refused_and_its_dead_lines_still_read(producer,
                                                                              scenario):
    """The scoring walk refuses a region with no start_line (ruling CO-B2); the
    missing lines, which need no region, read as the driver left them."""
    with pytest.raises(Exception, match="no start_line; coverage.py writes it"):
        _walked(producer, scenario)

    assert _nonempty(_reread(producer, scenario)) == _nonempty(hand_dark(producer, scenario))


# --- 2. the spans next-item and explain read ----------------------------------------------------

def _measured_rows(slot) -> list:
    rows = ground_table.rows_for(slot.producer, slot.scenario, slot.probes)
    return [row for row in rows if row.modelled and not row.unmeasured]


def _in_span(dark: dict, row) -> list[int]:
    return sorted(line for line in dark.get(row.path, ()) if row.start <= line <= row.end)


def _load_uncovered(root):
    text = (root / "crapkit.toml").read_text(encoding="utf-8")
    return UNCOVERED.load_uncovered(root, CONFIG.load_config_text(text, root=root))


def _wrong_spans(uncovered, slot) -> list[tuple]:
    """(function, crapkit's lines, the hand lines) wherever they differ."""
    dark = hand_dark(slot.producer, slot.scenario)
    found = ((row, uncovered.in_span(f"{slot.prefix}/{row.path}", row.start, row.end))
             for row in _measured_rows(slot))
    return [(slot.producer, slot.scenario, row.function, got, _in_span(dark, row))
            for row, got in found if got != _in_span(dark, row)]


@pytest.mark.process
def test_every_function_gets_the_dark_lines_inside_its_span(probe_run):
    uncovered = _load_uncovered(probe_run.root)
    documented = [slot for slot in probe_run.slots if slot.producer in ground_table.FAMILIES]

    assert uncovered.note == ""
    assert [wrong for slot in documented for wrong in _wrong_spans(uncovered, slot)] == []


# --- 3. D6: the lcov line rule ------------------------------------------------------------------

lcov_lines = counts_table.lcov_lines
mixed_lines = counts_table.mixed_lines


def _d6_split(scenario: str) -> tuple[dict, dict, dict]:
    """(crapkit dead, lcov zero-count lines, mixed lines) per file."""
    folder = RECORDED / "vitest-istanbul-5.0.1"
    lcov = lcov_lines((folder / f"{scenario}.lcov").read_text(encoding="utf-8"))
    artifact = json.loads((folder / f"{scenario}.json").read_bytes())
    zero = {path: {line for line, count in lines.items() if count == 0}
            for path, lines in lcov.items()}
    mixed = {path: mixed_lines(data) for path, data in artifact.items()}
    return _walked("vitest-istanbul-5.0.1", scenario), zero, mixed


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_crapkit_and_lcov_part_only_on_mixed_lines(scenario):
    dead, zero, mixed = _d6_split(scenario)

    assert set(dead) == set(zero) == set(mixed)
    assert {path: dead[path] - zero[path] for path in dead} == mixed
    assert {path: zero[path] - dead[path] for path in dead} == {path: set() for path in dead}


@rulings.applies("D6")
def test_d6_a_line_with_a_run_and_an_unrun_statement_is_dark():
    """js/shapes.js line 60 in the idle scenario: `const arrow = (value) =>
    value * 2;` holds the declaration, which ran at import, and the arrow's
    body, which never ran."""
    dead, zero, mixed = _d6_split("idle")
    assert 60 in mixed["js/shapes.js"]
    count = lcov_lines((RECORDED / "vitest-istanbul-5.0.1" / "idle.lcov").read_text(
        encoding="utf-8"))["js/shapes.js"][60]

    rulings.pin_ruling("D6", crapkit="dark" if 60 in dead["js/shapes.js"] else "run",
                       oracle=f"DA:60,{count}")


# --- 4. the fold across lanes --------------------------------------------------------------------

PATHS = st.sampled_from(["a.py", "b.py", "src/c.ts"])
LANE = st.dictionaries(PATHS, st.sets(st.integers(1, 12), max_size=6), max_size=3)


def folded_model(lanes: list[dict]) -> dict:
    """docs/agent-json.md#uncovered_lines-null-is-not-: a line is dark when no
    lane that measured its file ran it; a lane silent on a file says nothing."""
    paths = {path for lane in lanes for path in lane}
    return {path: set.intersection(*_measuring(lanes, path)) for path in paths}


def _measuring(lanes: list[dict], path: str) -> list[set]:
    return [set(lane[path]) for lane in lanes if path in lane]


@given(st.lists(LANE, min_size=1, max_size=4), st.randoms(use_true_random=False))
@pure
def test_the_lane_fold_is_an_intersection_in_any_order(lanes, random):
    order = list(lanes)
    random.shuffle(order)
    folded: dict = {}
    for lane in order:
        UNCOVERED._fold_into(folded, lane)

    assert folded == folded_model(lanes)


# --- 5. parse consistency: coverage.py's other reports ------------------------------------------

REPORTS = RECORDED / "coveragepy-reports-7.16.1"


def _cobertura_zero(scenario: str) -> set[int]:
    root = ElementTree.parse(REPORTS / f"{scenario}.xml").getroot()
    return {int(line.get("number")) for line in root.iter("line") if line.get("hits") == "0"}


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_coverage_py_reports_name_the_same_dark_lines(scenario):
    """Parse consistency: coverage.py wrote all three reports."""
    dead = COVERAGE_PY.parse_coveragepy_missing_file(REPORTS / f"{scenario}.json",
                                                      path_prefix="")["py/shapes.py"]
    lcov = lcov_lines((REPORTS / f"{scenario}.lcov").read_text(encoding="utf-8"))["py/shapes.py"]

    assert dead == _cobertura_zero(scenario) == {line for line, n in lcov.items() if n == 0}
