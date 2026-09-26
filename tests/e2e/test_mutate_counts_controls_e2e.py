"""Controls: mutate's counts for a real kill and a real survivor, in 0.8.0's own fields.

test_mutate_e2e.py::test_a_mutant_whose_suite_gave_no_result_is_counted_apart pins
the kill and live rows together with 0.8.1's `timed_out` and `no_verdict`
counts, which 0.8.0's JSON lacks, so on 0.8.0 those rows stop at a KeyError.
This test replays them and asserts `mutants`, `killed` and `survived` alone, so
it passes on 0.8.0 and on every later tree.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_mutate_e2e import _no_result_repo, run_cli


@pytest.mark.parametrize("mode, counts", [("kill", (2, 2, 0)), ("live", (2, 0, 2))])
def test_a_real_kill_and_a_real_survivor_are_counted_as_before(tmp_path: Path, mode, counts):
    repo = _no_result_repo(tmp_path, mode)

    res = run_cli(repo, "mutate", "--files", "hot.py", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    out = json.loads(res.stdout)
    assert tuple(out[k] for k in ("mutants", "killed", "survived")) == counts, out
