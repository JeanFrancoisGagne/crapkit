"""Istanbul attribution across producers, and the documented rule over every recording.

1. crapkit's per-function counts equal counts_table's `line` rule
   (docs/lanes.md#what-the-istanbul-parser-reads) over every istanbul recording,
   the 25-file negative-counter artifact included: crapkit's heap sweep against a
   plain scan written from the docs.
2. The negative-counter artifact from a large consumer repo: the 74 negative
   counters json.load finds are the ones crapkit clamps and names, and each reads
   as not taken (R36).
3. The providers agree: vitest's v8 and istanbul providers state the same arms
   and statements for every function both measured, and nyc and jest with babel
   agree with vitest's istanbul provider, except where a rulings row says why.
4. Raw v8-to-istanbul output (c8, and jest's v8 provider) is not the istanbul
   model, so crapkit's scores over it part from the ground truth (D7, CO4).
5. crap4py 0.1.1 over the same coverage.py run's lcov: its rule, written from
   its own docs, reproduces its recorded report; on a function with branches
   and no nested function it gives crapkit's number; a function with no branch
   reads 1.0 there and statement coverage in crapkit (ruling CO8).
"""
import json

import pytest

from accuracy.coverage_oracles import counts_table, ground_table, mini_repo, probe_repo, under_test
from accuracy.kit import rulings

RECORDED = probe_repo.RECORDED
NEGATIVE = RECORDED / "negative-counters" / "coverage-final.json"
ISTANBUL = sorted(path for path in RECORDED.glob("*/*.json")
                  if "files" not in json.loads(path.read_bytes()))


ISTANBUL_READER = under_test.crapkit("coverage_istanbul")
COVERAGE_PY = under_test.crapkit("coverage_py")


def _crapkit_counts(path) -> dict:
    per_file = ISTANBUL_READER.parse_istanbul_both_file(path, repo_root="")[0]
    return {(key, fn.start, fn.end): (fn.branches_total, fn.branches_covered,
                                      fn.statements_total, fn.statements_covered, fn.invoked)
            for key, fns in per_file.items() for fn in fns}


def _table_counts(path) -> dict:
    rows = counts_table.istanbul_rows(json.loads(path.read_bytes()), "line")
    return {(row.path, row.start, row.end): (row.total, row.covered, len(row.stmts),
                                             len(row.stmts_run), row.invoked) for row in rows}


@pytest.mark.parametrize("path", ISTANBUL, ids=lambda path: f"{path.parent.name}-{path.name}")
def test_crapkit_attributes_every_recording_by_the_documented_line_rule(path):
    assert _crapkit_counts(path) == _table_counts(path)


def _negative_count(data: dict) -> int:
    return sum(1 for hits in data.get("b", {}).values() for value in hits if value < 0)


def _negatives(artifact: dict) -> dict[str, int]:
    counts = {key: _negative_count(data) for key, data in artifact.items()}
    return {key: count for key, count in counts.items() if count}


def test_negative_derived_counters_clamp_to_not_taken(capsys):
    """R36: 74 negatives in 25 files, each at index 1 of an if; crapkit clamps
    them, names the count, and each reads not taken."""
    negatives = _negatives(json.loads(NEGATIVE.read_bytes()))

    ISTANBUL_READER.parse_istanbul_both_file(NEGATIVE, repo_root="")
    note = capsys.readouterr().err

    assert (sum(negatives.values()), len(negatives)) == (74, 25)
    assert f"{sum(negatives.values())} negative derived branch count(s) in {len(negatives)} file(s)" in note


@pytest.mark.process
def test_recorded_v8_negative_counters_clamp(tmp_path):
    """R36 through the CLI: one negative derived branch count in a lane's
    artifact is clamped and named, the run scores, and the arm reads not taken
    (ifElse's if, called with true only: 1 of 2 arms)."""
    artifact = json.loads((RECORDED / "vitest-v8-5.0.1" / "call.json").read_bytes())
    entry = artifact["js/shapes.js"]
    branch = next(index for index, arm in entry["branchMap"].items() if arm["loc"]["start"]["line"] == 4)
    entry["b"][branch] = [entry["b"][branch][0], -1]
    toml = mini_repo.config([mini_repo.scope("js", ["js"], ["javascript"])],
                            [mini_repo.lane("js", "istanbul", ["js"])])
    driver = mini_repo.build(tmp_path / "repo", {
        "crapkit.toml": toml, "js/shapes.js": (probe_repo.PROBES / "js/shapes.js").read_bytes(),
        "recorded/js.json": json.dumps({"js/shapes.js": entry}).encode()})

    result = driver.run("coverage", "--export", "scored.tsv")

    assert result.code == 0, result.stderr
    assert "1 negative derived branch count(s) in 1 file(s)" in result.stderr
    rows = {line.split("\t")[3]: line.split("\t")[11] for line in
            (driver.root / "scored.tsv").read_text(encoding="utf-8").splitlines()[1:]}
    assert float(rows["3"]) == 0.5


# --- the providers agree --------------------------------------------------------------------------

def _positions(producer: str, scenario: str) -> dict:
    artifact = json.loads((RECORDED / producer / f"{scenario}.json").read_bytes())
    return {(row.path, row.start): (set(row.arms), set(row.arms_taken), row.stmts, row.stmts_run)
            for row in counts_table.istanbul_rows(artifact, "position")}


# Functions whose arms two providers state differently on purpose: the v8
# provider reads `v8 ignore` hints (ground_truth.tsv) and counts a default
# parameter as taken whenever the function runs (CO3).
PARTED = {"v8": {71, 22}, "istanbul": {71}}


def _disagreements(left: str, right: str, scenario: str, parted: set) -> list:
    ours, theirs = _positions(left, scenario), _positions(right, scenario)
    shared = set(ours) & set(theirs)
    return sorted(key for key in shared if key[1] not in parted and ours[key] != theirs[key])


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_vitest_v8_and_istanbul_state_the_same_arms(scenario):
    assert _disagreements("vitest-v8-5.0.1", "vitest-istanbul-5.0.1", scenario, PARTED["v8"]) == []


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
@pytest.mark.parametrize("producer", ["nyc-18.0.0", "jest-babel-30.5.2"])
def test_nyc_and_jest_babel_state_what_vitest_istanbul_states(producer, scenario):
    assert _disagreements(producer, "vitest-istanbul-5.0.1", scenario, set()) == []


def _scored_cov(probe_run, producer: str, scenario: str) -> dict:
    return {key: row.cov for key, row in probe_run.scored(producer, scenario).items()}


@pytest.mark.process
@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_crapkit_scores_the_providers_alike(probe_run, scenario):
    v8 = _scored_cov(probe_run, "vitest-v8-5.0.1", scenario)
    istanbul = _scored_cov(probe_run, "vitest-istanbul-5.0.1", scenario)
    parted = PARTED["v8"] | {63}

    assert sorted(key for key in v8 if key[1] not in parted and v8[key] != istanbul[key]) == []


# --- raw v8-to-istanbul is not the model ---------------------------------------------------------

def _off(row: ground_table.Truth, scored: dict) -> bool:
    """A measured row, other than the v8-hinted one, whose score parts from the table."""
    if row.unmeasured or row.function == "ignoredV8":
        return False
    return scored[(row.path, row.start)].cov != float(ground_table.expected(row))


def _off_truth(probe_run, producer: str) -> int:
    """How many modelled functions crapkit scores away from the ground truth."""
    off = 0
    for scenario in probe_repo.SCENARIOS:
        scored = probe_run.scored(producer, scenario)
        rows = ground_table.rows_for("vitest-istanbul-5.0.1", scenario,
                                     probe_repo.PRODUCERS[producer][1])
        off += sum(1 for row in rows if _off(row, scored))
    return off


@pytest.mark.process
@rulings.applies("D7")
def test_raw_c8_output_parts_from_the_ground_truth(probe_run):
    rulings.pin_ruling("D7", crapkit=_off_truth(probe_run, "c8-12.0.0"), oracle=0)


@pytest.mark.process
@rulings.applies("CO4")
def test_jest_v8_output_parts_from_the_ground_truth(probe_run):
    rulings.pin_ruling("CO4", crapkit=_off_truth(probe_run, "jest-v8-30.5.2"), oracle=0)


# --- crap4py 0.1.1 over the same run's lcov ------------------------------------------------------

REPORTS = RECORDED / "coveragepy-reports-7.16.1"
# `crap4py py --lcov <scenario>.lcov` over probes/py, crap4py 0.1.1 from PyPI
# (wheel sha256 2cdaf28dccfc88313c95f1bacdf99fe19a39efbe9855d981352bb02a7a17a90c),
# module paths written with forward slashes.
CRAP4PY = RECORDED / "crap4py-0.1.1"


def brda(lcov: str) -> list[tuple[int, bool]]:
    """(line, taken) per BRDA record; `-` is not taken, line 0 is skipped."""
    records = []
    for line in lcov.splitlines():
        if line.startswith("BRDA:"):
            number, _, _, taken = line[5:].split(",", 3)
            records += [(int(number), taken not in ("-", "0"))] if int(number) else []
    return records


def crap4py_coverage(records: list[tuple[int, bool]], start: int, end: int) -> float:
    """https://github.com/gabadi/crap4py/blob/v0.1.1/src/crap4py/coverage.py#L1-L8: the
    in-range BRDA records taken over those in range; none in range reads 1.0."""
    taken = [hit for line, hit in records if start <= line <= end]
    return sum(taken) / len(taken) if taken else 1.0


def crap4py_report(scenario: str) -> dict[str, float]:
    """{function: Cov% / 100} off crap4py's recorded report."""
    rows = (CRAP4PY / f"{scenario}.txt").read_text(encoding="utf-8").splitlines()[4:]
    return {cells[0]: float(cells[3].rstrip("%")) / 100 for cells in map(str.split, rows) if cells}


def _spans(scenario: str) -> dict[str, tuple[int, int]]:
    """{crap4py's name (the def's own name, a method as Class.method): (start, end)}."""
    rows = ground_table.rows_for("coveragepy-7.16.1", scenario, probe_repo.PYTHON)
    return {row.function.split(".")[-1] if row.function.startswith("outer.") else row.function:
            (row.start, row.end) for row in rows}


def _lcov(scenario: str) -> list[tuple[int, bool]]:
    return brda((REPORTS / f"{scenario}.lcov").read_text(encoding="utf-8"))


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_the_crap4py_rule_reproduces_its_report(scenario):
    records = _lcov(scenario)
    model = {name: round(crap4py_coverage(records, *span), 3) for name, span in _spans(scenario).items()}

    assert {name: round(value, 3) for name, value in crap4py_report(scenario).items()} == model


def _crapkit_python(scenario: str) -> dict[str, object]:
    per_file = COVERAGE_PY.parse_coveragepy_both_file(REPORTS / f"{scenario}.json", path_prefix="")[0]
    return {fn.name: fn for fn in per_file["py/shapes.py"]}


def _branchy_leaves(scenario: str) -> list[str]:
    """Functions with a BRDA record in range and no nested function inside."""
    records, spans = _lcov(scenario), _spans(scenario)
    return [name for name, span in spans.items()
            if _has_brda(records, span) and not _holds_another(span, spans.values())]


def _holds_another(span: tuple[int, int], spans) -> bool:
    return any(span[0] < inner[0] <= inner[1] <= span[1] for inner in spans)


def _has_brda(records: list[tuple[int, bool]], span: tuple[int, int]) -> bool:
    return any(span[0] <= line <= span[1] for line, _ in records)


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_crapkit_gives_crap4py_s_number_on_functions_with_branches(scenario):
    ours = {name.split(".")[-1]: fn for name, fn in _crapkit_python(scenario).items()}
    records, spans = _lcov(scenario), _spans(scenario)
    names = _branchy_leaves(scenario)

    assert names and {name: ours[name.split(".")[-1]].coverage for name in names} == {
        name: crap4py_coverage(records, *spans[name]) for name in names}


@rulings.applies("CO8")
def test_co8_a_function_with_no_branch_reads_full_in_crap4py():
    """branchless() in the idle scenario: no BRDA record in its span, and no
    statement of it ran."""
    fn = _crapkit_python("idle")["branchless"]

    rulings.pin_ruling("CO8", crapkit=fn.coverage,
                       oracle=crap4py_coverage(_lcov("idle"), *_spans("idle")["branchless"]))
