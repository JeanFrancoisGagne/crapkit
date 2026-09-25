"""Analysis determinism (R11): one file set reads the same, row for row, across
cold runs, hash seeds and pool versus serial analysis.

The expected value is another run's rows. The relation (every column equal,
rows in the same order) holds whatever crapkit computes, so no crapkit value
is taken on trust. Every run is spawned, since PYTHONHASHSEED only takes
effect when an interpreter starts, and each starts from a fresh repo, so no
cache is warm. The set holds more than 16 files, crapkit's pool threshold
(README "analysis_workers"), so a run that leaves analysis_workers at 0
analyzes in the process pool and one that sets it to 1 stays serial.
"""
from __future__ import annotations

import random

import pytest

from accuracy.analysis_oracles import analysis_inventory, analysis_tables

pytestmark = pytest.mark.process

SEEDS = ("0", "1", "4242")
POOL_THRESHOLD = 16


def _files(serial: bool) -> dict:
    files = {path: data for path, data in analysis_tables.probe_files().items()
             if "equivalence" in path or "shapes" in path.lower()}
    toml = analysis_inventory.config()
    if serial:
        toml = toml.replace("[crapkit]\n", "[crapkit]\nanalysis_workers = 1\n", 1)
    return {**files, "crapkit.toml": toml}


def _rows(work, seed: str, serial: bool = False) -> tuple:
    measured = analysis_inventory.measure(_files(serial), work, spawn=True,
                                          env={"PYTHONHASHSEED": seed})
    assert measured.code == 0, measured.stderr
    assert measured.rows, "the probe set produced no rows"
    return measured.rows


@pytest.fixture(scope="module")
def first_run(tmp_path_factory):
    return _rows(tmp_path_factory.mktemp("determinism-first"), SEEDS[0])


def test_the_set_reaches_the_pool():
    assert len(_files(serial=False)) - 1 > POOL_THRESHOLD


def test_two_cold_runs_match_row_for_row(first_run, tmp_path):
    assert _rows(tmp_path, SEEDS[0]) == first_run


@pytest.mark.parametrize("seed", SEEDS[1:])
def test_hash_seed_leaves_every_row_unchanged(seed, first_run, tmp_path):
    assert _rows(tmp_path, seed) == first_run


def test_pool_and_serial_analysis_give_identical_rows(first_run, tmp_path):
    assert _rows(tmp_path, SEEDS[0], serial=True) == first_run


@pytest.mark.nightly
def test_a_random_hash_seed_leaves_every_row_unchanged(first_run, tmp_path):
    seed = str(random.SystemRandom().randrange(1, 2**32 - 1))
    print(f"PYTHONHASHSEED={seed}")
    assert _rows(tmp_path, seed) == first_run, f"differs under PYTHONHASHSEED={seed}"
