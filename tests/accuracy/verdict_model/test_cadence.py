"""cadence.tiered: the cases it names stay on push, the others run nightly."""
from __future__ import annotations

import pytest

from accuracy.verdict_model import cadence


def _tiers(params) -> list:
    return [[mark.name for mark in param.marks] for param in params]


def test_named_ids_stay_on_push_and_the_rest_run_nightly():
    params = cadence.tiered(["a", "b", "c"], push={"b"}, ids=["x", "b", "z"])

    assert _tiers(params) == [["nightly"], [], ["nightly"]]
    assert [param.id for param in params] == ["x", "b", "z"]


def test_a_value_names_a_case_and_unpack_spreads_it():
    params = cadence.tiered([(1, {"k": 1}), (2, {"k": 2})], push=[(2, {"k": 2})], unpack=True)

    assert _tiers(params) == [["nightly"], []]
    assert [param.values for param in params] == [(1, {"k": 1}), (2, {"k": 2})]


def test_a_push_name_that_names_no_case_is_refused():
    with pytest.raises(AssertionError, match="push names no case"):
        cadence.tiered(["a"], push={"typo"})
