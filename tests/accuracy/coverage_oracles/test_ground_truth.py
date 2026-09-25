"""Ground truth: each probe function's coverage, worked by hand from the arms its driver takes.

probes/<lang>/ground_truth.tsv is written from each producer's documented branch
model and the driver's calls, never from a producer's output. Three checks:

1. Each committed recording states exactly those arms and statements, read with
   counts_table (json.load, no crapkit). A producer that moved, or a table that
   is wrong, fails here before it can reach a score.
2. On push, this interpreter's coverage.py 7.16.1 run over the Python probe
   states them too, and equals the committed 7.16.1 recording.
3. crapkit's scored cov and flag for every function equal the README's term
   over those counts, after the README floor for a Python def-line layout.
   Where crapkit and the ground truth part, a rulings row says why.

Every producer's recording is replayed on push; regenerate.py reruns the
producers in the nightly tier.
"""
import dataclasses
from fractions import Fraction
import json

import pytest

from accuracy.coverage_oracles import counts_table, ground_table, probe_repo
from accuracy.kit import rulings

LIVE = "coveragepy-live"
RECORDED = sorted(name for name in ground_table.FAMILIES if name != LIVE)
PROBES = {**{name: probes for name, (_, probes) in probe_repo.PRODUCERS.items()},
          LIVE: probe_repo.PYTHON}
# (producer family or name, function, scenario) -> the rulings row that explains
# where crapkit's score and the ground truth part.
KNOWN = {
    ("istanbul", "arrow", "idle"): "CO-B1", ("v8", "arrow", "idle"): "CO-B1",
    ("istanbul", "h", "idle"): "CO-B1", ("v8", "h", "idle"): "CO-B1",
    ("coveragepy-7.13.0", "outer.inner", "call"): "CO-B2",
    ("coveragepy-7.10.6", "outer.inner", "call"): "CO-B2",
    ("coveragepy", "excluded", "call"): "CO-B3", ("coveragepy", "excluded", "idle"): "CO-B3",
    ("istanbul", "ignoredIstanbul", "call"): "CO-B4", ("istanbul", "ignoredIstanbul", "idle"): "CO-B4",
    ("v8", "ignoredIstanbul", "call"): "CO-B4", ("v8", "ignoredIstanbul", "idle"): "CO-B4",
    ("v8", "ignoredV8", "call"): "CO-B4", ("v8", "ignoredV8", "idle"): "CO-B4",
    ("v8", "defaultParam", "call"): "CO3",
}
# In a TypeScript file the dropped function's span runs on to the next function's
# start line (CO-B5), so the join hands it that function's coverage instead.
SPAN_OVERLAP = {("istanbul", "ignoredIstanbul"), ("v8", "ignoredV8")}
TYPED = ("ts", "tsx", "vue")


def _ruling(producer: str, row: ground_table.Truth) -> str | None:
    family = ground_table.FAMILIES[producer]
    if (family, row.function) in SPAN_OVERLAP and row.path.split("/")[0] in TYPED:
        return "CO-B5"
    return (KNOWN.get((producer, row.function, row.scenario))
            or KNOWN.get((family, row.function, row.scenario)))


# --- 1 and 2: the recordings state the hand-worked arms ------------------------------------------

def _producer_rows(producer: str, artifact: dict) -> dict:
    """{(path, function or start): counts} with the producer's own attribution."""
    if ground_table.FAMILIES[producer] == "coveragepy":
        return {(row.path, row.name): row for row in counts_table.coveragepy_rows(artifact)}
    return {(row.path, row.start): row for row in counts_table.istanbul_rows(artifact, "position")}


def _key(producer: str, row: ground_table.Truth):
    python = ground_table.FAMILIES[producer] == "coveragepy"
    return (row.path, row.function if python else row.start)


def _stated(counts) -> tuple:
    return (set(counts.arms), set(counts.arms_taken), set(map(str, counts.stmts)),
            set(map(str, counts.stmts_run)))


def _hand(row: ground_table.Truth) -> tuple:
    return set(row.arms), set(row.arms_taken), set(row.stmts), set(row.stmts_run)


def _mismatch(producer: str, row: ground_table.Truth, found: dict) -> str | None:
    counts = found.get(_key(producer, row))
    if row.arms == ("absent",):
        return None if counts is None else f"{row.path}:{row.function} is in the artifact"
    if counts is None:
        return f"{row.path}:{row.function} ({row.scenario}) is missing from the artifact"
    if row.arms == ("excluded",):
        return None if not (counts.arms or counts.stmts) else f"{row.function} kept lines"
    hand, stated = _hand(row), _stated(counts)
    return None if hand == stated else f"{row.path}:{row.function} ({row.scenario}) {hand} != {stated}"


def _mismatches(producer: str, scenario: str, artifact: dict) -> list[str]:
    found = _producer_rows(producer, artifact)
    rows = [row for row in ground_table.rows_for(producer, scenario, PROBES[producer])
            if row.modelled and _ruling(producer, row) != "CO3"]
    return [line for line in (_mismatch(producer, row, found) for row in rows) if line]


def _recorded(producer: str, scenario: str) -> dict:
    path = probe_repo.RECORDED / producer / f"{scenario}.json"
    return json.loads(path.read_bytes())


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
@pytest.mark.parametrize("producer", RECORDED)
def test_each_recording_states_the_hand_worked_arms(producer, scenario):
    assert _mismatches(producer, scenario, _recorded(producer, scenario)) == []


@pytest.mark.process
@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_coveragepy_run_here_states_the_hand_worked_arms(probe_run, scenario):
    live = json.loads(probe_run.slot(LIVE, scenario).artifact.read_bytes())

    assert _mismatches(LIVE, scenario, live) == []
    assert (_by_name(counts_table.coveragepy_rows(live))
            == _by_name(counts_table.coveragepy_rows(_recorded("coveragepy-7.16.1", scenario))))


def _by_name(rows: list) -> list:
    return sorted(rows, key=lambda row: row.name)


def test_the_def_line_layouts_read_the_same_called_or_not():
    """README.md#remedy-what-to-do-about-it: coverage.py cannot show a call to a
    one-line def or a body on its signature's last line, which is why crapkit
    floors them. The recordings bear it out: called and idle state one thing."""
    for producer in [name for name in RECORDED if ground_table.FAMILIES[name] == "coveragepy"]:
        rows = {scenario: {row.name: row for row in counts_table.coveragepy_rows(
            _recorded(producer, scenario))} for scenario in probe_repo.SCENARIOS}
        assert (rows["call"]["body_on_signature"].stmts_run
                == rows["idle"]["body_on_signature"].stmts_run == ())
        assert rows["call"]["one_line"].stmts_run == rows["idle"]["one_line"].stmts_run == (85,)


# --- 3: crapkit's score agrees with the ground truth ----------------------------------------------

def _cases():
    for producer in [*RECORDED, LIVE]:
        for scenario in probe_repo.SCENARIOS:
            for row in ground_table.rows_for(producer, scenario, PROBES[producer]):
                ruling = _ruling(producer, row)
                marks = rulings.applies(ruling) if ruling else ()
                marks = marks if isinstance(marks, pytest.MarkDecorator) else ()
                yield pytest.param(producer, row, ruling, marks=marks,
                                   id=f"{producer}-{scenario}-{row.path}-{row.function}")


def _relation(scored) -> str:
    """How a row's CRAP relates to its ccn, in the words the rulings rows use."""
    if scored.crap == scored.ccn:
        return "crap = ccn"
    if scored.crap == scored.ccn * scored.ccn + scored.ccn:
        return "crap = ccn^2 + ccn"
    return f"crap = {scored.crap}"


def _verdict(row: ground_table.Truth, scored) -> str:
    """A function absent from the artifact that still reads measured took
    another function's number."""
    if row.arms == ("absent",) and scored.flag == "measured":
        return "measured from another function"
    return _relation(scored)


def _measured(row: ground_table.Truth, scored) -> tuple:
    """(crapkit, oracle). A function the producer was told not to measure has no
    coverage number, and README.md#flags-why-a-coverage-number-is-missing scores
    such a function at crap = ccn (the cc-only row), never as uncovered."""
    if row.unmeasured:
        return _verdict(row, scored), "crap = ccn"
    expected, flag = ground_table.crapkit_expected(row)
    return (scored.cov, scored.flag), (float(expected), flag)


@pytest.mark.process
@pytest.mark.parametrize("producer, row, ruling", list(_cases()))
def test_crapkit_scores_what_the_ground_truth_says(probe_run, producer, row, ruling):
    scored = probe_run.scored(producer, row.scenario).get((row.path, row.start))
    assert scored is not None, f"crapkit scored no row at {row.path}:{row.start}"
    crapkit, oracle = _measured(row, scored)
    if ruling:
        rulings.pin_ruling(ruling, crapkit=_first(crapkit), oracle=_first(oracle))
    else:
        assert crapkit == oracle


def _first(value):
    return value[0] if isinstance(value, tuple) else value


def test_a_function_with_no_arms_and_no_statements_reads_called_or_not():
    """README.md: with no statements, invoked-or-not."""
    row = ground_table.Truth("a.js", "f", 1, 1, "one-line", "call", "all", (), (), (), (), "x")

    assert ground_table.expected(row) == Fraction(1)
    assert ground_table.expected(dataclasses.replace(row, scenario="idle")) == 0
