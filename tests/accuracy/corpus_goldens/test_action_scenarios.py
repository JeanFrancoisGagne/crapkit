"""The GitHub Action's verdict and comment on three pull requests, run from action.yml.

action_runs runs the action's own steps against a clone of a two-commit repo:
commit A on main, then commit B, the pull request's head, which adds late(v)
(ccn 7, over the ceiling 6 of a cc-only scope). The expected verdicts are
written from README.md "What the verdict line covers" and "What the comment
looks like", never read from action.yml or comment.py:

- R90: a full clone scores the fork point first, so verify judges the diff
  from A: exit 6, "**verify failed, exit 6: complexity gate.**", and a gate
  bullet for late( v ) with ccn 7, cov 0%, crap 7.0 (a cc-only scope's CRAP is
  its ccn). gate "true" exits 6.
- R91: a depth-1 clone does not hold A, so the base run is not made; the
  comment says "verify judged no changed function" and quotes the shallow-clone
  reason, and gate "true" exits 1: the check fails as the full clone's does.
- R92: B also breaks the lane, and the workspace keeps an artifact from an
  earlier run: coverage exits 5, verify does not run, the comment says
  "no verdict: `crapkit coverage` exited 5", and gate "true" exits 5.

The comment's numbers are checked against kit.exact: CRAP load and grade over
the rows, and the worklist table's risk, ccn times the recency weight of the
file's commits (the newest commit of a two-timestamp log weighs 0.5).
"""
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
import re

import pytest

import hang_guard
from accuracy.corpus_goldens import action_runs, printed_runs, releases
from accuracy.kit import exact, repos, surfaces

pytestmark = pytest.mark.process
A_DATE, B_DATE = repos.EPOCH, repos.EPOCH + 3600
NOW = B_DATE + 86_400
BODY = ("    if v == 1:\n        return 1\n    if v == 2:\n        return 2\n"
        "    if v == 3:\n        return 3\n    if v == 4:\n        return 4\n"
        "    if v == 5:\n        return 5\n    if v == 6:\n        return 6\n    return 0\n")
CC_ONLY = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
           'languages = ["python"]\ncoverage_optional = true\n')
_SUMMARY = {"covered_lines": 1, "num_statements": 1, "missing_lines": 0, "excluded_lines": 0,
            "covered_branches": 0, "num_branches": 0, "missing_branches": 0,
            "num_partial_branches": 0, "percent_covered": 100.0}
_REGION = {"executed_lines": [2], "missing_lines": [], "excluded_lines": [],
           "executed_branches": [], "missing_branches": [], "summary": _SUMMARY}
ARTIFACT = json.dumps({"meta": {"format": 3, "version": "7.16.1", "branch_coverage": True,
                                "show_contexts": False, "timestamp": "2026-09-24T00:00:00"},
                       "files": {"src/a.py": {**_REGION, "classes": {},
                                              "functions": {"f": {**_REGION, "start_line": 1}}}},
                       "totals": _SUMMARY})
FAILING_LANE = ('[[lane]]\nname = "py"\ncommand = "python -c \\"import sys; sys.exit(1)\\""\n'
                'artifact = ".crapkit/cov/py.json"\nparser = "coveragepy"\nscopes = ["src"]\n'
                'container_ok = true\n')


# README "What the verdict line covers": the line when every lane failed.
EVERY_LANE_FAILED = ("**no verdict: `crapkit coverage` exited 5 (every lane failed (1 of 1); "
                     "the lane errors are in the job log); verify did not run.**")


def _laned(lane: str) -> str:
    return CC_ONLY.replace("coverage_optional = true\n", "") + "\n" + lane


def _spec(broken_lane: bool) -> repos.Spec:
    working = repos.lane_toml("py", ".crapkit/cov/py.json", "coveragepy", ["src"],
                              "recorded/py.json")
    first = {"crapkit.toml": _laned(working) if broken_lane else CC_ONLY,
             "src/a.py": "def f(x):\n    return x\n", "recorded/py.json": ARTIFACT}
    second = {"src/late.py": "def late(v):\n" + BODY}
    if broken_lane:
        second["crapkit.toml"] = _laned(FAILING_LANE)
    return repos.Spec(steps=(repos.Commit(files=first, date=A_DATE, message="A"),
                             repos.Commit(files=second, date=B_DATE, message="B")))


def _clone(origin: Path, dest: Path, shallow: bool) -> Path:
    depth = ["--depth", "1"] if shallow else []
    source = origin.resolve().as_uri() if shallow else str(origin)
    repos.git(dest.parent, "clone", "-q", "-c", "core.autocrlf=false", *depth, source, dest.name)
    return dest


def _run(tmp_path: Path, *, shallow=False, broken_lane=False, stale=False, env=None,
         path=None):
    origin = repos.build(_spec(broken_lane), tmp_path / "origin").root
    base_sha = repos.git(origin, "rev-parse", "HEAD~1").strip()
    checkout = _clone(origin, tmp_path / "checkout", shallow)
    if stale:
        (checkout / ".crapkit" / "cov").mkdir(parents=True)
        (checkout / ".crapkit" / "cov" / "py.json").write_text(ARTIFACT, encoding="utf-8")
    event = action_runs.Event(base_sha=base_sha, inputs={"gate": "true"}, date_now=NOW,
                              env=env or {})
    return action_runs.run_action(checkout, event, tmp_path / "runner", path)


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("full"))


def _verdict_line(comment: str) -> str:
    return next((line for line in comment.splitlines() if line.startswith("**")), "")


def test_two_commit_pr_judges_the_new_function(full):
    """R90: verify judges the pull request's diff from A, so it fails on late()
    with the gate's exit 6 instead of passing over an empty diff."""
    assert _verdict_line(full.comment).startswith("**verify failed, exit 6")
    assert full.exit == 6


def test_the_verdict_names_the_readme_rule_and_the_function(full):
    comment = full.comment

    assert _verdict_line(comment) == "**verify failed, exit 6: complexity gate.**"
    assert "- gate: `src/late.py:1` `late( v )` ccn 7, cov 0%, crap 7.0 -> decompose" in comment


def test_shallow_clone_matches_full_depth(full, tmp_path):
    """R91: the depth-1 clone's check fails as the full clone's does, and its
    comment says what was judged instead of "verify passed"."""
    shallow = _run(tmp_path, shallow=True)
    reason = shallow.file("crapkit-base.reason").strip()

    assert reason.startswith("shallow clone does not hold the fork point of ")
    assert _verdict_line(shallow.comment).startswith("**verify judged no changed function:**")
    assert reason in shallow.comment
    assert (shallow.exit != 0, full.exit != 0) == (True, True)
    assert shallow.exit == 1


@pytest.fixture(scope="module")
def broken(tmp_path_factory):
    """A lane the pull request broke, beside an artifact an earlier run left."""
    return _run(tmp_path_factory.mktemp("broken"), broken_lane=True, stale=True)


def test_failed_coverage_stops_verify(broken):
    """R92: coverage exits 5, verify does not run, and the comment gives no verdict."""
    assert _verdict_line(broken.comment).startswith("**no verdict: `crapkit coverage` exited 5")
    assert broken.file("crapkit-verify.json") == ""
    assert broken.exit == 5


def test_the_no_verdict_line_is_the_readme_s(broken):
    assert _verdict_line(broken.comment) == EVERY_LANE_FAILED


# --- the comment's numbers against kit.exact ---------------------------------------------

def _first_line(comment: str) -> str:
    return next(line for line in comment.splitlines() if " functions in " in line)


_SUMMARY_LINE = re.compile(r"^(\d+) functions in (\d+) files, (\d+) over ceiling 6, "
                           r"CRAP load ([\d.]+), grade (\S+)\.$")


def test_the_summary_line_adds_up_the_rows(full):
    """f (ccn 1) and late (ccn 7) in a cc-only scope: CRAP is ccn, so the load is
    8, one of two is over the ceiling, and 1 in 2 is grade F (README grade table).
    The load is compared as a number: docs/agent-json.md gives crap_load as the
    sum rounded to 2 dp, a JSON number, and the comment prints that number."""
    found = _SUMMARY_LINE.match(_first_line(full.comment)).groups()
    load = exact.half_even(Fraction(1) + Fraction(7), 2)

    assert found[:3] == ("2", "2", "1")
    assert Decimal(found[3]) == load
    assert found[4] == exact.grade(1, 2)


def test_the_table_risk_is_ccn_times_the_recency_weight(full):
    """late.py's one commit is the newest of a log with two timestamps."""
    roster = surfaces.Roster([{"path": "src/late.py", "long_name": "late( v )", "start": 1}])
    table = surfaces.from_pr_comment(full.comment, roster)
    weight = exact.churn_weight([B_DATE], A_DATE, B_DATE)

    assert table[("src/late.py", "late")]["worklist.ccn"] == "7"
    assert table[("src/late.py", "late")]["worklist.risk"] == exact.fixed(7 * weight, 1)
    assert table[("src/late.py", "late")]["worklist.remedy"] == "decompose"


# --- the verdict line, one rule per exit code ------------------------------------------------

# README "What the comment looks like": the rule each exit code stands for.
README_RULES = {6: "complexity gate", 7: "ratchet regressions", 8: "new test failures",
                9: "diff-coverage ceiling 4"}
VERIFY = {"ok": False, "run_id": 3, "baseline_run": 2, "changed_files": 1,
          "gate_violations": [], "ratchet_regressions": [], "new_failures": [],
          "diff_uncovered": [], "diff_uncovered_count": 0, "diff_uncovered_max": 4}
COVERAGE = {"functions": 2, "files": 2, "over_target": 1, "crap_load": 8.0, "grade": "F",
            "target": 6}


def _comment(tmp_path: Path, verify: dict, exit_code: int, reason: str | None = None) -> str:
    """comment.py of the crapkit under test, over hand-written payloads."""
    files = {"coverage.json": COVERAGE, "verify.json": verify,
             "worklist.json": {"active": [], "dormant_top": []}}
    for name, payload in files.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "reason").write_text(reason or "", encoding="utf-8")
    argv = [str(printed_runs.python()), str(action_runs.action_path() / "tools/action/comment.py"),
            "--coverage", str(tmp_path / "coverage.json"), "--coverage-exit", "0",
            "--verify", str(tmp_path / "verify.json"), "--verify-exit", str(exit_code),
            "--worklist", str(tmp_path / "worklist.json"), "--top", "5",
            "--out", str(tmp_path / "comment.md"), "--json-out", str(tmp_path / "comment.json")]
    if reason:
        (tmp_path / "sha").write_text("", encoding="utf-8")
        argv += ["--base-sha", str(tmp_path / "sha"), "--base-reason", str(tmp_path / "reason")]
    done = hang_guard.run(argv, cwd=tmp_path, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return (tmp_path / "comment.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("exit_code", sorted(README_RULES))
def test_a_failed_verdict_names_the_readme_rule_for_its_exit(tmp_path, exit_code):
    line = _verdict_line(_comment(tmp_path, VERIFY, exit_code))

    assert line.startswith(f"**verify failed, exit {exit_code}: {README_RULES[exit_code]}.**")


def test_a_pass_reads_passed_and_a_pass_without_a_base_run_says_so(tmp_path):
    passed = {**VERIFY, "ok": True}
    (tmp_path / "plain").mkdir()
    plain = _comment(tmp_path / "plain", passed, 0)
    unbased = _comment(tmp_path, passed, 0, reason="no base commit")

    assert _verdict_line(plain) == "**verify passed.** Run 3 against baseline 2, 1 changed file."
    assert _verdict_line(unbased) == ("**verify judged no changed function:** the base run was "
                                      "not made (no base commit). Run 3 against baseline 2, 1 "
                                      "changed file.")


# --- compatibility with the last release ----------------------------------------------------

@pytest.mark.nightly
def test_the_comment_renders_what_the_last_release_wrote(tmp_path):
    """The Action's steps run the last PyPI release (unpacked on PYTHONPATH, so
    the console script loads it) and this checkout's comment.py renders its
    payloads: the pull request above still reads exit 6 and names late( v )."""
    site = releases.site(releases.last(1)[0], tmp_path / "site")

    released = _run(tmp_path / "run", env={"PYTHONPATH": str(site)},
                    path=Path(__file__).resolve().parents[3])

    assert _verdict_line(released.comment) == "**verify failed, exit 6: complexity gate.**"
    assert "`late( v )` ccn 7, cov 0%, crap 7.0 -> decompose" in released.comment
    assert released.exit == 6
