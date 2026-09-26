"""Controls: the lane-results verdicts 0.8.0 already gave, asserted in 0.8.0's own fields.

test_lane_results_absence.py pins these rows together with the fields 0.8.1 added
(`lanes_without_results`, `lanes_without_baseline_results`), which 0.8.0's JSON
does not carry, so on 0.8.0 those tests stop at a KeyError and say nothing about
the verdict. Each test here replays one of them and asserts only the exit code and
the fields 0.8.0 printed, so it passes on 0.8.0 and on every later tree: a change
that breaks a verdict users already relied on fails here first.
"""
from __future__ import annotations

import json

from cli_inproc_repo import commit_all, repo, template_repo  # noqa: F401
from test_lane_results_absence import counted, failing, junit, run  # noqa: F401


def test_verify_forgives_a_failure_the_baseline_recorded_under_the_lanes_old_name(
        failing, capsys):  # noqa: F811 (the imported fixture)
    text = (failing / "crapkit.toml").read_text(encoding="utf-8")
    (failing / "crapkit.toml").write_text(text.replace('name = "unit"', 'name = "units"'),
                                          encoding="utf-8")
    commit_all(failing, "rename the lane")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], failing, capsys)

    payload = json.loads(out)
    assert code == 0, out + err
    assert (payload["ok"], payload["new_failures"], payload["forgiven_failures"]) == \
        (True, [], ["t::c0"])


def test_verify_fails_on_a_failure_the_baseline_did_not_record(counted, capsys):  # noqa: F811
    junit(counted, 20, "t::c1")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], counted, capsys)

    payload = json.loads(out)
    assert code == 8, out + err
    assert (payload["ok"], payload["new_failures"]) == (False, ["t::c1"])
