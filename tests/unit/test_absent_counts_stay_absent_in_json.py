"""What a machine reader gets for a lane or a run that recorded no value.

A lane records `tests_total`, `tests_skipped` and `failures` only when it read a
junit report, and a duration only when a real run stamped one. A reader of the
JSON outputs cannot see stderr, so the absence has to survive into the payload
itself: the key left out, or null, never a 0 or an empty list that reads as a
suite of no tests that failed nothing.

Each test takes a run whose `unit` lane recorded no count in one of the ways it
happens (its junit gone or unreadable under `--reuse-artifacts`, or no
`results_artifact` declared) after a run that counted 20 tests with one failing,
and reads every output that carries lane data: `coverage --json`, `runs --json`,
`doctor --json`, and the MCP tools `list_runs` and `check_config`.

A verify stored with no verdict is the other stand-in these outputs could carry:
its `findings` column starts at 0 before a verdict is written.
"""
from __future__ import annotations

import json

from cli_inproc_repo import add_knotty, commit_all, repo, seed_artifacts, template_repo  # noqa: F401

import pytest

from crapkit import mcp_server
from crapkit.cli import main
from crapkit.store import SnapshotStore

JUNIT = "junit.xml"
RESULT_KEYS = {"tests_total", "tests_skipped", "failures"}


def run(argv: list[str], repo, capsys) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def junit(repo, tests: int, *failing: str) -> None:
    boom = '<failure message="boom"/>'
    cases = "".join(
        f'<testcase classname="t" name="c{i}">{boom if f"t::c{i}" in failing else ""}</testcase>'
        for i in range(tests))
    (repo / JUNIT).write_text(f'<testsuite name="t" tests="{tests}">{cases}</testsuite>',
                              encoding="utf-8")


def declare_results(repo, declared: bool) -> None:
    path = repo / "crapkit.toml"
    text = path.read_text(encoding="utf-8").replace(f'results_artifact = "{JUNIT}"\n', "")
    if declared:
        text = text.replace('artifact = "coverage/unit.json"',
                            f'artifact = "coverage/unit.json"\nresults_artifact = "{JUNIT}"', 1)
    path.write_text(text, encoding="utf-8")
    commit_all(repo, f"results_artifact declared: {declared}")


def lose_the_count(repo, form: str) -> None:
    if form == "undeclared":
        declare_results(repo, declared=False)
    elif form == "missing":
        (repo / JUNIT).unlink()
    else:
        (repo / JUNIT).write_bytes(b"<testsuite><testcase")


@pytest.fixture(params=["missing", "malformed", "undeclared"])
def countless(request, repo, capsys):
    """Run 1 counted 20 tests, t::c0 failing; run 2's `unit` lane recorded no
    count in the named way. Returns the repo and run 2's `coverage --json`."""
    declare_results(repo, declared=True)
    seed_artifacts(repo)
    junit(repo, 20, "t::c0")
    assert run(["coverage", "--reuse-artifacts"], repo, capsys)[0] == 0
    lose_the_count(repo, request.param)
    code, out, err = run(["coverage", "--reuse-artifacts", "--json"], repo, capsys)
    assert code == 0, err
    return repo, json.loads(out)


def test_coverage_json_leaves_the_counts_out_of_a_lane_that_recorded_none(countless):
    _, payload = countless

    assert RESULT_KEYS.isdisjoint(payload["lanes"]["unit"]), payload["lanes"]["unit"]


def test_the_stored_run_holds_no_count_for_that_lane(countless):
    repo, _ = countless
    first, second = SnapshotStore(repo / ".crapkit" / "crap.sqlite").list_runs()

    assert (first["lanes"]["unit"]["tests_total"], first["lanes"]["unit"]["failures"]) == (20, ["t::c0"])
    assert RESULT_KEYS.isdisjoint(second["lanes"]["unit"]), second["lanes"]["unit"]


def _stand_ins(payload) -> list[str]:
    """Every key under which a lane's count or duration reads as a measured 0 or []."""
    text = json.dumps(payload)
    return [key for key in ("tests_total", "tests_skipped", "failures", "seconds")
            if f'"{key}": 0' in text or f'"{key}": []' in text]


def test_runs_json_carries_no_count_at_all(countless, capsys):
    repo, _ = countless

    code, out, _ = run(["runs", "--json"], repo, capsys)

    assert code == 0
    assert _stand_ins(json.loads(out)) == []
    assert [r["lanes"] for r in json.loads(out)["runs"]] == [["ui", "unit"], ["ui", "unit"]]


def test_doctor_json_leaves_a_duration_no_run_stamped_null(countless, capsys):
    """A repo that only ever reused its artifacts never timed a lane."""
    repo, _ = countless

    _, out, _ = run(["doctor", "--json"], repo, capsys)

    assert [lane["seconds"] for lane in json.loads(out)["lanes"]] == [None, None]


@pytest.mark.parametrize("tool", ["list_runs", "check_config"])
def test_the_mcp_tools_relay_the_same_absence(countless, tool):
    repo, _ = countless

    result = mcp_server._call_tool(repo, tool, {})

    assert _stand_ins(result.get("structuredContent", result["content"][0]["text"])) == []


# --- a verify stored with no verdict ---------------------------------------------

def test_a_verify_stored_before_its_verdict_reads_as_no_verdict(repo, capsys):
    """An override whose alert command fails refuses after the run is stored,
    so that run never gets a verdict. `runs` shows `verdict=-` and null, the
    `findings` column keeps the 0 it starts at, and the run is not a baseline."""
    seed_artifacts(repo)
    assert run(["coverage", "--reuse-artifacts"], repo, capsys)[0] == 0
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(
        text.replace('alert_command = "python append_alert.py"',
                     "alert_command = 'python -c \"import sys; sys.exit(4)\"'"), encoding="utf-8")
    commit_all(repo, "an alert that fails")
    add_knotty(repo)
    assert run(["verify", "--reuse-artifacts", "--override", "hotfix"], repo, capsys)[0] == 5

    _, listed, _ = run(["runs"], repo, capsys)
    _, out, _ = run(["runs", "--json"], repo, capsys)

    stored = json.loads(out)["runs"][-1]
    assert (stored["kind"], stored["verdict_ok"], stored["findings"]) == ("verify", None, 0)
    assert stored["baseline"] is False
    assert "verify    verdict=-" in listed.splitlines()[-1], listed
