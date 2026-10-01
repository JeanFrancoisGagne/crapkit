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
   Where crapkit and the ground truth part, a rulings row says why. A
   coverage.py recording with no start_line is refused instead (ruling CO-B2).

Every producer's recording is replayed on push. In the nightly tier
regenerate.py reruns each producer over the probes and the fresh artifact must
equal the committed one three ways (counts_table, crapkit's parse or its
refusal, the canonical form), so the ground truth holds for the producer as installed.
"""
import dataclasses
from fractions import Fraction
import json
from pathlib import Path
import re
import sys

import pytest

import hang_guard
from accuracy.coverage_oracles import counts_table, ground_table, mini_repo, probe_repo
from accuracy.kit import rulings, runlog, tiers

LIVE = "coveragepy-live"
RECORDED = sorted(name for name in ground_table.FAMILIES if name != LIVE)
PROBES = {**{name: probes for name, (_, probes) in probe_repo.PRODUCERS.items()},
          LIVE: probe_repo.PYTHON}
# (producer family or name, function, scenario) -> the rulings row that explains
# where crapkit's score and the ground truth part.
KNOWN = {
    ("istanbul", "arrow", "idle"): "CO-B1", ("v8", "arrow", "idle"): "CO-B1",
    ("istanbul", "h", "idle"): "CO-B1", ("v8", "h", "idle"): "CO-B1",
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


def _absent(row: ground_table.Truth, counts) -> str | None:
    return None if counts is None else f"{row.path}:{row.function} is in the artifact"


def _excluded(row: ground_table.Truth, counts) -> str | None:
    kept = counts is not None and bool(counts.arms or counts.stmts)
    return f"{row.function} kept lines" if kept else None


def _counted(row: ground_table.Truth, counts) -> str | None:
    if counts is None:
        return f"{row.path}:{row.function} ({row.scenario}) is missing from the artifact"
    hand, stated = _hand(row), _stated(counts)
    return None if hand == stated else f"{row.path}:{row.function} ({row.scenario}) {hand} != {stated}"


MISMATCH = {("absent",): _absent, ("excluded",): _excluded}


def _mismatch(producer: str, row: ground_table.Truth, found: dict) -> str | None:
    return MISMATCH.get(row.arms, _counted)(row, found.get(_key(producer, row)))


def _checked_rows(producer: str, scenario: str) -> list[ground_table.Truth]:
    """The modelled rows, less CO3's: the v8 provider counts a default
    parameter's arm as the function's own (ruling CO3)."""
    rows = ground_table.rows_for(producer, scenario, PROBES[producer])
    return [row for row in rows if row.modelled and _ruling(producer, row) != "CO3"]


def _mismatches(producer: str, scenario: str, artifact: dict) -> list[str]:
    found = _producer_rows(producer, artifact)
    lines = (_mismatch(producer, row, found) for row in _checked_rows(producer, scenario))
    return [line for line in lines if line]


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
    for producer in PYTHON_RECORDED:
        call, idle = (_named_rows(producer, scenario) for scenario in probe_repo.SCENARIOS)
        assert call["body_on_signature"].stmts_run == idle["body_on_signature"].stmts_run == ()
        assert call["one_line"].stmts_run == idle["one_line"].stmts_run == (85,)


PYTHON_RECORDED = [name for name in RECORDED if ground_table.FAMILIES[name] == "coveragepy"]


def _named_rows(producer: str, scenario: str) -> dict:
    return {row.name: row for row in counts_table.coveragepy_rows(_recorded(producer, scenario))}


# --- 3: crapkit's score agrees with the ground truth ----------------------------------------------

def _param(producer: str, row: ground_table.Truth):
    """One case, a strict xfail when its rulings row is an open defect."""
    ruling = _ruling(producer, row)
    marks = rulings.applies(ruling) if ruling else ()
    marks = marks if isinstance(marks, pytest.MarkDecorator) else ()
    return pytest.param(producer, row, ruling, marks=marks,
                        id=f"{producer}-{row.scenario}-{row.path}-{row.function}")


# The producers crapkit scores: a recording it refuses has no row to compare.
SCORED = [name for name in [*RECORDED, LIVE] if name not in probe_repo.REFUSED]


def _cases():
    for producer in SCORED:
        for scenario in probe_repo.SCENARIOS:
            for row in ground_table.rows_for(producer, scenario, PROBES[producer]):
                yield _param(producer, row)


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


# --- one language per repo: the shapes past fixes named -------------------------------------------

# (case, probe, recording, functions). A repo holding one probe file and one
# recording, scored by `crapkit coverage --export`, read by column name: the
# oldest CLI that fixed these shapes runs it too, so the retro replay reaches
# the join and the ratio themselves.
# R02: a nested function joins its own region, never its encloser's.
# R03: a callback's branch counts against the callback, not the function around it.
# R07: a function with no branch reads its statement ratio, 2 of 4 lines.
ONE_LANGUAGE = [
    ("py-nested", "py/shapes.py", "coveragepy-7.16.1", ("outer", "outer.inner")),
    ("py-branchless", "py/shapes.py", "coveragepy-7.16.1", ("branchless",)),
    ("js-callback", "js/shapes.js", "vitest-istanbul-5.0.1", ("withCallback", "withCallback.callback")),
    ("ts-callback", "ts/shapes.ts", "vitest-istanbul-5.0.1", ("withCallback", "withCallback.callback")),
]


def _one_language_run(tmp_path, probe: str, recording: str):
    """(driver, result) of `crapkit coverage --export scored.tsv` over a repo
    holding one probe and its recording."""
    suffix = probe.split("/")[0]
    parser = "coveragepy" if suffix == "py" else "istanbul"
    toml = mini_repo.config([mini_repo.scope("s", [suffix], [probe_repo.LANGUAGES["." + suffix]])],
                            [mini_repo.lane("cov", parser, ["s"])])
    artifact = json.loads((probe_repo.RECORDED / recording / "call.json").read_bytes())
    driver = mini_repo.build(tmp_path / "repo", {
        "crapkit.toml": toml, probe: (probe_repo.PROBES / probe).read_bytes(),
        "recorded/cov.json": json.dumps(_only(artifact, probe, parser)).encode()})
    return driver, driver.run("coverage", "--export", "scored.tsv")


def _one_language_rows(tmp_path, probe: str, recording: str) -> dict[int, probe_repo.Row]:
    driver, result = _one_language_run(tmp_path, probe, recording)
    assert result.code == 0, result.stderr
    text = (driver.root / "scored.tsv").read_bytes().decode("utf-8")
    return {row.start: row for row in probe_repo.read_scored(text)}


NO_START_LINE = re.compile(r"py/shapes\.py: [\w.]+: no start_line; coverage\.py writes it on "
                           r"every function from 7\.13\.1, so install coverage>=7\.13\.1 and "
                           r"rerun the lane")


@pytest.mark.process
@pytest.mark.parametrize("producer", probe_repo.REFUSED)
@rulings.applies("CO-B2")
def test_a_recording_without_start_line_is_refused_by_name(tmp_path, producer):
    """coverage.py writes each region's def line as start_line from 7.13.1. Read
    from its body alone, a nested def on its encloser's first body line took the
    encloser's region, so a never-called outer.inner scored 0.5. crapkit refuses
    such a report at exit 5, naming the file, its first function and the coverage
    to install; the ground truth for outer.inner stays 0.0."""
    _, result = _one_language_run(tmp_path, "py/shapes.py", producer)
    truth = {row.function: row for row in ground_table.rows_for(producer, "call",
                                                                ("py/shapes.py",))}

    assert NO_START_LINE.search(result.stderr), result.stderr
    rulings.pin_ruling("CO-B2", crapkit=f"exit {result.code}",
                       oracle=float(ground_table.crapkit_expected(truth["outer.inner"])[0]))


def _only(artifact: dict, probe: str, parser: str) -> dict:
    """The recording cut to the one probe file the repo holds."""
    if parser == "coveragepy":
        return {**artifact, "files": {probe: artifact["files"][probe]}}
    return {probe: artifact[probe]}


@pytest.mark.process
@pytest.mark.parametrize("case, probe, recording, functions", ONE_LANGUAGE,
                         ids=[case for case, *_ in ONE_LANGUAGE])
def test_nested_and_branchless_functions_read_their_own_counts(tmp_path, case, probe, recording,
                                                               functions):
    rows = _one_language_rows(tmp_path, probe, recording)
    truth = {row.function: row for row in ground_table.rows_for(recording, "call", (probe,))}

    assert _scored_covs(rows, truth, functions) == _truth_covs(truth, functions)


def _scored_covs(rows: dict, truth: dict, functions: tuple) -> dict:
    """{function: crapkit's cov, or None when no row starts on its line}."""
    found = {name: rows.get(truth[name].start) for name in functions}
    return {name: row.cov if row else None for name, row in found.items()}


def _truth_covs(truth: dict, functions: tuple) -> dict:
    return {name: float(ground_table.crapkit_expected(truth[name])[0]) for name in functions}


# --- nightly: the producers rerun ---------------------------------------------------------------

REGENERATE = probe_repo.HERE / "regenerate.py"
MISSING = 3


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("producer", sorted([*probe_repo.PRODUCERS, "coveragepy-reports-7.16.1",
                                             "pytest-cov-7.1.0-contexts"]))
def test_the_producer_rerun_matches_its_recording(producer):
    """regenerate.py check: exit 0 when the fresh run equals the recording, 1
    when it differs, 3 when the producer cannot run here (an infra miss)."""
    tiers.require_process(producer)
    done = hang_guard.run([sys.executable, str(REGENERATE), "check", "--producer", producer],
                          text=True, encoding="utf-8", errors="replace")
    if done.returncode == MISSING:
        runlog.note("infra", message=done.stderr.strip())
    assert done.returncode == 0, done.stderr


def test_a_half_built_producer_venv_is_built_again(tmp_path, monkeypatch):
    """A venv whose install failed (no network) is built again on the next call,
    so run.py's retry meets the same infra miss, never a venv without its
    producer; a venv whose install finished is reused."""
    from accuracy.coverage_oracles import regenerate
    calls = []

    def uv(*args: str) -> None:
        calls.append(args[0])
        if args[0] == "venv":
            python = regenerate._venv_python(Path(args[-1]))
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"")
        elif calls.count("pip") == 1:
            raise regenerate.ProducerMissing("no network for a venv")

    monkeypatch.setattr(regenerate, "_uv", uv)
    monkeypatch.setenv("CRAPKIT_ACCURACY_PRODUCERS", str(tmp_path))
    producer = regenerate.PRODUCERS["coveragepy-7.16.1"]
    with pytest.raises(regenerate.ProducerMissing):
        regenerate.python_for(producer)
    regenerate.python_for(producer)
    regenerate.python_for(producer)

    assert calls == ["venv", "pip", "venv", "pip"]


@pytest.mark.parametrize("producer", probe_repo.REFUSED)
def test_a_rerun_crapkit_refuses_like_its_recording_differs_in_nothing(tmp_path, monkeypatch,
                                                                       producer):
    """crapkit refuses a coverage.py report without start_line (ruling CO-B2), so
    for such a producer the check compares refusals: a rerun refused for the same
    function equals its recording, read from any scratch path."""
    from accuracy.coverage_oracles import regenerate
    monkeypatch.syspath_prepend(str(regenerate.HERE))
    recording = regenerate.RECORDED / producer / "call.json"
    fresh = tmp_path / "out-call" / "canonical" / "call.json"
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(recording.read_bytes())

    assert regenerate.differences(regenerate.PRODUCERS[producer], fresh, recording) == []


def test_a_rerun_that_gains_start_line_differs_from_a_refused_recording(tmp_path, monkeypatch):
    """A producer that starts writing start_line parses where its recording was
    refused, and the check names crapkit's reading as moved."""
    from accuracy.coverage_oracles import regenerate
    monkeypatch.syspath_prepend(str(regenerate.HERE))
    producer = probe_repo.REFUSED[0]
    recording = regenerate.RECORDED / producer / "call.json"
    artifact = json.loads(recording.read_bytes())
    for data in artifact["files"].values():
        for region in data.get("functions", {}).values():
            region["start_line"] = min(region["executed_lines"] + region["missing_lines"]
                                       + region["excluded_lines"], default=1)
    fresh = tmp_path / "call.json"
    fresh.write_text(json.dumps(artifact), encoding="utf-8")

    found = regenerate.differences(regenerate.PRODUCERS[producer], fresh, recording)

    assert "call.json: crapkit's parsed FnCoverage differs" in found
