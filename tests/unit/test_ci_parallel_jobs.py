"""CI runs its long sessions side by side.

Windows ran unit then e2e in one job, and the verdict measured base then
candidate in one job: the two serial chains set the critical path. Each Windows
session and each verdict side is now a job of its own, and a join judges the two
measurements. These read the workflow the way the runner reads it: every matrix
row's command goes through the argument parser of the script it calls.
"""
from collections import defaultdict
import itertools
from pathlib import Path
import re
import runpy
import shlex
from types import SimpleNamespace

import yaml

from test_ci_verdict import ROOT, driver

RUNNER = runpy.run_path(str(ROOT / "tools/testing/run.py"))
CI = driver()


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def matrix_rows(matrix):
    """Every job a matrix expands to: the product of its axes less each row an exclude matches."""
    assert "include" not in matrix, "this expansion handles exclude rows only"
    axes = dict(matrix)
    rules = axes.pop("exclude", [])
    rows = (dict(zip(axes, values)) for values in itertools.product(*axes.values()))
    return [row for row in rows if not _excluded(row, rules)]


def _excluded(row, rules):
    return any(rule.items() <= row.items() for rule in rules)


def rendered(text, row):
    """A step's text with `${{ matrix.<axis> }}` replaced the way the runner fills it."""
    filled = re.sub(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", lambda match: str(row[match.group(1)]), text)
    assert "${{ matrix" not in filled, filled
    return filled


def step(job, key, prefix):
    found = [item for item in job["steps"] if str(item.get(key, "")).startswith(prefix)]
    assert len(found) == 1, (key, prefix, found)
    return found[0]


def arguments(parse, command, script):
    words = shlex.split(command)
    assert words[:2] == ["python", script], words
    return parse(words[2:])


def test_each_windows_session_is_a_job_and_every_platform_runs_each_suite_once():
    job = workflow()["jobs"]["test"]
    command = step(job, "run", "python tools/testing/run.py")["run"]
    sessions = defaultdict(list)
    for row in matrix_rows(job["strategy"]["matrix"]):
        args = arguments(RUNNER["parse_arguments"], rendered(command, row), "tools/testing/run.py")
        sessions[row["os"], row["python"]].append(tuple(args.suite))

    assert sessions, "the test job lost its matrix"
    for (runner, _), selected in sessions.items():
        expected = [(suite,) for suite in RUNNER["SUITES"]] if runner == "windows-latest" else [RUNNER["SUITES"]]
        assert sorted(selected) == sorted(expected), (runner, selected)


def measurement_hand_offs(jobs):
    """Each measurement row's side and the artifact name and path it uploads."""
    job = jobs["verdict-measure"]
    command = step(job, "run", "python tools/testing/ci.py")["run"]
    upload = step(job, "uses", "actions/upload-artifact@")
    assert upload["if"] == "always()", "a failed measurement still hands off what it has"
    assert upload["with"]["include-hidden-files"] is True
    assert upload["with"]["overwrite"] is True, "a rerun replaces its earlier hand-off"
    hand_offs = {}
    for row in matrix_rows(job["strategy"]["matrix"]):
        args = arguments(CI.parse_arguments, rendered(command, row), "tools/testing/ci.py")
        assert args.base == "$BASE_REF" and not args.join
        name, path = (rendered(upload["with"][key], row) for key in ("name", "path"))
        hand_offs[args.measure] = (name, Path(path), args.measured / args.measure)
    return hand_offs


def test_the_verdict_measures_each_side_in_a_job_of_its_own():
    jobs = workflow()["jobs"]
    hand_offs = measurement_hand_offs(jobs)

    assert sorted(hand_offs) == sorted(CI.SIDES)
    assert "needs" not in jobs["verdict-measure"], "measurement starts with the test jobs"
    for side, (name, uploaded, written) in hand_offs.items():
        assert uploaded == written, f"{side} uploads {uploaded} but hands off at {written}"


def test_the_join_reads_both_hand_offs_where_the_measurements_left_them():
    jobs = workflow()["jobs"]
    join = jobs["verdict"]
    args = arguments(CI.parse_arguments, step(join, "run", "python tools/testing/ci.py")["run"],
                     "tools/testing/ci.py")
    downloads = {item["with"]["name"]: Path(item["with"]["path"]) for item in join["steps"]
                 if str(item.get("uses", "")).startswith("actions/download-artifact@")}

    assert join["needs"] == "verdict-measure"
    assert args.join
    assert args.base == "$BASE_REF"
    assert args.measure is None
    assert downloads == {name: args.measured / side
                         for side, (name, _, _) in measurement_hand_offs(jobs).items()}
    retained = step(join, "uses", "actions/upload-artifact@")
    assert retained["if"] == "always()"
    assert Path(retained["with"]["path"]) == args.output
    assert retained["with"]["overwrite"] is True, "a rerun of the join replaces its verdict"


def evaluate(expression, github):
    """GitHub's `a == 'b' && c || d` over one event.

    `||` returns its first truthy operand, else its last; `&&` returns its first
    falsy operand, else its last; `&&` binds tighter.
    """
    value = None
    for alternative in expression.split("||"):
        value = all_of(alternative, github)
        if value:
            return value
    return value


def all_of(text, github):
    value = None
    for part in text.split("&&"):
        value = comparison(part.strip(), github)
        if not value:
            return value
    return value


def comparison(text, github):
    left, equals, right = (part.strip() for part in text.partition("=="))
    return operand(left, github) == operand(right, github) if equals else operand(left, github)


def operand(text, github):
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    assert text.startswith("github."), text
    return getattr(github, text.removeprefix("github."))


def interpolated(text, github):
    return re.sub(r"\$\{\{(.*?)\}\}", lambda match: str(evaluate(match.group(1).strip(), github)), text)


def run_of(event, ref, run_id):
    return SimpleNamespace(workflow="ci", event_name=event, ref=ref, run_id=run_id)


def test_a_pull_request_cancels_its_older_run_and_every_push_to_main_finishes():
    concurrency = workflow()["concurrency"]

    def group(run):
        return interpolated(concurrency["group"], run), evaluate(
            concurrency["cancel-in-progress"].removeprefix("${{").removesuffix("}}").strip(), run)

    first_push, second_push = run_of("pull_request", "refs/pull/7/merge", 101), run_of(
        "pull_request", "refs/pull/7/merge", 102)
    assert group(first_push) == group(second_push)
    assert group(second_push)[1] is True
    assert group(run_of("pull_request", "refs/pull/8/merge", 103))[0] != group(first_push)[0]
    main, next_main = run_of("push", "refs/heads/main", 201), run_of("push", "refs/heads/main", 202)
    assert group(main)[0] != group(next_main)[0], "a queued main run would replace a pending one"
    assert group(main)[1] is False
