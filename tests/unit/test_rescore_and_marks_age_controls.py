"""Controls: two answers 0.8.0 already gave, asserted in 0.8.0's own fields.

- rescore joins a function the run measured and the tree left unchanged to the
  run's coverage. test_unmeasured_coverage_is_flagged.py pins the same row with
  0.8.1's `unmeasured` field and imports score.flagged_crap, so on 0.8.0 that
  file does not import.
- ratchet report reads a mark no commit carries yet as open, uncommitted and
  0 days old. test_shallow_history_is_named.py pins it with 0.8.1's `shallow`
  field, a KeyError on 0.8.0.

Each test passes on 0.8.0 and on every later tree.
"""
from __future__ import annotations

import json

import pytest

from cli_inproc_repo import commit_all, repo, seed_artifacts, template_repo  # noqa: F401
from hand_scored_repo import run
from test_shallow_history_is_named import (KNOT, OLD, OLD_DEBT, ages, commit, git, marks,
                                           report, set_policy, write)


@pytest.fixture()
def measured(repo, capsys):  # noqa: F811 (the imported fixture)
    seed_artifacts(repo)
    code, out, err = run(repo, capsys, "coverage", "--reuse-artifacts")
    assert code == 0, out + err
    return repo


@pytest.mark.parametrize("argv", [("rescore",), ("rescore", "--gate")])
def test_rescore_joins_an_unchanged_function_to_the_runs_coverage(measured, capsys, argv):
    code, out, err = run(measured, capsys, *argv, "src/app.ts", "--json")

    rows = {r["function"].split("(")[0].strip(): r for r in json.loads(out)["functions"]}
    assert code in (0, 6), err
    assert (rows["dispatch"]["cov"], rows["dispatch"]["flag"]) == (1.0, "measured")


def test_a_mark_no_commit_carries_yet_reads_0d_open_and_uncommitted(tmp_path, capsys):
    root = tmp_path / "fresh"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "src/a.py", KNOT.format(n="old_debt"))
    set_policy(root, "max-age")
    commit(root, "init", OLD)
    write(root, "crapkit-ratchet.tsv", marks(OLD_DEBT))

    code, body, err = report(root, capsys, "--enforce")

    assert (code, body["open"], body["uncommitted"], ages(body), err) == (0, 1, 1, [0], "")
