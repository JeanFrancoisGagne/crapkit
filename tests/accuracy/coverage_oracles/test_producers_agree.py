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
"""
import json

import pytest

from accuracy.coverage_oracles import counts_table, ground_table, probe_repo
from accuracy.kit import rulings

RECORDED = probe_repo.RECORDED
NEGATIVE = RECORDED / "negative-counters" / "coverage-final.json"
ISTANBUL = sorted(path for path in RECORDED.glob("*/*.json")
                  if "files" not in json.loads(path.read_bytes()))


def _crapkit_counts(path) -> dict:
    from crapkit.coverage_istanbul import parse_istanbul_both_file
    per_file = parse_istanbul_both_file(path, repo_root="")[0]
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
    from crapkit.coverage_istanbul import parse_istanbul_both_file
    negatives = _negatives(json.loads(NEGATIVE.read_bytes()))

    parse_istanbul_both_file(NEGATIVE, repo_root="")
    note = capsys.readouterr().err

    assert (sum(negatives.values()), len(negatives)) == (74, 25)
    assert f"{sum(negatives.values())} negative derived branch count(s) in {len(negatives)} file(s)" in note


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
