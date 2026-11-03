"""Ground truth: each probe function's coverage, worked by hand from the arms its driver takes.

probes/<lang>/ground_truth.tsv is written from each producer's documented branch
model and the driver's calls, never from a producer's output. Three checks:

1. Each committed recording states exactly those arms and statements, read with
   counts_table (json.load, no crapkit). A producer that moved, or a table that
   is wrong, fails here before it can reach a score. An lcov recording is read
   with str.split and its lines go to the table's spans.
2. On push, this interpreter's coverage.py 7.16.1 run over the Python probe
   states them too, and equals the committed 7.16.1 recording.
3. crapkit's scored cov and flag for every function equal the README's term
   over those counts, after the README floor for a Python def-line layout.
   Where crapkit and the ground truth part, a rulings row says why. A
   coverage.py recording with no start_line is refused instead (ruling CO-B2).
   gcovr's Cobertura recordings of the C++ probe are scored here against the
   C++ table through a `parser = "cobertura"` lane.

Cross-format: one c8 run's istanbul JSON and lcov give each probe function the
same cov, and each function where the two part is a named finding (C8_FINDINGS).

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
import tempfile

import pytest

import hang_guard
from accuracy.coverage_oracles import counts_table, ground_table, mini_repo, probe_repo
from accuracy.kit import rulings, runlog, tiers

LIVE = "coveragepy-live"
RECORDED = sorted(name for name in {**ground_table.FAMILIES, **ground_table.LCOV_FAMILIES}
                  if name != LIVE)
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
    family = ground_table.family(producer)
    if (family, row.function) in SPAN_OVERLAP and row.path.split("/")[0] in TYPED:
        return "CO-B5"
    return (KNOWN.get((producer, row.function, row.scenario))
            or KNOWN.get((family, row.function, row.scenario)))


# --- 1 and 2: the recordings state the hand-worked arms ------------------------------------------

def _producer_rows(producer: str, artifact, spans: list) -> dict:
    """{(path, function or start): counts} with the producer's own attribution;
    an lcov tracefile names no function, so its lines go to the table's spans."""
    family = ground_table.family(producer)
    if family == "coveragepy":
        return {(row.path, row.name): row for row in counts_table.coveragepy_rows(artifact)}
    if family == "llvm":
        return {(row.path, row.start): row for row in counts_table.lcov_rows(artifact, spans)}
    return {(row.path, row.start): row for row in counts_table.istanbul_rows(artifact, "position")}


def _key(producer: str, row: ground_table.Truth):
    python = ground_table.family(producer) == "coveragepy"
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


def _mismatches(producer: str, scenario: str, artifact) -> list[str]:
    rows = _checked_rows(producer, scenario)
    found = _producer_rows(producer, artifact, sorted({(row.path, row.start, row.end) for row in rows}))
    lines = (_mismatch(producer, row, found) for row in rows)
    return [line for line in lines if line]


def _recorded(producer: str, scenario: str):
    """A JSON recording as its value, an lcov one as its text."""
    path = probe_repo.artifact(producer, scenario)
    data = path.read_bytes()
    return data.decode("utf-8") if path.suffix == ".lcov" else json.loads(data)


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


PYTHON_RECORDED = [name for name in RECORDED if ground_table.family(name) == "coveragepy"]


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
# gcovr's Cobertura joins here alone: gcov states other lines than llvm's model.
SCORED = [name for name in [*RECORDED, LIVE, *ground_table.COBERTURA_FAMILIES]
          if name not in probe_repo.REFUSED]


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


# --- cross-format: one c8 run, read as istanbul JSON and as lcov ------------------------------
#
# c8 12.0.0 writes both reports from one run of the mjs probe, so the two
# readings share one set of counts. crapkit's cov agrees on every other
# function; each function where the two part is a named finding below:
# (istanbul cov, lcov cov) to 4 places, and why they part.
C8 = "c8-12.0.0"
C8_PROBE = "mjs/shapes.mjs"
_ARMS_BY_LINE = ("c8 writes the callback's function-block arm on line 46, the callback's first line, and lcov's "
                 "join gives each arm to the innermost span holding its line: withCallback keeps its own block "
                 "arm on line 45 (1 of 1) and the callback takes line 46's arm and the untaken arm on line 50 "
                 "(1 of 2). The istanbul reading of the same run gives each 2 of 3")
_NO_CALL_COUNT = ("an uncalled function: c8 writes no BRDA arm for it, so lcov scores its DA lines, and the line "
                  "named here runs at import outside the function (the module's binding, the class body); the "
                  "lcov reader reads no call count (FNDA), and the istanbul reading scores the function 0")
C8_FINDINGS = {
    "call": {"withCallback ( values )": ((0.6667, 1.0), _ARMS_BY_LINE),
             "(anonymous)": ((0.6667, 0.5), _ARMS_BY_LINE)},
    "idle": {"arrow ( value )": ((0.0, 1.0), f"line 60, {_NO_CALL_COUNT}"),
             "method ( value )": ((0.0, 0.1667), f"line 86, {_NO_CALL_COUNT}")},
}


def _c8_covs(top: Path, parser: str, recording: Path) -> dict[str, float]:
    """{function: crapkit's cov} for the mjs probe scored from one recording."""
    toml = mini_repo.config([mini_repo.scope("s", ["mjs"], ["javascript"])],
                            [mini_repo.lane("cov", parser, ["s"])])
    driver = mini_repo.build(top, {"crapkit.toml": toml,
                                   C8_PROBE: (probe_repo.PROBES / C8_PROBE).read_bytes(),
                                   "recorded/cov.json": recording.read_bytes()})
    result = driver.run("coverage", "--export", "scored.tsv")
    assert result.code == 0, result.stderr
    text = (driver.root / "scored.tsv").read_bytes().decode("utf-8")
    return {row.name: row.cov for row in probe_repo.read_scored(text)}


@pytest.mark.process
@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_one_c8_run_reads_the_same_through_istanbul_and_lcov(tmp_path, scenario):
    istanbul = _c8_covs(tmp_path / "istanbul", "istanbul",
                        probe_repo.RECORDED / C8 / f"{scenario}.json")
    lcov = _c8_covs(tmp_path / "lcov", "lcov", probe_repo.RECORDED / C8 / f"{scenario}.lcov")

    parted = {name: (round(cov, 4), round(lcov[name], 4)) for name, cov in istanbul.items()
              if lcov[name] != cov}

    assert set(istanbul) == set(lcov)
    assert parted == {name: covs for name, (covs, _) in C8_FINDINGS[scenario].items()}


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


@pytest.mark.process
def test_the_probe_root_is_spelled_as_the_producers_report_it(tmp_path, monkeypatch):
    """jest reports each file by its real path. A GitHub Windows runner's TEMP is
    the short name C:/Users/RUNNER~1/..., so a probe root kept as tempfile
    spelled it stayed in every jest key, and both jest reruns read as changed
    recordings. A junction (a symlink elsewhere) spells one directory two ways,
    as a short name does."""
    from accuracy.coverage_oracles import regenerate
    (tmp_path / "real").mkdir()
    regenerate._link(tmp_path / "real", tmp_path / "alias")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "alias"))
    seen = []

    regenerate._each(["coveragepy-7.16.1"], lambda producer, root, scenario, out: seen.append(
        (root, out)))

    assert seen and all(path == path.resolve() for pair in seen for path in pair)


# --- lcov: crapkit's evidence is the tracefile's own counts ---------------------------
#
# counts_table reads each tracefile with str.split and no crapkit: DA counts per
# line, BRDA arms per line, and the LF, LH, BRF and BRH lines the producer
# summed. crapkit's per-file evidence must say the same about every SF path.
# crapkit reads the file in a child process, as the CLI checks run it, so this
# file imports nothing of crapkit.

LCOV_RECORDINGS = ["c8-12.0.0", "cargo-llvm-cov-0.9.1", "coveragepy-reports-7.16.1",
                   "llvm-cov-19.1.7", "vitest-istanbul-5.0.1"]
_LCOV_READ = """
import json, sys
from pathlib import Path
from crapkit import coverage_lcov
from crapkit.config import Lane
path = Path(sys.argv[1])
lane = Lane(name="lcov", command="x", artifact=path.name, parser="lcov", scopes=("src",))
evidence = coverage_lcov.read(lane, path.parent, path)[1]
print(json.dumps({key: [list(e.hit_lines), list(e.missed_lines), [list(b) for b in e.branches]]
                  for key, e in evidence.items()}))
"""


def _lcov_evidence(path: Path) -> dict:
    """{key: [hit lines, missed lines, [line, arms, taken] triples]} as crapkit reads it."""
    child = hang_guard.run([sys.executable, "-c", _LCOV_READ, str(path)], text=True,
                           encoding="utf-8")
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout)


def _split_records(text: str) -> str:
    """Each SF record written as two records of the same SF, its DA and BRDA
    lines dealt between them in turn, the way two test runs of one file merge:
    a reader that keeps the second record instead of merging loses half."""
    out: list[str] = []
    halves: tuple[list[str], list[str]] = ([], [])
    for line in text.splitlines():
        tag = line.partition(":")[0]
        if tag == "SF":
            sf, halves = line, ([], [])
        elif tag in ("DA", "BRDA"):
            halves[len(halves[0]) > len(halves[1])].append(line)
        elif tag == "end_of_record":
            out += [sf, *halves[0], "end_of_record", sf, *halves[1], "end_of_record"]
    return "\n".join(out) + "\n"


@pytest.mark.parametrize("split", [False, True], ids=["as-recorded", "split-in-two-records"])
@pytest.mark.parametrize("scenario", ["call", "idle"])
@pytest.mark.parametrize("name", LCOV_RECORDINGS)
def test_crapkit_reads_each_lcov_recording_as_its_own_counts(tmp_path, name, scenario, split):
    path = probe_repo.RECORDED / name / f"{scenario}.lcov"
    text = path.read_text(encoding="utf-8")
    lines, files = counts_table.lcov_lines(text), counts_table.lcov_files(text)
    if split:
        path = tmp_path / path.name
        path.write_text(_split_records(text), encoding="utf-8")

    evidence = _lcov_evidence(path)

    assert sorted(evidence) == sorted(sf.removeprefix("./") for sf in lines)
    for sf, counts in lines.items():
        (hit, missed, branches), wrote = evidence[sf.removeprefix("./")], files[sf]
        assert hit == sorted(n for n, count in counts.items() if count > 0)
        assert missed == sorted(n for n, count in counts.items() if count == 0)
        assert {line: (total, taken) for line, total, taken in branches} == wrote.arms
        assert (len(hit) + len(missed), len(hit)) == (wrote.summary["LF"], wrote.summary["LH"])
        assert (sum(total for _, total, _ in branches), sum(taken for _, _, taken in branches)) == (
            wrote.summary["BRF"], wrote.summary["BRH"])



# --- Cobertura: crapkit's evidence is the report's own counts --------------------------
#
# counts_table reads each report with ElementTree and no crapkit: each class's
# line hits and condition-coverage, and the line-rate and branch-rate it wrote.
# crapkit's per-file evidence must say the same about every filename, and a
# report whose classes are each split in two must read the same, merged.

COBERTURA_RECORDINGS = ["gcovr-7.2", "coveragepy-reports-7.16.1"]
_COBERTURA_READ = _LCOV_READ.replace("coverage_lcov", "coverage_cobertura").replace(
    'parser="lcov"', 'parser="cobertura"')


def _cobertura_evidence(path: Path) -> dict:
    child = hang_guard.run([sys.executable, "-c", _COBERTURA_READ, str(path)], text=True,
                           encoding="utf-8")
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout)


def _split_classes(data: bytes) -> bytes:
    """Each class written as two classes of the same filename, its lines dealt
    between them in turn: a reader that keeps the second class loses half."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(data)
    for classes in root.iter("classes"):
        for cls in list(classes):
            twin = ET.SubElement(classes, "class", cls.attrib)
            twin_lines = ET.SubElement(twin, "lines")
            lines = cls.find("lines")
            for index, line in enumerate(list(lines)):
                if index % 2:
                    lines.remove(line)
                    twin_lines.append(line)
    return ET.tostring(root, encoding="utf-8")


@pytest.mark.parametrize("split", [False, True], ids=["as-recorded", "split-in-two-classes"])
@pytest.mark.parametrize("scenario", ["call", "idle"])
@pytest.mark.parametrize("name", COBERTURA_RECORDINGS)
def test_crapkit_reads_each_cobertura_recording_as_its_own_counts(tmp_path, name, scenario, split):
    path = probe_repo.RECORDED / name / f"{scenario}.xml"
    data = path.read_bytes()
    classes = counts_table.cobertura_classes(data)
    if split:
        path = tmp_path / path.name
        path.write_bytes(_split_classes(data))

    evidence = _cobertura_evidence(path)

    assert sorted(evidence) == sorted(cls.filename for cls in classes)
    for cls in classes:
        hit, missed, branches = evidence[cls.filename]
        assert hit == sorted(n for n, count in cls.hits.items() if count > 0)
        assert missed == sorted(n for n, count in cls.hits.items() if count == 0)
        assert {line: (total, taken) for line, total, taken in branches} == {
            line: pair for line, pair in cls.conditions.items() if pair[0]}
        # The rates are the producer's rounding: coverage.py keeps 4 significant digits.
        assert len(hit) / len(cls.hits) == pytest.approx(cls.line_rate, rel=1e-3)
        arms = sum(total for _, total, _ in branches)
        assert (sum(taken for _, _, taken in branches) / arms if arms else 1) == pytest.approx(
            cls.branch_rate, rel=1e-3)


# Accuracy check 3, cross-format: one gcov run written by gcovr as lcov and as
# Cobertura, and one coverage.py data file reported as lcov and as `coverage
# xml`, give the same per-file evidence through the two readers.
@pytest.mark.parametrize("scenario", ["call", "idle"])
@pytest.mark.parametrize("name", COBERTURA_RECORDINGS)
def test_one_run_reads_the_same_through_lcov_and_cobertura(name, scenario):
    lcov = _lcov_evidence(probe_repo.RECORDED / name / f"{scenario}.lcov")
    cobertura = _cobertura_evidence(probe_repo.RECORDED / name / f"{scenario}.xml")

    assert cobertura == lcov
