"""What each position of a scored run IS.

`_scored_run` used to return a bare 8-tuple. `coverage` unpacked it dropping
position 5 and `verify` dropped 7 and 8, both with `_`, so the only record of
what any position held was the order of the names in the unpack line. Two of
them are failures of different kinds: position 4 is the lanes that FAILED to
run, position 5 is the tests that failed inside a lane that ran. `coverage`
named position 4 `failures` and never read position 5.

These tests pin the field names and which value each one carries. The lane run
is stubbed: the names are the subject, not lizard or git.
"""
import types

import pytest

import crapkit.cli.scoring as scoring
import crapkit.score
from crapkit.cli import _ScoredRun, _scored_run
from crapkit.cli.scoring import _Corpus

CORPUS = _Corpus(files=7, skipped_max_bytes=1)
PROVENANCE = {"unit": {"failures": ["tests/test_a.py::test_a"]}}


@pytest.fixture
def run(monkeypatch) -> _ScoredRun:
    """One scored run with every stage stubbed to a value only that stage can produce."""
    cfg = types.SimpleNamespace(scope_paths={}, max_parallel_lanes=1, target=80.0,
                                scope_targets={}, coverage_optional_scopes=())
    monkeypatch.setattr(scoring, "_build_inventory",
                        lambda root, cfg, git: ("cafe123", ["raw row"], CORPUS, 3,
                                                {"crapkit": "0.4.5"}))
    monkeypatch.setattr(scoring, "_run_lanes",
                        lambda *a, **k: ({}, PROVENANCE, {"ui": "exit 2"}, []))
    monkeypatch.setattr(crapkit.score, "score_rows", lambda rows, cov, **k: ["scored row"])
    return _scored_run(None, cfg, [], reuse_artifacts=False, git=object())


def test_a_scored_run_names_its_eight_positions(run):
    assert run._fields == ("commit", "scored", "provenance", "lane_errors", "test_failures",
                           "tool_versions", "corpus", "cache_hits")


def test_each_field_holds_what_its_name_says(run):
    assert run.commit == "cafe123"
    assert run.scored == ["scored row"]
    assert run.provenance == PROVENANCE
    assert run.lane_errors == {"ui": "exit 2"}
    assert run.test_failures == {"tests/test_a.py::test_a"}
    assert run.tool_versions == {"crapkit": "0.4.5"}
    assert run.corpus == CORPUS
    assert run.cache_hits == 3


def test_a_failed_lane_and_a_failed_test_are_separate_fields(run):
    """The two used to sit at positions 4 and 5 of an unnamed tuple, and
    `coverage` read one of them under the other's name."""
    assert run.lane_errors != run.test_failures


def test_the_positions_still_unpack_in_the_order_callers_read_them(run):
    commit, scored, provenance, lane_errors, tests, versions, corpus, hits = run

    assert (commit, scored, provenance) == (run.commit, run.scored, run.provenance)
    assert (lane_errors, tests, versions) == (run.lane_errors, run.test_failures,
                                              run.tool_versions)
    assert (corpus, hits) == (run.corpus, run.cache_hits)
