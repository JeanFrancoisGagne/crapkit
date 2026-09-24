"""A lane's test results, absent rather than zero, through every command that reads them.

A lane records `tests_total`, `tests_skipped` and `failures` only when it parsed a
junit report. It records none of them when it declares no `results_artifact`, or when
`--reuse-artifacts` found the report gone, empty, malformed, cut short or saying the
run never finished. Each reader once took that absence for a value: `coverage` said
the lane ran 0 tests, a trusted run with no count hid the next run's drop, and
`verify` read a baseline with no failure list as one that failed nothing and a
baseline with no count as nothing to compare.

Every test here feeds one form of that absence through `main`, the entry point
`python -m crapkit` uses, over a real git repo, and asserts what the command says.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from cli_inproc_repo import commit_all, git, repo, seed_artifacts, template_repo  # noqa: F401

import pytest

from crapkit.cli import main
from crapkit.errors import ToolError
from crapkit.invocation import _self
from crapkit.lanes import _results_provenance
from crapkit.store import SnapshotStore

LANES_PAGE = Path(__file__).resolve().parents[2] / "docs" / "lanes.md"

JUNIT = "junit.xml"

# The forms a reused junit takes when it cannot be read. Each lands the lane on
# the no-counts path with one warning naming the file.
UNREADABLE = {
    "missing": None,
    "empty": b"",
    "malformed": b"<testsuite><testcase",
    "zero-testcases": b'<testsuite name="t" tests="0"></testsuite>',
    "crashed-worker": (b'<testsuites><testsuite name="t" tests="1"><testcase classname="t" '
                       b'name="c0"/></testsuite><error message="worker \'gw0\' crashed while '
                       b'running \'t::c1\'"/></testsuites>'),
    "count-mismatch": (b'<testsuite name="t" tests="20"><testcase classname="t" name="a"/>'
                       b'<testcase classname="t" name="b"/><testcase classname="t" name="c"/>'
                       b"</testsuite>"),
    "not-utf8": b'<testsuite name="t" tests="1"><testcase classname="t" name="caf\xe9"/></testsuite>',
}
# The forms a baseline's lane record takes when it holds no results. A verify
# over a junit it reused and could not read was one more, until it exited 5 and
# stored no run (test_a_verify_over_a_junit_it_could_not_read_stores_no_run).
BASELINE_GAPS = ("missing", "zero-testcases", "malformed", "crashed-worker", "undeclared")


def run(argv: list[str], repo, capsys) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def runs(repo) -> list[dict]:
    return SnapshotStore(repo / ".crapkit" / "crap.sqlite").list_runs()


def junit(repo, tests: int, *failing: str) -> None:
    """A finished report of `tests` cases named t::c0.., the named ids failing."""
    boom = '<failure message="boom"/>'
    cases = "".join(
        f'<testcase classname="t" name="c{i}">{boom if f"t::c{i}" in failing else ""}</testcase>'
        for i in range(tests))
    (repo / JUNIT).write_text(f'<testsuite name="t" tests="{tests}">{cases}</testsuite>',
                              encoding="utf-8")


def declare_results(repo, declared: bool = True) -> None:
    """The `unit` lane reads junit.xml, or stops reading it, committed so the
    config is the commit's own."""
    path = repo / "crapkit.toml"
    text = path.read_text(encoding="utf-8").replace(f'results_artifact = "{JUNIT}"\n', "")
    if declared:
        text = text.replace('artifact = "coverage/unit.json"',
                            f'artifact = "coverage/unit.json"\nresults_artifact = "{JUNIT}"', 1)
    path.write_text(text, encoding="utf-8")
    commit_all(repo, "results_artifact" if declared else "no results_artifact")


def break_junit(repo, form: str) -> None:
    content = UNREADABLE[form]
    if content is None:
        (repo / JUNIT).unlink(missing_ok=True)
    else:
        (repo / JUNIT).write_bytes(content)


def run_without_results(repo, capsys, form: str) -> None:
    """One trusted run whose `unit` lane recorded no test results, in the named way."""
    if form == "undeclared":
        declare_results(repo, declared=False)
        code, _, err = run(["coverage", "--reuse-artifacts"], repo, capsys)
        declare_results(repo)
    else:
        break_junit(repo, form)
        code, _, err = run(["coverage", "--reuse-artifacts"], repo, capsys)
    assert code == 0, err
    assert "tests_total" not in runs(repo)[-1]["lanes"]["unit"]


@pytest.fixture()
def counted(repo, capsys):
    """One trusted coverage run whose `unit` lane counted 20 passing tests."""
    declare_results(repo)
    seed_artifacts(repo)
    junit(repo, 20)
    code, _, err = run(["coverage", "--reuse-artifacts"], repo, capsys)
    assert code == 0, err
    return repo


def _real_lane_commands(repo, *, exit_code: int = 0) -> None:
    """Lane commands that touch their canned artifact, so a run without
    --reuse-artifacts has a fresh artifact to read. The `unit` lane's command
    then exits `exit_code`."""
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    for name, code in (("unit", exit_code), ("ui", 0)):
        script = f"import os, sys; os.utime('coverage/{name}.json'); sys.exit({code})"
        command = json.dumps(f'python -c "{script}"')
        text = text.replace('command = "python -c pass"\nartifact', f"command = {command}\nartifact", 1)
    (repo / "crapkit.toml").write_text(text, encoding="utf-8")
    commit_all(repo, "real lane commands")


# --- coverage: this run's lane has no count -----------------------------------

@pytest.mark.parametrize("form", sorted(UNREADABLE))
def test_coverage_warns_about_a_reused_junit_it_cannot_read_and_counts_nothing(
        counted, capsys, form):
    break_junit(counted, form)

    code, _, err = run(["coverage", "--reuse-artifacts"], counted, capsys)

    assert code == 0, err
    assert f"lane 'unit' reused {JUNIT} and cannot check it" in err, err
    assert "ran 0 tests" not in err and "fewer" not in err, err
    assert "tests_total" not in runs(counted)[-1]["lanes"]["unit"]


@pytest.mark.parametrize("reuse", [True, False], ids=["reuse", "real-run"])
def test_coverage_says_nothing_about_a_lane_that_stopped_declaring_a_junit(counted, capsys, reuse):
    declare_results(counted, declared=False)
    if not reuse:
        _real_lane_commands(counted)

    code, _, err = run(["coverage", *(["--reuse-artifacts"] if reuse else [])], counted, capsys)

    assert code == 0, err
    assert "tests" not in err, err


def test_coverage_names_a_real_drop_from_the_last_count(counted, capsys):
    junit(counted, 12)

    code, _, err = run(["coverage", "--reuse-artifacts"], counted, capsys)

    assert code == 0
    assert "lane 'unit' ran 12 tests, 8 fewer than the last trusted run's 20" in err, err


def test_a_lane_renamed_since_the_last_count_cannot_drop(counted, capsys):
    text = (counted / "crapkit.toml").read_text(encoding="utf-8")
    (counted / "crapkit.toml").write_text(text.replace('name = "unit"', 'name = "units"'),
                                          encoding="utf-8")
    junit(counted, 2)

    code, _, err = run(["coverage", "--reuse-artifacts"], counted, capsys)

    assert code == 0, err
    assert "fewer" not in err, err


def test_a_lane_counted_for_the_first_time_cannot_drop(repo, capsys):
    """The last trusted run's lane declared no results_artifact."""
    seed_artifacts(repo)
    assert run(["coverage", "--reuse-artifacts"], repo, capsys)[0] == 0
    declare_results(repo)
    junit(repo, 20)

    code, _, err = run(["coverage", "--reuse-artifacts"], repo, capsys)

    assert code == 0, err
    assert "fewer" not in err, err


def test_a_lane_no_run_ever_counted_says_nothing(repo, capsys):
    seed_artifacts(repo)
    assert run(["coverage", "--reuse-artifacts"], repo, capsys)[0] == 0

    code, _, err = run(["coverage", "--reuse-artifacts"], repo, capsys)

    assert code == 0 and "tests" not in err, err


def test_a_lane_left_out_of_a_subset_run_cannot_drop(counted, capsys):
    code, _, err = run(["coverage", "--reuse-artifacts", "--lane", "ui"], counted, capsys)

    assert code == 0, err
    assert "fewer" not in err, err


# --- coverage: the last trusted run has no count -------------------------------

@pytest.mark.parametrize("form", BASELINE_GAPS)
def test_a_run_that_counted_nothing_does_not_hide_the_next_drop(counted, capsys, form):
    run_without_results(counted, capsys, form)
    junit(counted, 12)

    code, _, err = run(["coverage", "--reuse-artifacts"], counted, capsys)

    assert code == 0, err
    assert "lane 'unit' ran 12 tests, 8 fewer than the last trusted run's 20" in err, err


# --- verify: suite size against a baseline with no count ------------------------

@pytest.mark.parametrize("form", BASELINE_GAPS)
def test_verify_compares_the_suite_with_the_newest_run_behind_the_baseline_that_counted_it(
        counted, capsys, form):
    run_without_results(counted, capsys, form)
    baseline = runs(counted)[-1]["id"]
    junit(counted, 12)

    code, out, err = run(["verify", "--reuse-artifacts"], counted, capsys)

    assert code == 0, out + err
    assert (f"warning: lane 'unit' runs 8 fewer tests than run 1 (baseline run {baseline} "
            "recorded no test count for it)") in err, err


def test_a_baseline_with_its_own_count_is_the_one_compared(counted, capsys):
    junit(counted, 12)

    code, _, err = run(["verify", "--reuse-artifacts"], counted, capsys)

    assert code == 0
    assert "warning: lane 'unit' runs 8 fewer tests than the baseline\n" in err, err


def emit_baseline(repo, capsys, rel: str = "base.tsv") -> None:
    code, out, err = run(["verify", "--reuse-artifacts", "--emit-baseline", rel], repo, capsys)
    assert code == 0, out + err


def strip_results(repo, rel: str = "base.tsv") -> None:
    """The file as crapkit 0.8.0 and older wrote it: a stamp carrying no test results."""
    stamp, rest = (repo / rel).read_text(encoding="utf-8").split("\n", 1)
    assert " results=" in stamp, stamp
    (repo / rel).write_text(stamp.split(" results=")[0] + "\n" + rest, encoding="utf-8")


def test_a_baseline_file_carries_the_count_it_compares(counted, capsys):
    emit_baseline(counted, capsys)
    junit(counted, 12)

    code, out, err = run(["verify", "--reuse-artifacts", "--baseline-tsv", "base.tsv"],
                         counted, capsys)

    assert code == 0, out + err
    assert "warning: lane 'unit' runs 8 fewer tests than the baseline\n" in err, err


def test_a_baseline_file_an_older_crapkit_wrote_says_it_holds_no_test_results(counted, capsys):
    emit_baseline(counted, capsys)
    strip_results(counted)
    junit(counted, 12)

    code, out, err = run(["verify", "--reuse-artifacts", "--baseline-tsv", "base.tsv", "--json"],
                         counted, capsys)

    assert code == 0, err
    assert ("warning: the baseline file base.tsv holds no test results, so every test failure "
            "counts as new and no suite size is compared; write it again with "
            f"`{_self()} verify --emit-baseline base.tsv` to carry them") in err, err
    assert json.loads(out)["lanes_without_baseline_results"] == []


def test_a_lane_that_stopped_declaring_a_junit_names_the_gap(counted, capsys):
    """The one lane with no count this run that verify still judges: it
    declares no junit, so there is nothing it failed to read."""
    declare_results(counted, declared=False)

    code, _, err = run(["verify", "--reuse-artifacts"], counted, capsys)

    assert code == 0, err
    assert ("warning: lane 'unit' wrote no test counts this run (no results_artifact was "
            "parsed), so the baseline's 20 tests cannot be compared") in err, err


@pytest.mark.parametrize("form", ["missing", "malformed"])
def test_a_reused_junit_verify_could_not_read_refuses_before_any_count_is_compared(
        counted, capsys, form):
    """These two forms once passed with the no-count line above; the refusal
    now comes first, and a run that stops there compares nothing."""
    break_junit(counted, form)

    code, _, err = run(["verify", "--reuse-artifacts"], counted, capsys)

    assert code == 5, err
    assert f"crapkit: {unread_refusal()}" in err, err
    assert "cannot be compared" not in err, err


def test_a_lane_renamed_since_the_baseline_compares_nothing(counted, capsys):
    text = (counted / "crapkit.toml").read_text(encoding="utf-8")
    (counted / "crapkit.toml").write_text(text.replace('name = "unit"', 'name = "units"'),
                                          encoding="utf-8")
    commit_all(counted, "rename the lane")
    junit(counted, 2)

    code, _, err = run(["verify", "--reuse-artifacts"], counted, capsys)

    assert code == 0, err
    assert "fewer" not in err and "cannot be compared" not in err, err


# --- verify: failures against a baseline with no failure list -------------------

@pytest.fixture()
def failing(repo, capsys):
    """One trusted coverage run whose `unit` lane recorded t::c0 failing."""
    declare_results(repo)
    seed_artifacts(repo)
    junit(repo, 3, "t::c0")
    code, _, err = run(["coverage", "--reuse-artifacts"], repo, capsys)
    assert code == 0, err
    return repo


@pytest.mark.parametrize("form", BASELINE_GAPS)
def test_a_failure_an_older_run_recorded_is_forgiven_past_a_baseline_with_no_list(
        failing, capsys, form):
    run_without_results(failing, capsys, form)
    baseline = runs(failing)[-1]["id"]
    junit(failing, 3, "t::c0")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], failing, capsys)

    payload = json.loads(out)
    assert code == 0, out + err
    assert payload["forgiven_failures"] == ["t::c0"]
    assert payload["lanes_without_baseline_results"] == []
    assert (f"warning: lane 'unit': baseline run {baseline} recorded no test results, so its "
            "failures are compared with run 1's") in err, err


@pytest.mark.parametrize("form", BASELINE_GAPS)
def test_a_failure_no_run_recorded_counts_as_new_and_says_why(repo, capsys, form):
    """The baseline's lane recorded no failure list, in the named way, and no
    run behind it did: the failure may predate the change, and nothing can
    tell. Exit 8 stands, since a gate fails closed, and the line says why."""
    declare_results(repo)
    seed_artifacts(repo)
    run_without_results(repo, capsys, form)
    junit(repo, 3, "t::c0")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], repo, capsys)

    payload = json.loads(out)
    assert code == 8, out + err
    assert payload["new_failures"] == ["t::c0"]
    assert payload["lanes_without_baseline_results"] == ["unit"]
    assert ("warning: lane 'unit': no trusted run at or behind the baseline recorded which of "
            "its tests failed, so its 1 new failure may predate this change; a baseline "
            "measured with results_artifact declared tells them apart") in err, err


def test_a_baseline_file_carries_the_failures_it_forgives(failing, capsys):
    """README Route 4: the file on the default branch is the only baseline a CI
    clone has, and it used to forgive nothing."""
    emit_baseline(failing, capsys)

    code, out, err = run(["verify", "--reuse-artifacts", "--baseline-tsv", "base.tsv", "--json"],
                         failing, capsys)

    payload = json.loads(out)
    assert code == 0, out + err
    assert payload["forgiven_failures"] == ["t::c0"]
    assert payload["baseline_run"] is None


def test_a_baseline_file_with_no_results_counts_every_failure_as_new_and_says_so(failing, capsys):
    emit_baseline(failing, capsys)
    strip_results(failing)

    code, out, err = run(["verify", "--reuse-artifacts", "--baseline-tsv", "base.tsv", "--json"],
                         failing, capsys)

    payload = json.loads(out)
    assert code == 8, out + err
    assert payload["lanes_without_baseline_results"] == ["unit"]
    assert "the baseline file base.tsv holds no test results" in err, err
    assert "no trusted run" not in err, "a file is not a stored run"


def test_a_failure_forgiven_under_a_renamed_lane_is_not_called_unjudged(failing, capsys):
    text = (failing / "crapkit.toml").read_text(encoding="utf-8")
    (failing / "crapkit.toml").write_text(text.replace('name = "unit"', 'name = "units"'),
                                          encoding="utf-8")
    commit_all(failing, "rename the lane")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], failing, capsys)

    payload = json.loads(out)
    assert code == 0, out + err
    assert payload["forgiven_failures"] == ["t::c0"]
    assert payload["lanes_without_baseline_results"] == []
    assert "warning: lane 'units'" not in err, err


def test_a_baseline_that_recorded_the_failure_forgives_it_quietly(failing, capsys):
    code, out, err = run(["verify", "--reuse-artifacts", "--json"], failing, capsys)

    assert code == 0, err
    assert json.loads(out)["forgiven_failures"] == ["t::c0"]
    assert "compared with run" not in err


def test_the_fork_point_that_declared_no_junit_blames_no_failure_silently(repo, capsys):
    """The Action's flow: `verify --base` against a fork point whose lane
    declared no results_artifact, from a branch that adds one."""
    seed_artifacts(repo)
    assert run(["coverage", "--reuse-artifacts"], repo, capsys)[0] == 0
    declare_results(repo)
    junit(repo, 3, "t::c0")

    code, out, err = run(["verify", "--reuse-artifacts", "--base", "HEAD~1", "--json"],
                         repo, capsys)

    payload = json.loads(out)
    assert code == 8, out + err
    assert payload["lanes_without_baseline_results"] == ["unit"]
    assert "may predate this change" in err, err


# --- verify: a baseline an older crapkit stored ---------------------------------

def _stored_by(repo, version: str, kind: str, lanes: dict, *, ok: bool | None = None) -> int:
    """A run as that crapkit stored it: its tool_versions and its lane records."""
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    rows = store.read_scored(runs(repo)[0]["id"])
    run_id = store.write_run(commit=git(repo, "rev-parse", "HEAD").strip(),
                             tool_versions={"crapkit": version, "lizard": "1.24.0"},
                             rows=rows, lanes=lanes, kind=kind)
    if ok is not None:
        store.set_verdict_ok(run_id, ok)
    return run_id


def test_a_failure_a_076_verify_retried_into_a_pass_is_new_when_it_fails_its_retry(
        counted, capsys):
    """crapkit 0.7.6 stored a passing verify whose t::c0 failed and then passed
    its flake retry: `failures` kept the id and no `retried_passes` named it.
    Read as a baseline failure, a real failure of t::c0 later was forgiven."""
    verify_run = _stored_by(counted, "0.7.6", "verify", {
        "unit": {"failures": ["t::c0"], "tests_total": 20, "tests_skipped": 0},
        "ui": {}}, ok=True)
    junit(counted, 20, "t::c0")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], counted, capsys)

    payload = json.loads(out)
    assert payload["baseline_run"] == verify_run
    assert code == 8, out + err
    assert payload["new_failures"] == ["t::c0"]
    assert (f"warning: lane 'unit': baseline run {verify_run} was written by crapkit 0.7.6, "
            "which kept a failure that passed its flake retry in its failure list, so its "
            "failures are compared with run 1's") in err, err


def test_a_coverage_run_an_older_crapkit_stored_still_forgives(repo, capsys):
    """Only a verify runs a flake retry, so an older coverage run's list holds."""
    seed_artifacts(repo)
    assert run(["coverage", "--reuse-artifacts"], repo, capsys)[0] == 0
    _stored_by(repo, "0.4.9", "coverage", {
        "unit": {"failures": ["t::c0"], "tests_total": 3, "tests_skipped": 0}, "ui": {}})
    declare_results(repo)
    junit(repo, 3, "t::c0")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], repo, capsys)

    assert code == 0, out + err
    assert json.loads(out)["forgiven_failures"] == ["t::c0"]


# --- verify: this run's lane has no failure list ---------------------------------

def unread_refusal(lane: str = "unit", path: str = JUNIT) -> str:
    return (f"lane {lane!r} declares results_artifact {path}, which this verify reused and "
            "could not read, so no test in it was checked for a new failure; run verify "
            "without --reuse-artifacts so the lane writes it again")


@pytest.mark.parametrize("form", sorted(UNREADABLE))
def test_verify_names_the_lane_whose_failures_it_could_not_check(counted, capsys, form):
    """A declared junit that `verify --reuse-artifacts` cannot read exits 5, as
    a real run over the same file does, so `verify passed.` never stands for a
    run that checked no test. Nothing is stored: a passing verify there became
    the next trusted baseline."""
    break_junit(counted, form)
    stored = runs(counted)

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], counted, capsys)

    assert code == 5, out + err
    assert json.loads(out)["error"] == {"exit": 5, "kind": "tool", "message": unread_refusal()}
    assert f"lane 'unit' reused {JUNIT} and cannot check it" in err, err
    assert runs(counted) == stored


def test_a_verify_over_a_junit_it_could_not_read_stores_no_run(counted, capsys):
    """The route that once made a trusted run with no count: that verify passed
    and became the baseline. Now the next verify still compares with run 1."""
    (counted / JUNIT).unlink()
    assert run(["verify", "--reuse-artifacts"], counted, capsys)[0] == 5
    junit(counted, 12)

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], counted, capsys)

    assert code == 0, out + err
    assert json.loads(out)["baseline_run"] == 1
    assert "warning: lane 'unit' runs 8 fewer tests than the baseline\n" in err, err


def test_the_refusal_names_every_lane_it_could_not_check(counted, capsys):
    """Each declared lane whose reused junit could not be read is named, in
    declaration order, in the one refusal."""
    text = (counted / "crapkit.toml").read_text(encoding="utf-8")
    (counted / "crapkit.toml").write_text(
        text.replace('artifact = "coverage/ui.json"',
                     'artifact = "coverage/ui.json"\nresults_artifact = "ui.xml"', 1),
        encoding="utf-8")
    commit_all(counted, "the ui lane reads ui.xml")
    (counted / JUNIT).unlink()

    code, _, err = run(["verify", "--reuse-artifacts"], counted, capsys)

    assert code == 5, err
    assert f"crapkit: {unread_refusal()}; {unread_refusal('ui', 'ui.xml')}\n" in err, err


def test_the_lanes_page_quotes_both_lines_the_refusal_prints(tmp_path, capsys):
    from crapkit.cli.verifying import _refuse_unread_results

    lane = SimpleNamespace(name="py", results_artifact=".crapkit/cov/junit-py.xml")
    provenance = _results_provenance(tmp_path, lane, reuse_artifact=True)
    with pytest.raises(ToolError) as refused:
        _refuse_unread_results([lane], {"py": provenance})

    page = LANES_PAGE.read_text(encoding="utf-8")
    assert f"{capsys.readouterr().err}crapkit: {refused.value}\nEXIT=5\n" in page


def test_a_lane_that_declares_no_junit_is_not_refused(counted, capsys):
    """Q27's other half: a lane that never declared a junit passes, named under
    lanes_without_results, since there was no report to read."""
    code, out, err = run(["verify", "--reuse-artifacts", "--json"], counted, capsys)

    assert code == 0, out + err
    assert json.loads(out)["lanes_without_results"] == ["ui"]


def test_a_lane_with_no_junit_that_exited_nonzero_is_named(repo, capsys):
    """The lane's exit code is recorded, not enforced, and with no junit it is
    the only sign a test failed."""
    seed_artifacts(repo)
    assert run(["coverage", "--reuse-artifacts"], repo, capsys)[0] == 0
    _real_lane_commands(repo, exit_code=1)

    code, out, err = run(["verify", "--json"], repo, capsys)

    payload = json.loads(out)
    assert code == 0, out + err
    assert payload["lanes_without_results"] == ["ui", "unit"]
    assert ("warning: lane 'unit' exited 1 and declares no results_artifact, so verify cannot "
            "see which of its tests failed; declare results_artifact (the lane's junit report) "
            "to check them") in err, err
    assert "lane 'ui'" not in err, "a lane that exited 0 has nothing to report"


def test_a_readable_failing_junit_fails_the_verdict(counted, capsys):
    junit(counted, 20, "t::c1")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], counted, capsys)

    payload = json.loads(out)
    assert code == 8, out + err
    assert payload["new_failures"] == ["t::c1"]
    assert payload["lanes_without_results"] == ["ui"]


def test_a_junit_a_real_run_never_wrote_fails_the_lane(counted, capsys):
    _real_lane_commands(counted)
    (counted / JUNIT).unlink()

    code, _, err = run(["verify"], counted, capsys)

    assert code == 5, err
    assert f"results_artifact {JUNIT} is missing" in err, err
